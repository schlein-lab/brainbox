#[cfg(target_arch = "x86_64")]
use kvm_bindings::{
    kvm_msr_entry, kvm_pit_config, kvm_segment, kvm_userspace_memory_region, Msrs, KVM_MAX_CPUID_ENTRIES,
};
#[cfg(target_arch = "x86_64")]
use kvm_ioctls::Kvm;
use kvm_ioctls::{VcpuExit, VcpuFd};
#[cfg(target_arch = "x86_64")]
use linux_loader::loader::KernelLoader;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
#[cfg(target_arch = "x86_64")]
use std::sync::atomic::AtomicUsize;
use std::sync::{Arc, Mutex};
use vm_memory::{Bytes, GuestAddress, GuestMemoryMmap};

#[cfg(target_arch = "x86_64")]
use vm_memory::{Address, GuestMemory, GuestMemoryRegion};
use vm_superio::serial::NoEvents;
use vm_superio::{Serial, Trigger};
use vmm_sys_util::eventfd::EventFd;

mod virtio;
#[cfg(target_arch = "x86_64")]
use virtio::VirtioBlkMmio;
mod vsock;
use vsock::VsockMmio;
#[cfg(target_arch = "x86_64")]
mod mptable;
mod jail;
mod seccomp;

#[cfg(target_arch = "aarch64")]
mod arch_aarch64;

#[cfg(target_arch = "x86_64")]
mod x86_layout {
    pub const MEM_SIZE: usize = 1024 << 20;

    pub const HIGHMEM_START: u64 = 0x0010_0000;
    pub const LOW_RAM_TOP: u64 = 0xd000_0000;

    pub const HIGH_RAM_START: u64 = 0x1_0000_0000;
    pub const ZERO_PAGE_START: u64 = 0x7000;
    pub const CMDLINE_START: u64 = 0x2_0000;
    pub const BOOT_GDT: u64 = 0x500;
    pub const BOOT_PML4: u64 = 0x9000;
    pub const BOOT_PDPT: u64 = 0xa000;
    pub const BOOT_PD: u64 = 0xb000;
    pub const COM1_IRQ: u32 = 4;
    pub const CMDLINE: &str = "console=ttyS0 reboot=t panic=1 pci=off acpi=off nomodeset i8042.noaux i8042.nomux i8042.dumbkbd";
}
#[cfg(target_arch = "x86_64")]
use x86_layout::*;

fn main() {

    if let Err(e) = jail::apply() {
        eprintln!("[pn-vmm] jail FATAL (fail-closed; PN_VMM_JAIL_STRICT=0 downgrades this to a warning): {e}");
        std::process::exit(1);
    }

    let mut nvcpus: u8 = std::env::var("PN_VMM_VCPUS")
        .ok()
        .and_then(|s| s.trim().parse::<u8>().ok())
        .unwrap_or(1);
    let mut pos: Vec<String> = Vec::new();
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == "--selftest" {
            #[cfg(target_arch = "x86_64")]
            stage1_selftest();
            #[cfg(not(target_arch = "x86_64"))]
            eprintln!("[pn-vmm] --selftest is x86_64-only (the 3-instruction PIO guest)");
            return;
        } else if a == "--vcpus" {
            nvcpus = match args.next().and_then(|s| s.trim().parse::<u8>().ok()) {
                Some(n) => n,
                None => {
                    eprintln!("[pn-vmm] --vcpus needs a count (1..16)");
                    std::process::exit(2);
                }
            };
        } else if let Some(v) = a.strip_prefix("--vcpus=") {
            nvcpus = match v.trim().parse::<u8>() {
                Ok(n) => n,
                Err(_) => {
                    eprintln!("[pn-vmm] --vcpus needs a count (1..16)");
                    std::process::exit(2);
                }
            };
        } else {
            pos.push(a);
        }
    }
    let nvcpus = nvcpus.clamp(1, 16);
    let mut pos = pos.into_iter();

    #[cfg(target_arch = "x86_64")]
    let default_kernel = "kernel/vmlinux.bin";
    #[cfg(target_arch = "aarch64")]
    let default_kernel = "kernel/Image";
    let kernel = pos.next().unwrap_or_else(|| default_kernel.to_string());
    let initrd = pos.next().unwrap_or_else(|| "kernel/initramfs.cpio".to_string());

    #[cfg(target_arch = "x86_64")]
    let r = boot_kernel(&kernel, &initrd, nvcpus);
    #[cfg(target_arch = "aarch64")]
    let r = arch_aarch64::boot_kernel(&kernel, &initrd, nvcpus);
    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    let r: Result<(), String> = Err("pn-vmm supports x86_64 and aarch64 only".to_string());

    match r {
        Ok(()) => {}
        Err(e) => {
            eprintln!("[pn-vmm] boot error: {e}");
            std::process::exit(1);
        }
    }
}

#[cfg(target_arch = "aarch64")]
pub(crate) fn seccomp_install() -> Result<(), String> {
    seccomp::install()
}

#[derive(Clone, Copy)]
pub(crate) enum Lane {
    Seat,
    Llm,
    Rfb,
    Net,
    Term,
    Act,
    Gui,
}

impl Lane {
    fn name(self) -> &'static str {
        match self {
            Lane::Seat => "seat",
            Lane::Llm => "LLM",
            Lane::Rfb => "RFB",
            Lane::Net => "NET",
            Lane::Term => "TERM",
            Lane::Act => "ACT",
            Lane::Gui => "GUI",
        }
    }

    pub(crate) fn set(self, g: &mut VsockMmio, s: std::os::unix::net::UnixStream) {
        match self {
            Lane::Seat => g.set_seat(s),
            Lane::Llm => g.set_llm(s),
            Lane::Rfb => g.set_rfb(s),
            Lane::Net => g.set_net(s),
            Lane::Term => g.set_term(s),
            Lane::Act => g.set_act(s),
            Lane::Gui => g.set_gui(s),
        }
    }

    fn deliver(self, g: &mut VsockMmio, gm: &GuestMemoryMmap, data: &[u8]) {
        match self {
            Lane::Seat => g.deliver_rx(gm, data),
            Lane::Llm => g.deliver_rx_llm(gm, data),
            Lane::Rfb => g.deliver_rx_rfb(gm, data),
            Lane::Net => g.deliver_rx_net(gm, data),
            Lane::Term => g.deliver_rx_term(gm, data),
            Lane::Act => g.deliver_rx_act(gm, data),
            Lane::Gui => g.deliver_rx_gui(gm, data),
        }
    }
}

