package detect

type HWVirtJSON struct {
	DevKVM      bool   `json:"dev_kvm"`
	CPUFlag     string `json:"cpu_flag"`
	Nested      bool   `json:"nested_virt"`
	InContainer bool   `json:"in_container"`
	HyperV      bool   `json:"hyper_v"`
}

type MgmtJSON struct {
	Libvirt        bool    `json:"libvirt"`
	QemuPresent    bool    `json:"qemu"`
	QemuImgPresent bool    `json:"qemu_img"`
	SynologyVMM    bool    `json:"synology_vmm"`
	QNAPVS         bool    `json:"qnap_vs"`
	HyperVWMI      bool    `json:"hyperv_wmi"`
	Backend        Backend `json:"backend"`
}

type CapJSON struct {
	RAMMiB  int `json:"host_ram_mib"`
	Cores   int `json:"host_cores"`
	VRAMMiB int `json:"vm_vram_mib"`
	VCPU    int `json:"vm_vcpu"`
	RootGiB int `json:"vm_root_gib"`
	DataGiB int `json:"vm_data_gib"`
}

type NetJSON struct {
	PrimaryIface  string `json:"primary_iface"`
	PrimaryIP     string `json:"primary_ip"`
	DefaultGW     string `json:"default_gw"`
	BridgePresent string `json:"bridge_present"`
	Bridgeable    bool   `json:"bridgeable"`
	DHCP          bool   `json:"dhcp"`
	MDNSName      string `json:"mdns_name"`
}

func assemble(p Platform, osPretty, arch string, hv HWVirt, mg MgmtLayer, cap Capacity, net Network) Report {
	v, backend, reasons, blockers, warnings := Decide(p, hv, mg, net)
	mg.Backend = backend
	return Report{
		Platform: p,
		OSPretty: osPretty,
		Arch:     arch,
		HWVirt: HWVirtJSON{
			DevKVM: hv.DevKVM, CPUFlag: hv.CPUFlag, Nested: hv.Nested,
			InContainer: hv.InContainer, HyperV: hv.HyperV,
		},
		Mgmt: MgmtJSON{
			Libvirt: mg.Libvirt, QemuPresent: mg.QemuPresent, QemuImgPresent: mg.QemuImgPresent,
			SynologyVMM: mg.SynologyVMM, QNAPVS: mg.QNAPVS, HyperVWMI: mg.HyperVWMI, Backend: backend,
		},
		Capacity: CapJSON{
			RAMMiB: cap.RAMMiB, Cores: cap.Cores,
			VRAMMiB: cap.Sizing.VRAMMiB, VCPU: cap.Sizing.VCPU,
			RootGiB: cap.Sizing.RootGiB, DataGiB: cap.Sizing.DataGiB,
		},
		Network: NetJSON{
			PrimaryIface: net.PrimaryIface, PrimaryIP: net.PrimaryIP, DefaultGW: net.DefaultGW,
			BridgePresent: net.BridgePresent, Bridgeable: net.Bridgeable, DHCP: net.DHCP,
			MDNSName: net.mDNSName,
		},
		Verdict:  v,
		Backend:  backend,
		Reasons:  reasons,
		Blockers: blockers,
		Warnings: warnings,
	}
}
