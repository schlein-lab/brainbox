package sizing

import "testing"

func TestVRAMClamp(t *testing.T) {
	cases := []struct {
		name        string
		hostRAMMiB  int
		wantVRAMMiB int
	}{
		{"tiny host floors to 4G", 2048, 4 * 1024},
		{"8G host → 25%=2G floors to 4G", 8 * 1024, 4 * 1024},
		{"24G host → 25%=6G", 24 * 1024, 6 * 1024},
		{"64G host → 25%=16G", 64 * 1024, 16 * 1024},
		{"256G host caps at 16G", 256 * 1024, 16 * 1024},
		{"the dev host ~7939MiB → 25%≈1984 floors to 4G", 7939, 4 * 1024},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := vRAM(c.hostRAMMiB); got != c.wantVRAMMiB {
				t.Fatalf("vRAM(%d)=%d, want %d", c.hostRAMMiB, got, c.wantVRAMMiB)
			}
		})
	}
}

func TestVCPUClamp(t *testing.T) {
	cases := []struct {
		cores, want int
	}{
		{1, 2},
		{2, 2},
		{4, 2},
		{6, 3},
		{8, 4},
		{16, 8},
		{32, 8},
	}
	for _, c := range cases {
		if got := vCPU(c.cores); got != c.want {
			t.Fatalf("vCPU(%d)=%d, want %d", c.cores, got, c.want)
		}
	}
}

func TestComputeVMDev(t *testing.T) {

	s := Compute(7939, 6)
	if s.VRAMMiB != 4096 {
		t.Errorf("VRAMMiB=%d want 4096", s.VRAMMiB)
	}
	if s.VCPU != 3 {
		t.Errorf("VCPU=%d want 3", s.VCPU)
	}
	if s.DataGiB < minDataGiB {
		t.Errorf("DataGiB=%d below floor %d", s.DataGiB, minDataGiB)
	}
	if s.HostRAMMiB != 7939 || s.HostCores != 6 {
		t.Errorf("host echo wrong: %+v", s)
	}
}

func TestDataDiskFloor(t *testing.T) {
	if d := dataGiB(2048); d < minDataGiB {
		t.Fatalf("data disk %dG below floor", d)
	}
}
