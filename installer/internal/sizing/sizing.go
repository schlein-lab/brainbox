package sizing

type Spec struct {
	VRAMMiB    int
	VCPU       int
	RootGiB    int
	DataGiB    int
	HostRAMMiB int
	HostCores  int
}

const (

	minVRAMMiB = 4 * 1024
	maxVRAMMiB = 16 * 1024
	vRAMShare  = 25

	minVCPU = 2
	maxVCPU = 8

	rootGiB    = 8
	minDataGiB = 16
)

func Compute(hostRAMMiB, hostCores int) Spec {
	return Spec{
		VRAMMiB:    vRAM(hostRAMMiB),
		VCPU:       vCPU(hostCores),
		RootGiB:    rootGiB,
		DataGiB:    dataGiB(hostRAMMiB),
		HostRAMMiB: hostRAMMiB,
		HostCores:  hostCores,
	}
}

func vRAM(hostRAMMiB int) int {
	want := hostRAMMiB * vRAMShare / 100
	return clamp(want, minVRAMMiB, maxVRAMMiB)
}

func vCPU(hostCores int) int {
	return clamp(hostCores/2, minVCPU, maxVCPU)
}

func dataGiB(hostRAMMiB int) int {
	want := (vRAM(hostRAMMiB) / 1024) * 4
	if want < minDataGiB {
		return minDataGiB
	}
	return want
}

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