pub(crate) fn adopt_listener(path_env: &str) -> Option<std::os::unix::net::UnixListener> {
    use std::os::unix::fs::PermissionsExt;
    let path = match std::env::var(path_env) {
        Ok(p) if !p.is_empty() => p,
        _ => return None,
    };
    if std::env::var("PN_VMM_ADOPT_TOKEN").map(|t| t.is_empty()).unwrap_or(true) {
        eprintln!("[pn-vmm] {path_env} set but PN_VMM_ADOPT_TOKEN empty — adoption DISABLED (fail-closed)");
        return None;
    }
    let _ = std::fs::remove_file(&path);
    match std::os::unix::net::UnixListener::bind(&path) {
        Ok(l) => {

            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
            eprintln!("[pn-vmm] adoption listener bound: {path}");
            Some(l)
        }
        Err(e) => {
            eprintln!("[pn-vmm] adoption listener bind {path}: {e} — adoption DISABLED");
            None
        }
    }
}

fn authenticate(s: &mut std::os::unix::net::UnixStream, token: &[u8]) -> Option<Vec<u8>> {
    use std::os::unix::io::AsRawFd;
    let fd = s.as_raw_fd();
    let mut buf: Vec<u8> = Vec::with_capacity(256);
    let mut chunk = [0u8; 256];
    let step_ms = 500i32;
    let deadline_ms = 3000i32;
    let mut spent = 0i32;
    loop {
        if buf.len() > 512 {
            return None;
        }
        let mut pfd = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
        let r = unsafe { libc::poll(&mut pfd, 1, step_ms) };
        if r < 0 {
            return None;
        }
        if r == 0 {
            spent += step_ms;
            if spent >= deadline_ms {
                return None;
            }
            continue;
        }
        let n = match s.read(&mut chunk) {
            Ok(0) => return None,
            Ok(n) => n,
            Err(_) => return None,
        };
        buf.extend_from_slice(&chunk[..n]);
        if let Some(nl) = buf.iter().position(|&b| b == b'\n') {
            let line = &buf[..nl];
            let ok = line.len() == token.len()
                && line.iter().zip(token).fold(0u8, |a, (x, y)| a | (x ^ y)) == 0;
            if !ok {
                return None;
            }
            let _ = s.write_all(b"PNADOPTOK\n");
            let _ = s.flush();
            return Some(buf[nl + 1..].to_vec());
        }
    }
}

pub(crate) fn spawn_lane(
    dev: Arc<Mutex<VsockMmio>>,
    gm: GuestMemoryMmap,
    lane: Lane,
    initial_reader: std::os::unix::net::UnixStream,
    adopt: Option<std::os::unix::net::UnixListener>,
    token: Vec<u8>,
) {
    std::thread::spawn(move || {
        let mut rd = initial_reader;
        let mut buf = [0u8; 4096];
        loop {

            loop {
                match rd.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        if let Ok(mut g) = dev.lock() {

                            lane.deliver(&mut *g, &gm, &buf[..n]);
                        }
                    }
                }
            }
            let lst = match &adopt {
                Some(l) => l,
                None => break,
            };

            loop {
                let mut s = match lst.accept() {
                    Ok((s, _)) => s,
                    Err(_) => return,
                };
                let remainder = match authenticate(&mut s, &token) {
                    Some(r) => r,
                    None => continue,
                };
                let newrd = match s.try_clone() {
                    Ok(r) => r,
                    Err(e) => {
                        eprintln!("[pn-vmm] {} adopt try_clone: {e} — keep accepting", lane.name());
                        continue;
                    }
                };
                if let Ok(mut g) = dev.lock() {
                    lane.set(&mut *g, s);
                    if !remainder.is_empty() {
                        lane.deliver(&mut *g, &gm, &remainder);
                    }
                }
                rd = newrd;
                eprintln!("[pn-vmm] {} lane re-adopted (portal reconnected)", lane.name());
                break;
            }
        }
    });
}

