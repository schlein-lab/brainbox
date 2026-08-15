use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::FileTypeExt;
use std::os::unix::io::AsRawFd;
use vm_memory::{Bytes, GuestAddress, GuestMemoryMmap};
use vmm_sys_util::eventfd::EventFd;

fn backing_bytes(file: &File) -> Result<u64, String> {
    let meta = file.metadata().map_err(|e| format!("stat blk: {e}"))?;
    if meta.file_type().is_block_device() {

        const BLKGETSIZE64: libc::c_ulong = 0x8008_1272;
        let mut sz: u64 = 0;
        let r = unsafe { libc::ioctl(file.as_raw_fd(), BLKGETSIZE64, &mut sz as *mut u64) };
        if r != 0 {
            return Err(format!("BLKGETSIZE64: {}", std::io::Error::last_os_error()));
        }
        Ok(sz)
    } else {
        Ok(meta.len())
    }
}

#[cfg(target_arch = "x86_64")]
pub const BLK_MMIO_BASE: u64 = 0xd000_0000;
#[cfg(target_arch = "aarch64")]
pub const BLK_MMIO_BASE: u64 = 0x4010_0000;
pub const BLK_MMIO_SIZE: u64 = 0x1000;
pub const BLK_GSI0: u32 = 5;

#[cfg(target_arch = "x86_64")]
pub const RNG_MMIO_BASE: u64 = 0xd020_0000;
#[cfg(target_arch = "aarch64")]
pub const RNG_MMIO_BASE: u64 = 0x4030_0000;
pub const RNG_MMIO_SIZE: u64 = 0x1000;
pub const RNG_GSI: u32 = 7;

const MAGIC: u32 = 0x7472_6976;
const VERSION_LEGACY: u32 = 1;
const DEVICE_ID_BLK: u32 = 2;
const DEVICE_ID_RNG: u32 = 4;
const VENDOR_ID: u32 = 0x4d56_4e50;
const SECTOR: u64 = 512;
const QUEUE_MAX: u16 = 256;
const VIRTIO_BLK_F_RO: u32 = 5;

const MAX_SEG: usize = 8 << 20;

const T_IN: u32 = 0;
const T_OUT: u32 = 1;
const T_FLUSH: u32 = 4;
const T_GET_ID: u32 = 8;

const F_NEXT: u16 = 1;
const F_WRITE: u16 = 2;

const R_MAGIC: u64 = 0x000;
const R_VERSION: u64 = 0x004;
const R_DEVICE_ID: u64 = 0x008;
const R_VENDOR_ID: u64 = 0x00c;
const R_DEVICE_FEATURES: u64 = 0x010;
const R_DEVICE_FEATURES_SEL: u64 = 0x014;
const R_DRIVER_FEATURES: u64 = 0x020;
const R_DRIVER_FEATURES_SEL: u64 = 0x024;
const R_GUEST_PAGE_SIZE: u64 = 0x028;
const R_QUEUE_SEL: u64 = 0x030;
const R_QUEUE_NUM_MAX: u64 = 0x034;
const R_QUEUE_NUM: u64 = 0x038;
const R_QUEUE_ALIGN: u64 = 0x03c;
const R_QUEUE_PFN: u64 = 0x040;
const R_QUEUE_NOTIFY: u64 = 0x050;
const R_INTERRUPT_STATUS: u64 = 0x060;
const R_INTERRUPT_ACK: u64 = 0x064;
const R_STATUS: u64 = 0x070;
const R_CONFIG: u64 = 0x100;

fn write_le(data: &mut [u8], v: u32) {
    let b = v.to_le_bytes();
    for (i, out) in data.iter_mut().enumerate() {
        *out = if i < 4 { b[i] } else { 0 };
    }
}
fn read_le(data: &[u8]) -> u32 {
    let mut b = [0u8; 4];
    for (i, x) in data.iter().enumerate().take(4) {
        b[i] = *x;
    }
    u32::from_le_bytes(b)
}
fn align_up(v: u64, a: u64) -> u64 {
    (v + a - 1) & !(a - 1)
}

