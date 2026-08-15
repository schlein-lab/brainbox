package detect

import (
	"bufio"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
)

func Run() Report {
	p, osPretty := detectPlatform()
	hv := detectHWVirt()
	mg := detectMgmt(p)
	cap := detectCapacity()
	net := detectNetwork()
	return assemble(p, osPretty, runtime.GOARCH, hv, mg, cap, net)
}

func detectPlatform() (Platform, string) {
	switch runtime.GOOS {
	case "windows":
		return PlatformWindows, "Windows"
	case "darwin":
		return PlatformMacOS, "macOS"
	}

	osRelease := readFile("/etc/os-release")
	pretty := kvFromOSRelease(osRelease, "PRETTY_NAME")
	if pretty == "" {
		pretty = "Linux"
	}
	switch {
	case fileExists("/etc/synoinfo.conf") || fileExists("/etc.defaults/VERSION"):
		return PlatformSynology, pretty
	case fileExists("/etc/config/uLinux.conf") || dirExists("/share/CACHEDEV1_DATA"):
		return PlatformQNAP, pretty
	case strings.Contains(strings.ToLower(osRelease), "truenas"):
		return PlatformTrueNAS, pretty
	case osRelease == "" && runtime.GOOS == "linux":
		return PlatformUnknown, pretty
	}
	return PlatformLinux, pretty
}

func detectHWVirt() HWVirt {
	var hv HWVirt

	if f, err := os.OpenFile("/dev/kvm", os.O_RDONLY, 0); err == nil {
		hv.DevKVM = true
		_ = f.Close()
	} else if fileExists("/dev/kvm") {

		hv.DevKVM = true
	}

	cpuinfo := readFile("/proc/cpuinfo")
	switch {
	case strings.Contains(cpuinfo, "vmx"):
		hv.CPUFlag = "vmx"
	case strings.Contains(cpuinfo, "svm"):
		hv.CPUFlag = "svm"
	}

	hv.Nested = readNested()

	hv.InContainer = inContainer()
	return hv
}

func readNested() bool {
	for _, p := range []string{
		"/sys/module/kvm_intel/parameters/nested",
		"/sys/module/kvm_amd/parameters/nested",
	} {
		v := strings.TrimSpace(readFile(p))
		if v == "Y" || v == "1" {
			return true
		}
	}
	return false
}

func inContainer() bool {
	if fileExists("/.dockerenv") || fileExists("/run/.containerenv") {
		return true
	}
	cg := readFile("/proc/1/cgroup")
	if strings.Contains(cg, "docker") || strings.Contains(cg, "containerd") ||
		strings.Contains(cg, "lxc") || strings.Contains(cg, "kubepods") {
		return true
	}

	if strings.TrimSpace(readFile("/run/systemd/container")) != "" {
		return true
	}
	return false
}

func detectMgmt(p Platform) MgmtLayer {
	var mg MgmtLayer
	mg.QemuPresent = onPath("qemu-system-x86_64") || onPath("qemu-system-aarch64") || onPath("qemu-kvm")
	mg.QemuImgPresent = onPath("qemu-img")
	mg.Libvirt = detectLibvirt()

	mg.SynologyVMM = p == PlatformSynology && dirExists("/var/packages/Virtualization")
	mg.QNAPVS = p == PlatformQNAP && dirExists("/share/Virtualization")
	mg.HyperVWMI = p == PlatformWindows
	return mg
}

func detectLibvirt() bool {
	if !onPath("virsh") {

		return socketExists("/var/run/libvirt/libvirt-sock") ||
			socketExists("/run/libvirt/libvirt-sock") ||
			socketExists("/run/libvirt/virtqemud-sock")
	}

	cmd := exec.Command("virsh", "-r", "-c", "qemu:///system", "uri")
	cmd.Env = append(os.Environ(), "LANG=C", "LC_ALL=C")
	if err := cmd.Run(); err == nil {
		return true
	}

	return socketExists("/run/libvirt/libvirt-sock") ||
		socketExists("/run/libvirt/virtqemud-sock")
}

func detectCapacity() Capacity {
	ram := readMemTotalMiB()
	cores := runtime.NumCPU()
	return Capacity{RAMMiB: ram, Cores: cores, Sizing: sizing.Compute(ram, cores)}
}

func readMemTotalMiB() int {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "MemTotal:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				kb, _ := strconv.Atoi(fields[1])
				return kb / 1024
			}
		}
	}
	return 0
}

func detectNetwork() Network {
	net := Network{mDNSName: "portioneer.local"}
	iface, gw := defaultRoute()
	net.PrimaryIface = iface
	net.DefaultGW = gw
	net.PrimaryIP = ifaceIP(iface)
	net.DHCP = strings.Contains(routeRaw(), "proto dhcp")
	net.BridgePresent = existingBridge()

	net.Bridgeable = net.BridgePresent != "" ||
		(iface != "" && (dirExists("/sys/module/bridge") || onPath("ip")))
	return net
}

func defaultRoute() (iface, gw string) {
	out := routeRaw()
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 5 && fields[0] == "default" {
			for i := 0; i < len(fields)-1; i++ {
				switch fields[i] {
				case "dev":
					iface = fields[i+1]
				case "via":
					gw = fields[i+1]
				}
			}
			return iface, gw
		}
	}
	return "", ""
}

func routeRaw() string {
	if !onPath("ip") {
		return ""
	}
	out, _ := exec.Command("ip", "route").Output()
	return string(out)
}

func ifaceIP(iface string) string {
	if iface == "" || !onPath("ip") {
		return ""
	}
	out, _ := exec.Command("ip", "-o", "-4", "addr", "show", "dev", iface).Output()
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		for i := 0; i < len(fields)-1; i++ {
			if fields[i] == "inet" {
				return strings.SplitN(fields[i+1], "/", 2)[0]
			}
		}
	}
	return ""
}

func existingBridge() string {
	entries, err := os.ReadDir("/sys/class/net")
	if err != nil {
		return ""
	}
	for _, e := range entries {
		name := e.Name()
		if !dirExists(filepath.Join("/sys/class/net", name, "bridge")) {
			continue
		}
		if strings.HasPrefix(name, "virbr") || strings.HasPrefix(name, "docker") {
			continue
		}
		return name
	}
	return ""
}

func readFile(p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		return ""
	}
	return string(b)
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func dirExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func socketExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func onPath(bin string) bool {
	_, err := exec.LookPath(bin)
	return err == nil
}

func kvFromOSRelease(content, key string) string {
	for _, line := range strings.Split(content, "\n") {
		if strings.HasPrefix(line, key+"=") {
			v := strings.TrimPrefix(line, key+"=")
			return strings.Trim(v, "\"")
		}
	}
	return ""
}