#[cfg(target_arch = "x86_64")]
fn boot_kernel(kernel_path: &str, initrd_path: &str, nvcpus: u8) -> Result<(), String> {
    let kvm = Kvm::new().map_err(|e| format!("open /dev/kvm: {e}"))?;
    println!("[pn-vmm] arch=x86_64 KVM API v{} — booting {kernel_path}", kvm.get_api_version());
    let vm = kvm.create_vm().map_err(|e| format!("create_vm: {e}"))?;

    let mem_size: usize = std::env::var("PN_VMM_MEM_MB")
        .ok()
        .and_then(|s| s.trim().parse::<usize>().ok())
        .map(|mb| (mb << 20).clamp(128 << 20, 65536 << 20))
        .unwrap_or(MEM_SIZE);
    let low_size: usize = mem_size.min(LOW_RAM_TOP as usize);
    let ranges: Vec<(GuestAddress, usize)> = if mem_size <= LOW_RAM_TOP as usize {
        vec![(GuestAddress(0), mem_size)]
    } else {
        vec![(GuestAddress(0), low_size),
             (GuestAddress(HIGH_RAM_START), mem_size - low_size)]
    };
    let gm: GuestMemoryMmap = GuestMemoryMmap::from_ranges(&ranges)
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

    vm.set_tss_address(0xfffb_d000).map_err(|e| format!("tss: {e}"))?;
    vm.create_irq_chip().map_err(|e| format!("irqchip: {e}"))?;
    vm.create_pit2(kvm_pit_config::default()).map_err(|e| format!("pit: {e}"))?;

    let mut kf = std::fs::File::open(kernel_path).map_err(|e| format!("open kernel: {e}"))?;
    let load = linux_loader::loader::elf::Elf::load(&gm, None, &mut kf, Some(GuestAddress(HIGHMEM_START)))
        .map_err(|e| format!("elf load: {e:?}"))?;
    let entry = load.kernel_load.raw_value();
    println!("[pn-vmm] kernel entry = {entry:#x}");

    let initrd = load_initrd(&gm, initrd_path, low_size)?;

    let mut ro_set: std::collections::HashSet<usize> = match std::env::var("PN_VMM_BLK_RO") {
        Ok(s) => s.split(',').filter_map(|x| x.trim().parse::<usize>().ok()).collect(),
        Err(_) => std::iter::once(0usize).collect(),
    };
    ro_set.insert(0);
    let mut blks: Vec<VirtioBlkMmio> = Vec::new();
    let mut cmdline_str = String::from(CMDLINE);
    if let Ok(spec) = std::env::var("PN_VMM_BLK") {
        for (i, path) in spec.split(',').filter(|p| !p.is_empty()).enumerate() {
            let base = virtio::BLK_MMIO_BASE + (i as u64) * virtio::BLK_MMIO_SIZE;
            let gsi = virtio::BLK_GSI0 + i as u32;
            let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("blk eventfd: {e}"))?;
            vm.register_irqfd(&irq, gsi).map_err(|e| format!("blk irqfd: {e}"))?;
            let read_only = ro_set.contains(&i);
            let dev = VirtioBlkMmio::new(path, irq, base, read_only)?;
            println!("[pn-vmm] virtio-blk vd{} @ {base:#x} irq {gsi} — {} sectors, backing {path} [{}]",
                     (b'a' + i as u8) as char, dev.capacity_sectors(),
                     if read_only { "RO" } else { "RW" });
            cmdline_str.push_str(&format!(" virtio_mmio.device=4K@{base:#x}:{gsi}"));
            blks.push(dev);
        }
    }

    let blk_top_gsi = virtio::BLK_GSI0 + blks.len() as u32;
    let vsock_gsi = std::cmp::max(vsock::VSOCK_GSI, blk_top_gsi);
    let rng_gsi = std::cmp::max(virtio::RNG_GSI, vsock_gsi + 1);
    let gsi_max = u32::from(crate::mptable::IOAPIC_PINS) - 1;
    if rng_gsi > gsi_max {
        return Err(format!(
            "{} virtio-blk Platten belegen die IRQ-Leitungen bis {}; fuer vsock ({vsock_gsi}) und virtio-rng ({rng_gsi}) bleibt in der MP-Tabelle (IOAPIC-Pins 0..{gsi_max}) keine Leitung frei",
            blks.len(), blk_top_gsi - 1));
    }

    let mut vsock: Option<Arc<Mutex<VsockMmio>>> = None;
    if let Ok(cids) = std::env::var("PN_VMM_VSOCK") {
        if let Ok(cid) = cids.trim().parse::<u64>() {
            if cid >= 3 {
                let base = vsock::VSOCK_MMIO_BASE;
                let gsi = vsock_gsi;
                let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("vsock eventfd: {e}"))?;
                vm.register_irqfd(&irq, gsi).map_err(|e| format!("vsock irqfd: {e}"))?;
                cmdline_str.push_str(&format!(" virtio_mmio.device=4K@{base:#x}:{gsi}"));
                let dev = Arc::new(Mutex::new(VsockMmio::new(cid, irq, base)));

                let mode = match std::env::var("PN_VMM_VSOCK_SEAT") {
                    Ok(p) if !p.is_empty() => match std::os::unix::net::UnixStream::connect(&p) {
                        Ok(s) => {
                            let reader = s.try_clone().map_err(|e| format!("seat clone: {e}"))?;
                            dev.lock().unwrap().set_seat(s);

                            spawn_lane(
                                dev.clone(),
                                gm.clone(),
                                Lane::Seat,
                                reader,
                                adopt_listener("PN_VMM_VSOCK_SEAT_ADOPT"),
                                std::env::var("PN_VMM_ADOPT_TOKEN").unwrap_or_default().into_bytes(),
                            );

                            cmdline_str.push_str(" pn_seat=1");
                            format!("BRIDGE -> {p}")
                        }
                        Err(e) => format!("echo (seat {p}: {e})"),
                    },
                    _ => "echo".to_string(),
                };
                println!("[pn-vmm] virtio-vsock @ {base:#x} irq {gsi} — guest CID {cid}, host CID {}:{} [{mode}]",
                         vsock::HOST_CID, vsock::SERVICE_PORT);

                if let Ok(lp) = std::env::var("PN_VMM_VSOCK_LLM") {
                    if !lp.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&lp) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("llm clone: {e}"))?;
                                dev.lock().unwrap().set_llm(s);

                                spawn_lane(dev.clone(), gm.clone(), Lane::Llm, reader, None, Vec::new());
                                println!("[pn-vmm] virtio-vsock LLM channel -> {lp} (port {})", vsock::LLM_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_LLM {lp}: {e} (no LLM channel)"),
                        }
                    }
                }

                if let Ok(rp) = std::env::var("PN_VMM_VSOCK_RFB") {
                    if !rp.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&rp) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("rfb clone: {e}"))?;
                                dev.lock().unwrap().set_rfb(s);

                                spawn_lane(dev.clone(), gm.clone(), Lane::Rfb, reader, None, Vec::new());
                                println!("[pn-vmm] virtio-vsock RFB screen lane -> {rp} (port {})", vsock::RFB_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_RFB {rp}: {e} (no RFB lane)"),
                        }
                    }
                }

                if let Ok(np) = std::env::var("PN_VMM_VSOCK_NET") {
                    if !np.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&np) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("net clone: {e}"))?;
                                dev.lock().unwrap().set_net(s);

                                spawn_lane(dev.clone(), gm.clone(), Lane::Net, reader, None, Vec::new());
                                println!("[pn-vmm] virtio-vsock NET channel -> {np} (port {})", vsock::NET_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_NET {np}: {e} (no net channel)"),
                        }
                    }
                }

                if let Ok(tp) = std::env::var("PN_VMM_VSOCK_TERM") {
                    if !tp.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&tp) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("term clone: {e}"))?;
                                dev.lock().unwrap().set_term(s);

                                spawn_lane(
                                    dev.clone(),
                                    gm.clone(),
                                    Lane::Term,
                                    reader,
                                    adopt_listener("PN_VMM_VSOCK_TERM_ADOPT"),
                                    std::env::var("PN_VMM_ADOPT_TOKEN").unwrap_or_default().into_bytes(),
                                );
                                println!("[pn-vmm] virtio-vsock TERM channel -> {tp} (port {})", vsock::TERM_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_TERM {tp}: {e} (no term channel)"),
                        }
                    }
                }

                if let Ok(ap) = std::env::var("PN_VMM_VSOCK_ACT") {
                    if !ap.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&ap) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("act clone: {e}"))?;
                                dev.lock().unwrap().set_act(s);

                                spawn_lane(dev.clone(), gm.clone(), Lane::Act, reader, None, Vec::new());
                                println!("[pn-vmm] virtio-vsock ACT channel -> {ap} (port {})", vsock::ACT_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_ACT {ap}: {e} (no act channel)"),
                        }
                    }
                }

                if let Ok(gp) = std::env::var("PN_VMM_VSOCK_GUI") {
                    if !gp.is_empty() {
                        match std::os::unix::net::UnixStream::connect(&gp) {
                            Ok(s) => {
                                let reader = s.try_clone().map_err(|e| format!("gui clone: {e}"))?;
                                dev.lock().unwrap().set_gui(s);

                                spawn_lane(dev.clone(), gm.clone(), Lane::Gui, reader, None, Vec::new());
                                println!("[pn-vmm] virtio-vsock GUI desktop lane -> {gp} (port {})", vsock::GUI_PORT);
                            }
                            Err(e) => println!("[pn-vmm] PN_VMM_VSOCK_GUI {gp}: {e} (no GUI lane)"),
                        }
                    }
                }
                vsock = Some(dev);
            }
        }
    }

    let mut rng: Option<virtio::VirtioRngMmio> = None;
    if std::env::var("PN_VMM_RNG").map(|v| { let v = v.trim(); !(v == "0" || v == "off") }).unwrap_or(true) {
        let base = virtio::RNG_MMIO_BASE;
        let gsi = rng_gsi;
        let irq = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("rng eventfd: {e}"))?;
        vm.register_irqfd(&irq, gsi).map_err(|e| format!("rng irqfd: {e}"))?;
        cmdline_str.push_str(&format!(" virtio_mmio.device=4K@{base:#x}:{gsi}"));
        println!("[pn-vmm] virtio-rng @ {base:#x} irq {gsi} — /dev/urandom entropy source");
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

    let mut cmdline = linux_loader::cmdline::Cmdline::new(0x1000).map_err(|e| format!("cmdline: {e:?}"))?;
    cmdline.insert_str(&cmdline_str).map_err(|e| format!("cmdline insert: {e:?}"))?;
    let cmdline_c = cmdline.as_cstring().map_err(|e| format!("cmdline cstr: {e:?}"))?;
    let cmdline_bytes = cmdline_c.as_bytes_with_nul();
    gm.write_slice(cmdline_bytes, GuestAddress(CMDLINE_START)).map_err(|e| format!("write cmdline: {e:?}"))?;

    let mut bp = linux_loader::bootparam::boot_params::default();
    bp.hdr.type_of_loader = 0xff;
    bp.hdr.boot_flag = 0xaa55;
    bp.hdr.header = 0x5372_6448;
    bp.hdr.cmd_line_ptr = CMDLINE_START as u32;
    bp.hdr.cmdline_size = cmdline_bytes.len() as u32;
    bp.hdr.kernel_alignment = 0x0100_0000;
    if let Some((addr, size)) = initrd {
        bp.hdr.ramdisk_image = addr as u32;
        bp.hdr.ramdisk_size = size as u32;
        println!("[pn-vmm] initrd @ {addr:#x} ({size} bytes)");
    } else {
        println!("[pn-vmm] no initrd ({initrd_path} absent) — kernel will look for a root fs");
    }
    add_e820(&mut bp, 0, 0x9fc00, 1);
    add_e820(&mut bp, HIGHMEM_START, (low_size as u64) - HIGHMEM_START, 1);
    if mem_size > low_size {

        add_e820(&mut bp, HIGH_RAM_START, (mem_size - low_size) as u64, 1);
    }
    gm.write_obj(bp, GuestAddress(ZERO_PAGE_START)).map_err(|e| format!("write zeropage: {e:?}"))?;

    mptable::setup(&gm, nvcpus)?;

    setup_page_tables(&gm)?;

    let vcpu0 = vm.create_vcpu(0).map_err(|e| format!("create_vcpu 0: {e}"))?;
    vcpu_init(&kvm, &vcpu0, 0)?;
    let mut ap_vcpus: Vec<(u8, VcpuFd)> = Vec::new();
    for id in 1..nvcpus {
        let v = vm.create_vcpu(id as u64).map_err(|e| format!("create_vcpu {id}: {e}"))?;
        vcpu_init(&kvm, &v, id)?;
        ap_vcpus.push((id, v));
    }

    let mut sregs = vcpu0.get_sregs().map_err(|e| format!("get_sregs: {e}"))?;
    setup_gdt(&gm, &mut sregs)?;
    sregs.cr3 = BOOT_PML4;
    sregs.cr4 = 1 << 5;
    sregs.cr0 = 1 | (1 << 31);
    sregs.efer = (1 << 8) | (1 << 10);
    vcpu0.set_sregs(&sregs).map_err(|e| format!("set_sregs: {e}"))?;

    let mut regs = vcpu0.get_regs().map_err(|e| format!("get_regs: {e}"))?;
    regs.rflags = 0x2;
    regs.rip = entry;
    regs.rsi = ZERO_PAGE_START;
    regs.rbp = 0;
    regs.rsp = 0;
    vcpu0.set_regs(&regs).map_err(|e| format!("set_regs: {e}"))?;

    let intr_evt = EventFd::new(libc::EFD_NONBLOCK).map_err(|e| format!("eventfd: {e}"))?;
    vm.register_irqfd(&intr_evt, COM1_IRQ).map_err(|e| format!("register_irqfd: {e}"))?;
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

    println!("[pn-vmm] --- guest serial (console=ttyS0), Ctrl-] to detach ---");
    let _ = std::io::stdout().flush();

    let rip_trace = std::env::var("PN_VMM_RIP_TRACE").ok().and_then(|s| s.trim().parse::<u32>().ok());
    if let Some(secs) = rip_trace {
        unsafe {
            let mut sa: libc::sigaction = std::mem::zeroed();
            sa.sa_sigaction = rip_trace_alarm as usize;
            sa.sa_flags = 0;
            libc::sigaction(libc::SIGALRM, &sa, std::ptr::null_mut());
            libc::alarm(secs);
        }
        eprintln!("[pn-vmm][RIP-TRACE] armed: first sample in {secs}s, then every 1s");
    }

    let blks = Arc::new(Mutex::new(blks));
    let rng = Arc::new(Mutex::new(rng));
    let exit_rc = Arc::new(AtomicI32::new(0));
    let vcpu_tids = Arc::new(Mutex::new(vec![unsafe { libc::pthread_self() }]));
    let ctx = VcpuCtx {
        gm: gm.clone(),
        serial: serial.clone(),
        blks,
        rng,
        vsock: vsock.clone(),
        stop: stop.clone(),
        exit_rc: exit_rc.clone(),
        vcpu_tids: vcpu_tids.clone(),
    };

    let mut ap_handles = Vec::new();
    if !ap_vcpus.is_empty() {

        unsafe {
            let mut sa: libc::sigaction = std::mem::zeroed();
            sa.sa_sigaction = vcpu_kick_noop as usize;
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
        println!("[pn-vmm] SMP: {nvcpus} vcpus (BSP + {n_aps} APs parked in-kernel awaiting INIT/SIPI)");
    }

    if let Err(e) = seccomp::install() {
        eprintln!("[pn-vmm] FATAL: {e} — refusing to run a tenant without the seccomp sandbox");
        tty_restore(&tty_orig);
        std::process::exit(1);
    }

    let rc = run_vcpu(vcpu0, 0, ctx, rip_trace);
    tty_restore(&tty_orig);
    let _ = std::io::stdout().flush();

    let rc = rc.max(exit_rc.load(Ordering::SeqCst));
    if rc != 0 { std::process::exit(rc); }
    Ok(())
}