struct Queue {
    num: u16,
    align: u32,
    pfn: u32,
    last_avail: u16,
}
impl Queue {
    fn new() -> Self {
        Queue { num: 0, align: 4096, pfn: 0, last_avail: 0 }
    }
    fn ready(&self) -> bool {
        self.pfn != 0 && self.num != 0
    }
}

pub struct VirtioBlkMmio {
    mmio_base: u64,
    file: File,
    capacity_sectors: u64,
    read_only: bool,
    irq: EventFd,
    page_size: u32,
    features_sel: u32,
    driver_features_sel: u32,
    driver_features: u64,
    queue_sel: u32,
    queue: Queue,
    status: u32,
    interrupt_status: u32,
}

impl VirtioBlkMmio {
    pub fn new(path: &str, irq: EventFd, mmio_base: u64, read_only: bool) -> Result<Self, String> {

        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(!read_only)
            .open(path)
            .map_err(|e| format!("open blk backing {path}: {e}"))?;
        let len = backing_bytes(&file)?;
        Ok(VirtioBlkMmio {
            mmio_base,
            file,
            capacity_sectors: len / SECTOR,
            read_only,
            irq,
            page_size: 4096,
            features_sel: 0,
            driver_features_sel: 0,
            driver_features: 0,
            queue_sel: 0,
            queue: Queue::new(),
            status: 0,
            interrupt_status: 0,
        })
    }

    pub fn capacity_sectors(&self) -> u64 {
        self.capacity_sectors
    }

    pub fn base(&self) -> u64 {
        self.mmio_base
    }

    pub fn contains(&self, addr: u64) -> bool {
        addr >= self.mmio_base && addr < self.mmio_base + BLK_MMIO_SIZE
    }

    pub fn mmio_read(&mut self, off: u64, data: &mut [u8]) {
        if off >= R_CONFIG {

            let c = (off - R_CONFIG) as usize;
            let mut cfg = [0u8; 32];
            cfg[..8].copy_from_slice(&self.capacity_sectors.to_le_bytes());
            for (i, b) in data.iter_mut().enumerate() {
                *b = *cfg.get(c + i).unwrap_or(&0);
            }
            return;
        }
        let v: u32 = match off {
            R_MAGIC => MAGIC,
            R_VERSION => VERSION_LEGACY,
            R_DEVICE_ID => DEVICE_ID_BLK,
            R_VENDOR_ID => VENDOR_ID,

            R_DEVICE_FEATURES => {
                if self.read_only && self.features_sel == 0 {
                    1 << VIRTIO_BLK_F_RO
                } else {
                    0
                }
            }
            R_QUEUE_NUM_MAX => QUEUE_MAX as u32,
            R_QUEUE_PFN => self.queue.pfn,
            R_INTERRUPT_STATUS => self.interrupt_status,
            R_STATUS => self.status,
            _ => 0,
        };
        write_le(data, v);
    }

    pub fn mmio_write(&mut self, off: u64, data: &[u8], gm: &GuestMemoryMmap) {
        let v = read_le(data);
        match off {
            R_DEVICE_FEATURES_SEL => self.features_sel = v,
            R_DRIVER_FEATURES_SEL => self.driver_features_sel = v,
            R_DRIVER_FEATURES => {
                if self.driver_features_sel == 0 {
                    self.driver_features = (self.driver_features & !0xffff_ffff) | v as u64;
                } else {
                    self.driver_features = (self.driver_features & 0xffff_ffff) | ((v as u64) << 32);
                }
            }
            R_GUEST_PAGE_SIZE => self.page_size = v,
            R_QUEUE_SEL => self.queue_sel = v,
            R_QUEUE_NUM => {
                if self.queue_sel == 0 {
                    self.queue.num = v as u16;
                }
            }
            R_QUEUE_ALIGN => {
                if self.queue_sel == 0 {
                    self.queue.align = v;
                }
            }
            R_QUEUE_PFN => {
                if self.queue_sel == 0 {
                    self.queue.pfn = v;
                    self.queue.last_avail = 0;
                }
            }
            R_QUEUE_NOTIFY => self.process_queue(gm),
            R_INTERRUPT_ACK => self.interrupt_status &= !v,
            R_STATUS => {
                self.status = v;
                if v == 0 {

                    self.queue = Queue::new();
                    self.interrupt_status = 0;
                }
            }
            _ => {}
        }
        let _ = self.features_sel;
    }

