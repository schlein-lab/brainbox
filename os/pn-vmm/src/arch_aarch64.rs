use kvm_bindings::{
    kvm_create_device, kvm_device_attr, kvm_userspace_memory_region, kvm_vcpu_init,
    kvm_device_type_KVM_DEV_TYPE_ARM_VGIC_V2, KVM_ARM_VCPU_PSCI_0_2, KVM_DEV_ARM_VGIC_CTRL_INIT,
    KVM_DEV_ARM_VGIC_GRP_ADDR, KVM_DEV_ARM_VGIC_GRP_CTRL, KVM_VGIC_V2_ADDR_TYPE_CPU,
    KVM_VGIC_V2_ADDR_TYPE_DIST,
};
use kvm_ioctls::{Kvm, VcpuFd, VmFd};
use linux_loader::loader::KernelLoader;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use vm_memory::{Address, Bytes, GuestAddress, GuestMemory, GuestMemoryMmap, GuestMemoryRegion};
use vm_superio::Serial;
use vmm_sys_util::eventfd::EventFd;

use crate::virtio::{self, VirtioBlkMmio};
use crate::vsock::{self, VsockMmio};
use crate::{
    adopt_listener, load_initrd, run_vcpu, spawn_lane, tty_raw, tty_restore, ctrlc_set, EventFdTrigger,
    Lane, RawOut, VcpuCtx,
};

pub const DRAM_START: u64 = 0x8000_0000;
const GICD_BASE: u64 = 0x0800_0000;
const GICD_SIZE: u64 = 0x0001_0000;
const GICC_BASE: u64 = 0x0801_0000;
const GICC_SIZE: u64 = 0x0001_0000;
pub const SERIAL_MMIO_BASE: u64 = 0x4000_0000;
pub const SERIAL_MMIO_SIZE: u64 = 0x0000_0100;
const SERIAL_SPI: u32 = 0;
const MEM_SIZE_DEFAULT: u64 = 1024 << 20;

const GIC_FDT_SPI: u32 = 0;
const GIC_FDT_PPI: u32 = 1;
const IRQ_TYPE_EDGE_RISING: u32 = 1;
const IRQ_TYPE_LEVEL_HIGH: u32 = 4;
const IRQ_TYPE_LEVEL_LOW: u32 = 8;

const KVM_REG_ARM64: u64 = 0x6000_0000_0000_0000;
const KVM_REG_SIZE_U64: u64 = 0x0030_0000_0000_0000;
const KVM_REG_ARM_CORE: u64 = 0x0010_0000;

const CORE_REG_X0: u64 = 0;
const CORE_REG_PC: u64 = 256 / 4;
fn core_reg(byte_off_div4: u64) -> u64 {
    KVM_REG_ARM64 | KVM_REG_SIZE_U64 | KVM_REG_ARM_CORE | byte_off_div4
}

const CMDLINE: &str =
    "console=ttyS0 earlycon=uart8250,mmio,0x40000000 reboot=t panic=1 pci=off";

