package detect

import "github.com/schlein-lab/brainarbeit/installer/internal/sizing"

func mkCap(ramMiB, cores int) Capacity {
	return Capacity{RAMMiB: ramMiB, Cores: cores, Sizing: sizing.Compute(ramMiB, cores)}
}