    fn process_queue(&mut self, gm: &GuestMemoryMmap) {
        if !self.queue.ready() {
            return;
        }
        let qsz = self.queue.num as u64;
        let base = self.queue.pfn as u64 * self.page_size as u64;
        let desc_addr = base;
        let avail_addr = base + 16 * qsz;
        let used_addr = align_up(avail_addr + 6 + 2 * qsz, self.queue.align as u64);

        let avail_idx: u16 = gm.read_obj(GuestAddress(avail_addr + 2)).unwrap_or(0);
        let mut serviced = false;
        while self.queue.last_avail != avail_idx {
            let slot = (self.queue.last_avail as u64) % qsz;
            let head: u16 = gm.read_obj(GuestAddress(avail_addr + 4 + slot * 2)).unwrap_or(0);
            let written = self.handle_chain(gm, desc_addr, qsz, head);

            let used_idx: u16 = gm.read_obj(GuestAddress(used_addr + 2)).unwrap_or(0);
            let ring_slot = (used_idx as u64) % qsz;
            let e = used_addr + 4 + ring_slot * 8;
            let _ = gm.write_obj(head as u32, GuestAddress(e));
            let _ = gm.write_obj(written, GuestAddress(e + 4));
            let _ = gm.write_obj(used_idx.wrapping_add(1), GuestAddress(used_addr + 2));

            self.queue.last_avail = self.queue.last_avail.wrapping_add(1);
            serviced = true;
        }
        if serviced {
            self.interrupt_status |= 1;
            let _ = self.irq.write(1);
        }
    }

    fn handle_chain(&mut self, gm: &GuestMemoryMmap, desc_addr: u64, qsz: u64, head: u16) -> u32 {
        let mut segs: Vec<(u64, u32, bool)> = Vec::new();
        let mut idx = head;
        let mut guard = 0u64;
        loop {
            let d = desc_addr + (idx as u64) * 16;
            let addr: u64 = gm.read_obj(GuestAddress(d)).unwrap_or(0);
            let len: u32 = gm.read_obj(GuestAddress(d + 8)).unwrap_or(0);
            let flags: u16 = gm.read_obj(GuestAddress(d + 12)).unwrap_or(0);
            let next: u16 = gm.read_obj(GuestAddress(d + 14)).unwrap_or(0);
            segs.push((addr, len, flags & F_WRITE != 0));
            guard += 1;
            if flags & F_NEXT == 0 || guard > qsz {
                break;
            }
            idx = next;
        }
        if segs.len() < 2 {
            return 0;
        }
        let (haddr, _, _) = segs[0];
        let req_type: u32 = gm.read_obj(GuestAddress(haddr)).unwrap_or(u32::MAX);
        let sector: u64 = gm.read_obj(GuestAddress(haddr + 8)).unwrap_or(0);
        let status_addr = segs[segs.len() - 1].0;
        let data_segs = &segs[1..segs.len() - 1];

        let mut ok = true;
        let mut written: u32 = 0;
        match req_type {
            T_IN => {
                let mut off = sector * SECTOR;
                for &(addr, len, _) in data_segs {
                    if len as usize > MAX_SEG {
                        ok = false;
                        break;
                    }
                    let mut buf = vec![0u8; len as usize];
                    if self.read_at(off, &mut buf).is_err() {
                        ok = false;
                        break;
                    }
                    if gm.write_slice(&buf, GuestAddress(addr)).is_err() {
                        ok = false;
                        break;
                    }
                    off += len as u64;
                    written += len;
                }
            }
            T_OUT if self.read_only => {
                ok = false;
            }
            T_OUT => {
                let mut off = sector * SECTOR;
                for &(addr, len, _) in data_segs {
                    if len as usize > MAX_SEG {
                        ok = false;
                        break;
                    }
                    let mut buf = vec![0u8; len as usize];
                    if gm.read_slice(&mut buf, GuestAddress(addr)).is_err() {
                        ok = false;
                        break;
                    }
                    if self.write_at(off, &buf).is_err() {
                        ok = false;
                        break;
                    }
                    off += len as u64;
                }
            }
            T_FLUSH => {
                let _ = self.file.flush();
                let _ = self.file.sync_all();
            }
            T_GET_ID => {
                let id = b"pn-vmm-blk";
                if let Some(&(addr, len, _)) = data_segs.first() {
                    let n = (len as usize).min(id.len());
                    let _ = gm.write_slice(&id[..n], GuestAddress(addr));
                    written += n as u32;
                }
            }
            _ => ok = false,
        }
        let st: u8 = if ok { 0 } else { 1 };
        let _ = gm.write_slice(&[st], GuestAddress(status_addr));
        written + 1
    }