pub(crate) type Com1 = Serial<EventFdTrigger, NoEvents, RawOut>;

#[derive(Clone)]
pub(crate) struct VcpuCtx {
    pub(crate) gm: GuestMemoryMmap,
    pub(crate) serial: Arc<Mutex<Com1>>,
    pub(crate) blks: Arc<Mutex<Vec<virtio::VirtioBlkMmio>>>,
    pub(crate) rng: Arc<Mutex<Option<virtio::VirtioRngMmio>>>,
    pub(crate) vsock: Option<Arc<Mutex<VsockMmio>>>,
    pub(crate) stop: Arc<AtomicBool>,
    pub(crate) exit_rc: Arc<AtomicI32>,
    pub(crate) vcpu_tids: Arc<Mutex<Vec<libc::pthread_t>>>,
}

pub(crate) extern "C" fn vcpu_kick_noop(_: std::os::raw::c_int) {}

pub(crate) fn run_vcpu(vcpu: VcpuFd, id: u8, ctx: VcpuCtx, rip_trace: Option<u32>) -> i32 {
    let VcpuCtx { gm, serial, blks, rng, vsock, stop, exit_rc, vcpu_tids } = ctx;
    #[cfg(not(target_arch = "x86_64"))]
    let _ = &rip_trace;

    #[cfg(target_arch = "x86_64")]
    let mut pt_walked = false;
    #[cfg(target_arch = "x86_64")]
    let mut dm_dumped = false;
    let mut hlt_run: u64 = 0;
    let rc = loop {
        if stop.load(Ordering::SeqCst) {
            if id == 0 {
                println!("\n[pn-vmm] stopped.");
            }
            break 0;
        }
        match vcpu.run() {
            Ok(VcpuExit::IoOut(port, data)) => {
                if (0x3f8..=0x3ff).contains(&port) {
                    if let Ok(mut g) = serial.lock() {
                        let _ = g.write((port - 0x3f8) as u8, data[0]);
                    }
                }
                hlt_run = 0;
            }
            Ok(VcpuExit::IoIn(port, data)) => {
                if (0x3f8..=0x3ff).contains(&port) {
                    let v = serial.lock().map(|mut g| g.read((port - 0x3f8) as u8)).unwrap_or(0);
                    for b in data.iter_mut() { *b = v; }
                } else {
                    for b in data.iter_mut() { *b = 0xff; }
                }
                hlt_run = 0;
            }
            Ok(VcpuExit::Hlt) => {

                hlt_run += 1;
                if hlt_run > 200_000_000 { println!("\n[pn-vmm] guest wedged in HLT — stopping."); break 1; }
            }
            Ok(VcpuExit::Shutdown) => {
                println!("\n[pn-vmm] guest KVM_EXIT_SHUTDOWN (reboot/poweroff) — clean guest exit.");
                break 0;
            }
            Ok(VcpuExit::MmioRead(addr, data)) => {
                mmio_read_dispatch(addr, data, &blks, &rng, &vsock, &serial);
                hlt_run = 0;
            }
            Ok(VcpuExit::MmioWrite(addr, data)) => {
                mmio_write_dispatch(addr, data, &blks, &rng, &vsock, &gm, &serial);
                hlt_run = 0;
            }
            Ok(other) => { eprintln!("\n[pn-vmm] unhandled exit: {other:?}"); break 1; }
            Err(e) => {
                if e.errno() == libc::EAGAIN {

                    continue;
                }
                if e.errno() == libc::EINTR {

                    #[cfg(target_arch = "x86_64")]
                    if rip_trace.is_some() {
                        if let Ok(r) = vcpu.get_regs() {
                            let s = vcpu.get_sregs().ok();
                            let (cr2, cr3, cr0) = s.as_ref().map(|sr| (sr.cr2, sr.cr3, sr.cr0)).unwrap_or((0, 0, 0));
                            eprintln!(
                                "[pn-vmm][RIP-TRACE] rip={:#018x} cr2={:#018x} cr3={:#x} cr0={:#x} rsp={:#018x} rflags={:#x}",
                                r.rip, cr2, cr3, cr0, r.rsp, r.rflags
                            );
                            if !pt_walked && cr2 != 0 {
                                pt_walked = true;
                                eprintln!("[pn-vmm][PT-WALK] cr2={:#x}:{}", cr2, walk_pt(&gm, cr3, cr2));
                                eprintln!("[pn-vmm][PT-WALK] rsp={:#x}:{}", r.rsp, walk_pt(&gm, cr3, r.rsp));
                            }

                            let exc_rips: Vec<u64> = std::env::var("PN_VMM_EXC_RIPS").ok()
                                .map(|s| s.split(',').filter_map(|x| u64::from_str_radix(x.trim().trim_start_matches("0x"), 16).ok()).collect())
                                .unwrap_or_else(|| vec![0xffffffff81c00b50, 0xffffffff81c00bc0, 0xffffffff81c00a90]);
                            if exc_rips.contains(&r.rip) {
                                eprintln!("[pn-vmm][EXC-FRAME] at {:#x}{}", r.rip, decode_exc_frame(&gm, cr3, r.rsp, true));
                            }

                            if !dm_dumped {
                                dm_dumped = true;
                                let m = 0x000f_ffff_ffff_f000u64;
                                let pml4 = cr3 & m;
                                let e273: u64 = gm.read_obj(GuestAddress(pml4 + 273 * 8)).unwrap_or(0xdead);
                                eprintln!("[pn-vmm][DMAP] cr3={:#x} PML4[273]={:#x}(P={})", cr3, e273, e273 & 1);
                                if e273 & 1 != 0 {
                                    let pdpt = e273 & m;
                                    for i in 0..4u64 {
                                        let e: u64 = gm.read_obj(GuestAddress(pdpt + i * 8)).unwrap_or(0xdead);
                                        eprintln!("[pn-vmm][DMAP]   PDPT[{i}]={e:#x}(P={},PS={})", e & 1, (e >> 7) & 1);
                                        if i == 0 && e & 1 != 0 && e & (1 << 7) == 0 {
                                            let pd = e & m;
                                            for j in 0..4u64 {
                                                let pe: u64 = gm.read_obj(GuestAddress(pd + j * 8)).unwrap_or(0xdead);
                                                eprintln!("[pn-vmm][DMAP]     PD[{j}]={pe:#x}(P={},PS={})", pe & 1, (pe >> 7) & 1);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        unsafe { libc::alarm(1); }
                    }
                    continue;
                }
                eprintln!("\n[pn-vmm] KVM_RUN error: {e}");
                break 1;
            }
        }
    };
    stop.store(true, Ordering::SeqCst);
    exit_rc.fetch_max(rc, Ordering::SeqCst);
    let me = unsafe { libc::pthread_self() };
    if let Ok(tids) = vcpu_tids.lock() {
        for &t in tids.iter() {
            if t != me {
                unsafe { libc::pthread_kill(t, libc::SIGUSR1); }
            }
        }
    }
    rc
}

#[allow(unused_variables)]
fn mmio_read_dispatch(
    addr: u64,
    data: &mut [u8],
    blks: &Arc<Mutex<Vec<virtio::VirtioBlkMmio>>>,
    rng: &Arc<Mutex<Option<virtio::VirtioRngMmio>>>,
    vsock: &Option<Arc<Mutex<VsockMmio>>>,
    serial: &Arc<Mutex<Com1>>,
) {

    #[cfg(target_arch = "aarch64")]
    {
        let sbase = arch_aarch64::SERIAL_MMIO_BASE;
        if (sbase..sbase + arch_aarch64::SERIAL_MMIO_SIZE).contains(&addr) {
            let off = (addr - sbase) as u8;
            let v = serial.lock().map(|mut g| g.read(off)).unwrap_or(0);
            for b in data.iter_mut() { *b = v; }
            return;
        }
    }
    if let Ok(mut bl) = blks.lock() {
        if let Some(b) = bl.iter_mut().find(|b| b.contains(addr)) {
            let off = addr - b.base();
            b.mmio_read(off, data);
            return;
        }
    }
    if let Ok(mut rg) = rng.lock() {
        if let Some(r) = rg.as_mut().filter(|r| r.contains(addr)) {
            let off = addr - r.base();
            r.mmio_read(off, data);
            return;
        }
    }
    if let Some(vs) = vsock.as_ref() {
        if let Ok(mut g) = vs.lock() {
            if g.contains(addr) {
                let off = addr - g.base();
                g.mmio_read(off, data);
                return;
            }
        }
    }
    for x in data.iter_mut() { *x = 0; }
}

#[allow(unused_variables)]
fn mmio_write_dispatch(
    addr: u64,
    data: &[u8],
    blks: &Arc<Mutex<Vec<virtio::VirtioBlkMmio>>>,
    rng: &Arc<Mutex<Option<virtio::VirtioRngMmio>>>,
    vsock: &Option<Arc<Mutex<VsockMmio>>>,
    gm: &GuestMemoryMmap,
    serial: &Arc<Mutex<Com1>>,
) {

    #[cfg(target_arch = "aarch64")]
    {
        let sbase = arch_aarch64::SERIAL_MMIO_BASE;
        if (sbase..sbase + arch_aarch64::SERIAL_MMIO_SIZE).contains(&addr) {
            if let Ok(mut g) = serial.lock() {
                let _ = g.write((addr - sbase) as u8, data[0]);
            }
            return;
        }
    }
    if let Ok(mut bl) = blks.lock() {
        if let Some(b) = bl.iter_mut().find(|b| b.contains(addr)) {
            let off = addr - b.base();
            b.mmio_write(off, data, gm);
            return;
        }
    }
    if let Ok(mut rg) = rng.lock() {
        if let Some(r) = rg.as_mut().filter(|r| r.contains(addr)) {
            let off = addr - r.base();
            r.mmio_write(off, data, gm);
            return;
        }
    }
    if let Some(vs) = vsock.as_ref() {
        if let Ok(mut g) = vs.lock() {
            if g.contains(addr) {
                let off = addr - g.base();
                g.mmio_write(off, data, gm);
            }
        }
    }
}

#[cfg(target_arch = "x86_64")]
fn vcpu_init(kvm: &Kvm, vcpu: &VcpuFd, id: u8) -> Result<(), String> {
    let mut cpuid = kvm.get_supported_cpuid(KVM_MAX_CPUID_ENTRIES).map_err(|e| format!("cpuid: {e}"))?;
    for e in cpuid.as_mut_slice().iter_mut() {
        match e.function {
            1 => {
                e.ecx |= 1 << 31;

                e.edx |= 1 << 9;

                e.ebx = (e.ebx & 0x00ff_ffff) | ((id as u32) << 24);
            }
            0xb | 0x1f => {

                e.edx = id as u32;
            }
            _ => {}
        }
    }
    vcpu.set_cpuid2(&cpuid).map_err(|e| format!("set_cpuid2 vcpu{id}: {e}"))?;

    let apic_base: u64 = 0xfee0_0000 | (1 << 11) | if id == 0 { 1 << 8 } else { 0 };
    let msrs = Msrs::from_entries(&[
        kvm_msr_entry { index: 0x0000_001b, data: apic_base, ..Default::default() },

        kvm_msr_entry { index: 0x0000_01a0, data: 0x1, ..Default::default() },
    ])
    .map_err(|e| format!("build msrs: {e:?}"))?;
    vcpu.set_msrs(&msrs).map_err(|e| format!("set msrs vcpu{id}: {e}"))?;
    Ok(())
}

pub(crate) fn load_initrd(gm: &GuestMemoryMmap, path: &str, mem_size: usize) -> Result<Option<(u64, u64)>, String> {
    if !std::path::Path::new(path).exists() {
        return Ok(None);
    }
    let data = std::fs::read(path).map_err(|e| format!("read initrd: {e}"))?;
    let size = data.len() as u64;
    if size == 0 {
        return Ok(None);
    }

    let addr = ((mem_size as u64).saturating_sub(size)) & !0xfffu64;
    gm.write_slice(&data, GuestAddress(addr)).map_err(|e| format!("write initrd: {e:?}"))?;
    Ok(Some((addr, size)))
}

#[cfg(target_arch = "x86_64")]
fn add_e820(bp: &mut linux_loader::bootparam::boot_params, addr: u64, size: u64, type_: u32) {
    let i = bp.e820_entries as usize;
    bp.e820_table[i].addr = addr;
    bp.e820_table[i].size = size;
    bp.e820_table[i].type_ = type_;
    bp.e820_entries += 1;
}

#[cfg(target_arch = "x86_64")]
fn setup_page_tables(gm: &GuestMemoryMmap) -> Result<(), String> {
    const P: u64 = 1;
    const RW: u64 = 1 << 1;
    const PS: u64 = 1 << 7;
    gm.write_obj(BOOT_PDPT | P | RW, GuestAddress(BOOT_PML4)).map_err(|e| format!("pml4: {e:?}"))?;

    for g in 0..4u64 {
        let pd = BOOT_PD + g * 0x1000;
        gm.write_obj(pd | P | RW, GuestAddress(BOOT_PDPT + g * 8)).map_err(|e| format!("pdpt: {e:?}"))?;
        for i in 0..512u64 {
            let pde = ((g * 512 + i) << 21) | P | RW | PS;
            gm.write_obj(pde, GuestAddress(pd + i * 8)).map_err(|e| format!("pd: {e:?}"))?;
        }
    }
    Ok(())
}

#[cfg(target_arch = "x86_64")]
fn walk_pt(gm: &GuestMemoryMmap, cr3: u64, virt: u64) -> String {
    let mask = 0x000f_ffff_ffff_f000u64;
    let idxs = [(virt >> 39) & 0x1ff, (virt >> 30) & 0x1ff, (virt >> 21) & 0x1ff, (virt >> 12) & 0x1ff];
    let names = ["PML4", "PDPT", "PD", "PT"];
    let mut base = cr3 & mask;
    let mut out = String::new();
    for lvl in 0..4 {
        let ent_addr = base + idxs[lvl] * 8;
        let ent: u64 = gm.read_obj(GuestAddress(ent_addr)).unwrap_or(0xdead_dead);
        out.push_str(&format!(" {}[{}]={:#x}(P={}{})", names[lvl], idxs[lvl], ent, ent & 1,
                              if ent & (1 << 7) != 0 { ",PS" } else { "" }));
        if ent & 1 == 0 {
            out.push_str(" <-NOT_PRESENT");
            break;
        }
        if ent & (1 << 7) != 0 {
            out.push_str(" <-HUGE_LEAF");
            break;
        }
        base = ent & mask;
    }
    out
}

#[cfg(target_arch = "x86_64")]
fn translate(gm: &GuestMemoryMmap, cr3: u64, virt: u64) -> Option<u64> {
    let mask = 0x000f_ffff_ffff_f000u64;
    let idxs = [(virt >> 39) & 0x1ff, (virt >> 30) & 0x1ff, (virt >> 21) & 0x1ff, (virt >> 12) & 0x1ff];
    let mut base = cr3 & mask;
    for lvl in 0..4 {
        let ent: u64 = gm.read_obj(GuestAddress(base + idxs[lvl] * 8)).ok()?;
        if ent & 1 == 0 {
            return None;
        }
        if ent & (1 << 7) != 0 {

            let (pmask, vmask) = if lvl == 1 { (!0x3fff_ffffu64, 0x3fff_ffffu64) } else { (!0x1f_ffffu64, 0x1f_ffffu64) };
            return Some((ent & mask & pmask) | (virt & vmask));
        }
        base = ent & mask;
    }
    Some((base) | (virt & 0xfff))
}

#[cfg(target_arch = "x86_64")]
fn decode_exc_frame(gm: &GuestMemoryMmap, cr3: u64, rsp: u64, has_err: bool) -> String {
    let phys = match translate(gm, cr3, rsp) {
        Some(p) => p,
        None => return " [frame rsp not mapped]".to_string(),
    };
    let mut w = [0u64; 6];
    for (i, slot) in w.iter_mut().enumerate() {
        *slot = gm.read_obj(GuestAddress(phys + (i as u64) * 8)).unwrap_or(0xdead_dead);
    }
    if has_err {
        let err = w[0];
        format!(
            " [FRAME err={:#x}(P={} W={} U={} RSVD={} I/D={}) faultRIP={:#018x} cs={:#x} rflags={:#x}]",
            err, err & 1, (err >> 1) & 1, (err >> 2) & 1, (err >> 3) & 1, (err >> 4) & 1, w[1], w[2], w[3]
        )
    } else {
        format!(" [FRAME RIP={:#018x} cs={:#x} rflags={:#x} rsp={:#018x} ss={:#x}]", w[0], w[1], w[2], w[3], w[4])
    }
}

#[cfg(target_arch = "x86_64")]
fn setup_gdt(gm: &GuestMemoryMmap, sregs: &mut kvm_bindings::kvm_sregs) -> Result<(), String> {
    let gdt: [u64; 3] = [0, 0x00af_9b00_0000_ffff, 0x00cf_9300_0000_ffff];
    for (i, e) in gdt.iter().enumerate() {
        gm.write_obj(*e, GuestAddress(BOOT_GDT + (i as u64) * 8)).map_err(|e| format!("gdt: {e:?}"))?;
    }
    sregs.gdt.base = BOOT_GDT;
    sregs.gdt.limit = (gdt.len() * 8 - 1) as u16;
    let code = seg(0x08, 0xffff_f, 0xb, 1, 0, 1, 1, 0, 1);
    let data = seg(0x10, 0xffff_f, 0x3, 1, 0, 1, 1, 1, 0);
    sregs.cs = code;
    sregs.ds = data; sregs.es = data; sregs.fs = data; sregs.gs = data; sregs.ss = data;
    Ok(())
}

#[cfg(target_arch = "x86_64")]
#[allow(clippy::too_many_arguments)]
fn seg(selector: u16, limit: u32, type_: u8, s: u8, dpl: u8, present: u8, avl_g: u8, db: u8, l: u8) -> kvm_segment {
    kvm_segment {
        base: 0,
        limit,
        selector,
        type_,
        present,
        dpl,
        db,
        s,
        l,
        g: avl_g,
        avl: 0,
        unusable: 0,
        padding: 0,
    }
}

pub(crate) struct EventFdTrigger(pub(crate) EventFd);
impl Trigger for EventFdTrigger {
    type E = std::io::Error;
    fn trigger(&self) -> Result<(), Self::E> {
        self.0.write(1)
    }
}

pub(crate) struct RawOut;
impl std::io::Write for RawOut {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let n = unsafe { libc::write(1, buf.as_ptr() as *const libc::c_void, buf.len()) };
        if n < 0 { Err(std::io::Error::last_os_error()) } else { Ok(n as usize) }
    }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

pub(crate) fn tty_raw() -> Option<libc::termios> {
    unsafe {
        if libc::isatty(0) == 0 {
            return None;
        }
        let mut t: libc::termios = std::mem::zeroed();
        if libc::tcgetattr(0, &mut t) != 0 {
            return None;
        }
        let orig = t;
        libc::cfmakeraw(&mut t);
        libc::tcsetattr(0, libc::TCSANOW, &t);
        Some(orig)
    }
}
pub(crate) fn tty_restore(orig: &Option<libc::termios>) {
    if let Some(t) = orig {
        unsafe { libc::tcsetattr(0, libc::TCSANOW, t); }
    }
}

#[cfg(target_arch = "x86_64")]
extern "C" fn rip_trace_alarm(_: std::os::raw::c_int) {}

pub(crate) fn ctrlc_set<F: Fn() + Send + 'static>(f: F) -> std::io::Result<()> {
    use std::os::raw::c_int;
    static mut CB: Option<Box<dyn Fn() + Send>> = None;
    extern "C" fn handler(_: c_int) { unsafe { if let Some(cb) = (*std::ptr::addr_of!(CB)).as_ref() { cb(); } } }
    unsafe {
        CB = Some(Box::new(f));
        libc::signal(libc::SIGINT, handler as *const () as usize);
    }
    Ok(())
}

#[cfg(target_arch = "x86_64")]
fn stage1_selftest() {
    let kvm = Kvm::new().expect("kvm");
    let vm = kvm.create_vm().expect("vm");
    let code: &[u8] = &[0xba, 0xf8, 0x03, 0x00, 0xd8, 0x04, b'0', 0xee, 0xb0, b'\n', 0xee, 0xf4];
    let size = 0x1000usize;
    let host = unsafe {
        libc::mmap(std::ptr::null_mut(), size, libc::PROT_READ | libc::PROT_WRITE,
                   libc::MAP_ANONYMOUS | libc::MAP_SHARED | libc::MAP_NORESERVE, -1, 0)
    } as *mut u8;
    unsafe { std::slice::from_raw_parts_mut(host, size)[..code.len()].copy_from_slice(code); }
    let region = kvm_userspace_memory_region { slot: 0, flags: 0, guest_phys_addr: 0x1000, memory_size: size as u64, userspace_addr: host as u64 };
    unsafe { vm.set_user_memory_region(region).unwrap(); }
    let vcpu = vm.create_vcpu(0).unwrap();
    let mut sregs = vcpu.get_sregs().unwrap();
    sregs.cs.base = 0; sregs.cs.selector = 0; vcpu.set_sregs(&sregs).unwrap();
    let mut regs = vcpu.get_regs().unwrap();
    regs.rip = 0x1000; regs.rax = 2; regs.rbx = 2; regs.rflags = 2; vcpu.set_regs(&regs).unwrap();
    let mut out = String::new();
    loop {
        match vcpu.run().unwrap() {
            VcpuExit::IoOut(_, d) => for &b in d { out.push(b as char); },
            VcpuExit::Hlt => break,
            _ => break,
        }
    }
    println!("[pn-vmm] stage1 guest output: {out:?} ({})", if out.trim() == "4" { "OK" } else { "FAIL" });
}