pub fn boot_kernel(kernel_path: &str, initrd_path: &str, nvcpus: u8) -> Result<(), String> {
    let kvm = Kvm::new().map_err(|e| format!("open /dev/kvm: {e}"))?;
    println!("[pn-vmm] arch=aarch64 KVM API v{} — booting {kernel_path}", kvm.get_api_version());
    let vm = kvm.create_vm().map_err(|e| format!("create_vm: {e}"))?;

    let mem_size: u64 = std::env::var("PN_VMM_MEM_MB")
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .map(|mb| (mb << 20).clamp(128 << 20, 65536 << 20))
        .unwrap_or(MEM_SIZE_DEFAULT);
    let gm: GuestMemoryMmap =
        GuestMemoryMmap::from_ranges(&[(GuestAddress(DRAM_START), mem_size as usize)])
            .map_err(|e| format!("guest mem: {e:?}"))?;
    for (i, r) in gm.iter().enumerate() {
        let region = kvm_userspace_memory_region {
            slot: i as u32,
            flags: 0,
            guest_phys_addr: r.start_addr().raw_value(),
            memory_size: r.len(),
            userspace_addr: r.as_ptr() as u64,
        };
        unsafe { vm.set_user_memory_region(region).map_err(|e| format!("set_mem: {e}"))?; }
    }

    let mut kf = std::fs::File::open(kernel_path).map_err(|e| format!("open kernel: {e}"))?;
    let load = linux_loader::loader::pe::PE::load(&gm, Some(GuestAddress(DRAM_START)), &mut kf, None)
        .map_err(|e| format!("Image load: {e:?}"))?;
    let entry = load.kernel_load.raw_value();
    println!("[pn-vmm] kernel entry = {entry:#x}");

    let initrd = load_initrd(&gm, initrd_path, (DRAM_START + mem_size) as usize)?;

    let nvcpus = nvcpus.clamp(1, 16);
    let mut vcpus: Vec<VcpuFd> = Vec::new();
    for id in 0..nvcpus {
        let v = vm.create_vcpu(id as u64).map_err(|e| format!("create_vcpu {id}: {e}"))?;
        vcpus.push(v);
    }

    let mut kvi = kvm_vcpu_init::default();
    vm.get_preferred_target(&mut kvi).map_err(|e| format!("get_preferred_target: {e}"))?;
    kvi.features[0] |= 1 << KVM_ARM_VCPU_PSCI_0_2;

    for (id, v) in vcpus.iter().enumerate() {
        let mut this = kvi;
        if id != 0 {
            this.features[0] |= 1 << 1;
        }
        v.vcpu_init(&this).map_err(|e| format!("vcpu_init {id}: {e}"))?;
    }

    create_gic_v2(&vm)?;

    let mut cmdline_str = String::from(CMDLINE);
    let mut mmio_nodes: Vec<(u64, u32)> = Vec::new();

    let mut ro_set: std::collections::HashSet<usize> = match std::env::var("PN_VMM_BLK_RO") {
        Ok(s) => s.split(',').filter_map(|x| x.trim().parse::<usize>().ok()).collect(),
        Err(_) => std::iter::once(0usize).collect(),
    };
    ro_set.insert(0);
    let mut blks: Vec<VirtioBlkMmio> = Vec::new();
    if let Ok(spec) = std::env::var("PN_VMM_BLK") {
        for (i, path) in spec.split(',').filter(|p| !p.is_empty()).enumerate() {
            let base = virtio::BLK_MMIO_BASE + (i as u64) * virtio::BLK_MMIO_SIZE;
            let gsi = virtio::BLK_GSI0 + i as u32;
            let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("blk eventfd: {e}"))?;
            vm.register_irqfd(&irq, gsi).map_err(|e| format!("blk irqfd: {e}"))?;
            let read_only = ro_set.contains(&i);
            let dev = VirtioBlkMmio::new(path, irq, base, read_only)?;
            println!("[pn-vmm] virtio-blk vd{} @ {base:#x} spi {gsi} — {} sectors, backing {path} [{}]",
                     (b'a' + i as u8) as char, dev.capacity_sectors(),
                     if read_only { "RO" } else { "RW" });
            mmio_nodes.push((base, gsi));
            blks.push(dev);
        }
    }

    let blk_top_gsi = virtio::BLK_GSI0 + blks.len() as u32;
    let vsock_gsi = std::cmp::max(vsock::VSOCK_GSI, blk_top_gsi);
    let rng_gsi = std::cmp::max(virtio::RNG_GSI, vsock_gsi + 1);

    let mut vsock_dev: Option<Arc<Mutex<VsockMmio>>> = None;
    if let Ok(cids) = std::env::var("PN_VMM_VSOCK") {
        if let Ok(cid) = cids.trim().parse::<u64>() {
            if cid >= 3 {
                let base = vsock::VSOCK_MMIO_BASE;
                let gsi = vsock_gsi;
                let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("vsock eventfd: {e}"))?;
                vm.register_irqfd(&irq, gsi).map_err(|e| format!("vsock irqfd: {e}"))?;
                mmio_nodes.push((base, gsi));
                let dev = Arc::new(Mutex::new(VsockMmio::new(cid, irq, base)));
                wire_vsock_lanes(&dev, &gm, &mut cmdline_str)?;
                println!("[pn-vmm] virtio-vsock @ {base:#x} spi {gsi} — guest CID {cid}");
                vsock_dev = Some(dev);
            }
        }
    }

    let mut rng: Option<virtio::VirtioRngMmio> = None;
    if std::env::var("PN_VMM_RNG").map(|v| { let v = v.trim(); !(v == "0" || v == "off") }).unwrap_or(true) {
        let base = virtio::RNG_MMIO_BASE;
        let gsi = rng_gsi;
        let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("rng eventfd: {e}"))?;
        vm.register_irqfd(&irq, gsi).map_err(|e| format!("rng irqfd: {e}"))?;
        mmio_nodes.push((base, gsi));
        println!("[pn-vmm] virtio-rng @ {base:#x} spi {gsi}");
        rng = Some(virtio::VirtioRngMmio::new(irq, base)?);
    }

    {
        let mut seed = [0u8; 32];
        if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
            if f.read_exact(&mut seed).is_ok() {
                let hex: String = seed.iter().map(|b| format!("{b:02x}")).collect();
                cmdline_str.push_str(&format!(" pn_rngseed={hex}"));
            }
        }
    }
    if let Ok(extra) = std::env::var("PN_VMM_EXTRA_CMDLINE") {
        let extra = extra.trim();
        if !extra.is_empty() {
            cmdline_str.push(' ');
            cmdline_str.push_str(extra);
        }
    }

    let fdt = build_fdt(mem_size, nvcpus, &cmdline_str, &mmio_nodes, &initrd)
        .map_err(|e| format!("build fdt: {e}"))?;

    let fdt_addr = fdt_placement(mem_size, &initrd, fdt.len() as u64);
    gm.write_slice(&fdt, GuestAddress(fdt_addr)).map_err(|e| format!("write fdt: {e:?}"))?;
    println!("[pn-vmm] FDT @ {fdt_addr:#x} ({} bytes)", fdt.len());

    vcpus[0].set_one_reg(core_reg(CORE_REG_PC), &entry.to_le_bytes())
        .map_err(|e| format!("set pc: {e}"))?;
    vcpus[0].set_one_reg(core_reg(CORE_REG_X0), &fdt_addr.to_le_bytes())
        .map_err(|e| format!("set x0=fdt: {e}"))?;

    let intr_evt = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("serial eventfd: {e}"))?;
    vm.register_irqfd(&intr_evt, SERIAL_SPI).map_err(|e| format!("serial irqfd: {e}"))?;
    let trig = EventFdTrigger(intr_evt.try_clone().map_err(|e| format!("evt clone: {e}"))?);
    let serial = Arc::new(Mutex::new(Serial::new(trig, RawOut)));

    let stop = Arc::new(AtomicBool::new(false));
    {
        let s = stop.clone();
        let _ = ctrlc_set(move || s.store(true, Ordering::SeqCst));
    }

    let tty_orig = tty_raw();
    {
        let ser = serial.clone();
        let s = stop.clone();
        std::thread::spawn(move || {
            let mut buf = [0u8; 64];
            loop {
                let n = unsafe { libc::read(0, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
                if n <= 0 {
                    break;
                }
                let n = n as usize;
                if buf[..n].contains(&0x1d) {
                    s.store(true, Ordering::SeqCst);
                    break;
                }
                if let Ok(mut g) = ser.lock() {
                    let _ = g.enqueue_raw_bytes(&buf[..n]);
                }
            }
        });
    }
    println!("[pn-vmm] --- guest serial (console=ttyS0 @ mmio 0x40000000), Ctrl-] to detach ---");
    let _ = std::io::stdout().flush();

    let blks = Arc::new(Mutex::new(blks));
    let rng = Arc::new(Mutex::new(rng));
    let exit_rc = Arc::new(AtomicI32::new(0));
    let vcpu_tids = Arc::new(Mutex::new(vec![unsafe { libc::pthread_self() }]));
    let ctx = VcpuCtx {
        gm: gm.clone(),
        serial: serial.clone(),
        blks,
        rng,
        vsock: vsock_dev.clone(),
        stop: stop.clone(),
        exit_rc: exit_rc.clone(),
        vcpu_tids: vcpu_tids.clone(),
    };

    let mut vcpu_iter = vcpus.into_iter();
    let vcpu0 = vcpu_iter.next().unwrap();
    let ap_vcpus: Vec<(u8, VcpuFd)> = vcpu_iter.enumerate().map(|(i, v)| ((i + 1) as u8, v)).collect();
    let mut ap_handles = Vec::new();
    if !ap_vcpus.is_empty() {
        unsafe {
            let mut sa: libc::sigaction = std::mem::zeroed();
            sa.sa_sigaction = crate::vcpu_kick_noop as usize;
            sa.sa_flags = 0;
            libc::sigemptyset(&mut sa.sa_mask);
            libc::sigaction(libc::SIGUSR1, &sa, std::ptr::null_mut());
        }
        use std::os::unix::thread::JoinHandleExt;
        let n_aps = ap_vcpus.len();
        let ready = Arc::new(AtomicUsize::new(0));
        for (id, v) in ap_vcpus {
            let ctx = ctx.clone();
            let rdy = ready.clone();
            let h = std::thread::spawn(move || {
                rdy.fetch_add(1, Ordering::SeqCst);
                run_vcpu(v, id, ctx, None);
            });
            vcpu_tids.lock().unwrap().push(h.as_pthread_t());
            ap_handles.push(h);
        }
        while ready.load(Ordering::SeqCst) < n_aps {
            std::thread::yield_now();
        }
        println!("[pn-vmm] SMP: {nvcpus} vcpus (BSP + {n_aps} APs, PSCI-started by the guest)");
    }

    if let Err(e) = crate::seccomp_install() {
        eprintln!("[pn-vmm] FATAL: {e} — refusing to run a tenant without the seccomp sandbox");
        tty_restore(&tty_orig);
        std::process::exit(1);
    }

    let rc = run_vcpu(vcpu0, 0, ctx, None);
    tty_restore(&tty_orig);
    let _ = std::io::stdout().flush();
    let rc = rc.max(exit_rc.load(Ordering::SeqCst));
    if rc != 0 {
        std::process::exit(rc);
    }
    Ok(())
}

fn wire_vsock_lanes(
    dev: &Arc<Mutex<VsockMmio>>,
    gm: &GuestMemoryMmap,
    cmdline_str: &mut String,
) -> Result<(), String> {
    let connect = |p: &str| std::os::unix::net::UnixStream::connect(p);

    if let Ok(p) = std::env::var("PN_VMM_VSOCK_SEAT") {
        if !p.is_empty() {
            match connect(&p) {
                Ok(s) => {
                    let reader = s.try_clone().map_err(|e| format!("seat clone: {e}"))?;
                    dev.lock().unwrap().set_seat(s);
                    spawn_lane(dev.clone(), gm.clone(), Lane::Seat, reader,
                               adopt_listener("PN_VMM_VSOCK_SEAT_ADOPT"),
                               std::env::var("PN_VMM_ADOPT_TOKEN").unwrap_or_default().into_bytes());
                    cmdline_str.push_str(" pn_seat=1");
                }
                Err(e) => eprintln!("[pn-vmm] seat {p}: {e} (no seat)"),
            }
        }
    }

    for (env, lane, adopt) in [
        ("PN_VMM_VSOCK_LLM", Lane::Llm, false),
        ("PN_VMM_VSOCK_RFB", Lane::Rfb, false),
        ("PN_VMM_VSOCK_NET", Lane::Net, false),
        ("PN_VMM_VSOCK_TERM", Lane::Term, true),
        ("PN_VMM_VSOCK_ACT", Lane::Act, false),
        ("PN_VMM_VSOCK_GUI", Lane::Gui, false),
    ] {
        if let Ok(p) = std::env::var(env) {
            if p.is_empty() {
                continue;
            }
            match connect(&p) {
                Ok(s) => {
                    let reader = match s.try_clone() {
                        Ok(r) => r,
                        Err(e) => { eprintln!("[pn-vmm] {env} clone: {e}"); continue; }
                    };
                    lane.set(&mut dev.lock().unwrap(), s);
                    let (lst, tok) = if adopt {
                        (adopt_listener("PN_VMM_VSOCK_TERM_ADOPT"),
                         std::env::var("PN_VMM_ADOPT_TOKEN").unwrap_or_default().into_bytes())
                    } else {
                        (None, Vec::new())
                    };
                    spawn_lane(dev.clone(), gm.clone(), lane, reader, lst, tok);
                    println!("[pn-vmm] vsock lane {env} -> {p}");
                }
                Err(e) => println!("[pn-vmm] {env} {p}: {e} (lane off)"),
            }
        }
    }
    Ok(())
}

fn create_gic_v2(vm: &VmFd) -> Result<(), String> {
    let mut gic = kvm_create_device {
        type_: kvm_device_type_KVM_DEV_TYPE_ARM_VGIC_V2,
        fd: 0,
        flags: 0,
    };
    let dev = vm.create_device(&mut gic).map_err(|e| format!("create vGICv2: {e}"))?;

    let set_addr = |group: u32, ty: u64, addr: u64| -> Result<(), String> {
        let attr = kvm_device_attr {
            group,
            attr: ty,
            addr: &addr as *const u64 as u64,
            flags: 0,
        };
        dev.set_device_attr(&attr).map_err(|e| format!("vGIC set addr grp {group}: {e}"))
    };
    set_addr(KVM_DEV_ARM_VGIC_GRP_ADDR, KVM_VGIC_V2_ADDR_TYPE_DIST as u64, GICD_BASE)?;
    set_addr(KVM_DEV_ARM_VGIC_GRP_ADDR, KVM_VGIC_V2_ADDR_TYPE_CPU as u64, GICC_BASE)?;

    let init = kvm_device_attr {
        group: KVM_DEV_ARM_VGIC_GRP_CTRL,
        attr: KVM_DEV_ARM_VGIC_CTRL_INIT as u64,
        addr: 0,
        flags: 0,
    };
    dev.set_device_attr(&init).map_err(|e| format!("vGIC CTRL_INIT: {e}"))?;
    println!("[pn-vmm] vGICv2: dist @ {GICD_BASE:#x}, cpu @ {GICC_BASE:#x}");
    Ok(())
}

fn fdt_placement(mem_size: u64, initrd: &Option<(u64, u64)>, fdt_len: u64) -> u64 {
    let top = match initrd {
        Some((addr, _)) => *addr,
        None => DRAM_START + mem_size,
    };
    (top.saturating_sub(fdt_len).saturating_sub(0x20_0000)) & !0x1f_ffff
}

fn build_fdt(
    mem_size: u64,
    nvcpus: u8,
    cmdline: &str,
    mmio_nodes: &[(u64, u32)],
    initrd: &Option<(u64, u64)>,
) -> Result<Vec<u8>, vm_fdt::Error> {
    use vm_fdt::FdtWriter;
    const PHANDLE_GIC: u32 = 1;

    let mut fdt = FdtWriter::new()?;
    let root = fdt.begin_node("")?;
    fdt.property_string("compatible", "linux,dummy-virt")?;
    fdt.property_u32("#address-cells", 2)?;
    fdt.property_u32("#size-cells", 2)?;
    fdt.property_u32("interrupt-parent", PHANDLE_GIC)?;

    let chosen = fdt.begin_node("chosen")?;
    fdt.property_string("bootargs", cmdline)?;
    fdt.property_string("stdout-path", "/uart@40000000")?;
    if let Some((addr, size)) = initrd {
        fdt.property_u64("linux,initrd-start", *addr)?;
        fdt.property_u64("linux,initrd-end", *addr + *size)?;
    }
    fdt.end_node(chosen)?;

    let memory = fdt.begin_node(&format!("memory@{:x}", DRAM_START))?;
    fdt.property_string("device_type", "memory")?;
    fdt.property_array_u64("reg", &[DRAM_START, mem_size])?;
    fdt.end_node(memory)?;

    let cpus = fdt.begin_node("cpus")?;
    fdt.property_u32("#address-cells", 1)?;
    fdt.property_u32("#size-cells", 0)?;
    for id in 0..nvcpus {
        let cpu = fdt.begin_node(&format!("cpu@{id}"))?;
        fdt.property_string("device_type", "cpu")?;
        fdt.property_string("compatible", "arm,arm-v8")?;
        fdt.property_u32("reg", id as u32)?;
        if nvcpus > 1 {
            fdt.property_string("enable-method", "psci")?;
        }
        fdt.end_node(cpu)?;
    }
    fdt.end_node(cpus)?;

    let psci = fdt.begin_node("psci")?;
    fdt.property_string_list(
        "compatible",
        vec!["arm,psci-1.0".into(), "arm,psci-0.2".into()],
    )?;
    fdt.property_string("method", "hvc")?;
    fdt.end_node(psci)?;

    let cpu_mask = ((1u32 << nvcpus) - 1) << 8;
    let timer = fdt.begin_node("timer")?;
    fdt.property_string("compatible", "arm,armv8-timer")?;
    fdt.property_array_u32(
        "interrupts",
        &[
            GIC_FDT_PPI, 13, cpu_mask | IRQ_TYPE_LEVEL_LOW,
            GIC_FDT_PPI, 14, cpu_mask | IRQ_TYPE_LEVEL_LOW,
            GIC_FDT_PPI, 11, cpu_mask | IRQ_TYPE_LEVEL_LOW,
            GIC_FDT_PPI, 10, cpu_mask | IRQ_TYPE_LEVEL_LOW,
        ],
    )?;
    fdt.property_null("always-on")?;
    fdt.end_node(timer)?;

    let gic = fdt.begin_node(&format!("intc@{:x}", GICD_BASE))?;
    fdt.property_string("compatible", "arm,cortex-a15-gic")?;
    fdt.property_u32("#interrupt-cells", 3)?;
    fdt.property_null("interrupt-controller")?;
    fdt.property_array_u64("reg", &[GICD_BASE, GICD_SIZE, GICC_BASE, GICC_SIZE])?;
    fdt.property_u32("phandle", PHANDLE_GIC)?;
    fdt.end_node(gic)?;

    let uart = fdt.begin_node(&format!("uart@{:x}", SERIAL_MMIO_BASE))?;
    fdt.property_string("compatible", "ns16550a")?;
    fdt.property_array_u64("reg", &[SERIAL_MMIO_BASE, SERIAL_MMIO_SIZE])?;

    fdt.property_array_u32("interrupts", &[GIC_FDT_SPI, SERIAL_SPI, IRQ_TYPE_EDGE_RISING])?;
    fdt.property_u32("clock-frequency", 1_843_200)?;
    fdt.end_node(uart)?;

    for (base, gsi) in mmio_nodes {
        let n = fdt.begin_node(&format!("virtio_mmio@{:x}", base))?;
        fdt.property_string("compatible", "virtio,mmio")?;
        fdt.property_array_u64("reg", &[*base, 0x200])?;
        fdt.property_array_u32("interrupts", &[GIC_FDT_SPI, *gsi, IRQ_TYPE_EDGE_RISING])?;
        fdt.end_node(n)?;
    }

    fdt.end_node(root)?;
    fdt.finish()
}