    fn read_at(&mut self, off: u64, buf: &mut [u8]) -> std::io::Result<()> {
        self.file.seek(SeekFrom::Start(off))?;
        self.file.read_exact(buf)
    }
    fn write_at(&mut self, off: u64, buf: &[u8]) -> std::io::Result<()> {
        self.file.seek(SeekFrom::Start(off))?;
        self.file.write_all(buf)
    }
}

pub struct VirtioRngMmio {
    mmio_base: u64,
    src: File,
    irq: EventFd,
    page_size: u32,
    driver_features_sel: u32,
    driver_features: u64,
    queue_sel: u32,
    queue: Queue,
    status: u32,
    interrupt_status: u32,
}

impl VirtioRngMmio {
    pub fn new(irq: EventFd, mmio_base: u64) -> Result<Self, String> {
        let src = std::fs::File::open("/dev/urandom").map_err(|e| format!("open /dev/urandom: {e}"))?;
        Ok(VirtioRngMmio {
            mmio_base,
            src,
            irq,
            page_size: 4096,
            driver_features_sel: 0,
            driver_features: 0,
            queue_sel: 0,
            queue: Queue::new(),
            status: 0,
            interrupt_status: 0,
        })
    }

    pub fn base(&self) -> u64 {
        self.mmio_base
    }
    pub fn contains(&self, addr: u64) -> bool {
        addr >= self.mmio_base && addr < self.mmio_base + RNG_MMIO_SIZE
    }

    pub fn mmio_read(&mut self, off: u64, data: &mut [u8]) {
        if off >= R_CONFIG {
            for b in data.iter_mut() {
                *b = 0;
            }
            return;
        }
        let v: u32 = match off {
            R_MAGIC => MAGIC,
            R_VERSION => VERSION_LEGACY,
            R_DEVICE_ID => DEVICE_ID_RNG,
            R_VENDOR_ID => VENDOR_ID,
            R_DEVICE_FEATURES => 0,
            R_QUEUE_NUM_MAX => QUEUE_MAX as u32,
            R_QUEUE_PFN => self.queue.pfn,
            R_INTERRUPT_STATUS => self.interrupt_status,
            R_STATUS => self.status,
            _ => 0,
        };
        write_le(data, v);
    }

    pub fn mmio_write(&mut self, off: u64, data: &[u8], gm: &GuestMemoryMmap) {
        let v = read_le(data);
        match off {
            R_DRIVER_FEATURES_SEL => self.driver_features_sel = v,
            R_DRIVER_FEATURES => {
                if self.driver_features_sel == 0 {
                    self.driver_features = (self.driver_features & !0xffff_ffff) | v as u64;
                } else {
                    self.driver_features = (self.driver_features & 0xffff_ffff) | ((v as u64) << 32);
                }
            }
            R_GUEST_PAGE_SIZE => self.page_size = v,
            R_QUEUE_SEL => self.queue_sel = v,
            R_QUEUE_NUM => {
                if self.queue_sel == 0 {
                    self.queue.num = v as u16;
                }
            }
            R_QUEUE_ALIGN => {
                if self.queue_sel == 0 {
                    self.queue.align = v;
                }
            }
            R_QUEUE_PFN => {
                if self.queue_sel == 0 {
                    self.queue.pfn = v;
                    self.queue.last_avail = 0;
                }
            }
            R_QUEUE_NOTIFY => self.process_queue(gm),
            R_INTERRUPT_ACK => self.interrupt_status &= !v,
            R_STATUS => {
                self.status = v;
                if v == 0 {
                    self.queue = Queue::new();
                    self.interrupt_status = 0;
                }
            }
            _ => {}
        }
    }

    fn process_queue(&mut self, gm: &GuestMemoryMmap) {
        if !self.queue.ready() {
            return;
        }
        let qsz = self.queue.num as u64;
        let base = self.queue.pfn as u64 * self.page_size as u64;
        let desc_addr = base;
        let avail_addr = base + 16 * qsz;
        let used_addr = align_up(avail_addr + 6 + 2 * qsz, self.queue.align as u64);

        let avail_idx: u16 = gm.read_obj(GuestAddress(avail_addr + 2)).unwrap_or(0);
        let mut serviced = false;
        while self.queue.last_avail != avail_idx {
            let slot = (self.queue.last_avail as u64) % qsz;
            let head: u16 = gm.read_obj(GuestAddress(avail_addr + 4 + slot * 2)).unwrap_or(0);
            let written = self.fill_chain(gm, desc_addr, qsz, head);

            let used_idx: u16 = gm.read_obj(GuestAddress(used_addr + 2)).unwrap_or(0);
            let ring_slot = (used_idx as u64) % qsz;
            let e = used_addr + 4 + ring_slot * 8;
            let _ = gm.write_obj(head as u32, GuestAddress(e));
            let _ = gm.write_obj(written, GuestAddress(e + 4));
            let _ = gm.write_obj(used_idx.wrapping_add(1), GuestAddress(used_addr + 2));

            self.queue.last_avail = self.queue.last_avail.wrapping_add(1);
            serviced = true;
        }
        if serviced {
            self.interrupt_status |= 1;
            let _ = self.irq.write(1);
        }
    }

    fn fill_chain(&mut self, gm: &GuestMemoryMmap, desc_addr: u64, qsz: u64, head: u16) -> u32 {
        let mut idx = head;
        let mut guard = 0u64;
        let mut written: u32 = 0;
        loop {
            let d = desc_addr + (idx as u64) * 16;
            let addr: u64 = gm.read_obj(GuestAddress(d)).unwrap_or(0);
            let len: u32 = gm.read_obj(GuestAddress(d + 8)).unwrap_or(0);
            let flags: u16 = gm.read_obj(GuestAddress(d + 12)).unwrap_or(0);
            let next: u16 = gm.read_obj(GuestAddress(d + 14)).unwrap_or(0);
            if flags & F_WRITE != 0 && len > 0 {

                let n = (len as usize).min(MAX_SEG);
                let mut buf = vec![0u8; n];
                if self.src.read_exact(&mut buf).is_ok() && gm.write_slice(&buf, GuestAddress(addr)).is_ok() {
                    written += n as u32;
                }
            }
            guard += 1;
            if flags & F_NEXT == 0 || guard > qsz {
                break;
            }
            idx = next;
        }
        written
    }
}
