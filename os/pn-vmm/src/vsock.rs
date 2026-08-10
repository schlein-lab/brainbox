use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Write;
use std::os::unix::net::UnixStream;
use vm_memory::{Bytes, GuestAddress, GuestMemoryMmap};
use vmm_sys_util::eventfd::EventFd;

pub const VSOCK_MMIO_SIZE: u64 = 0x1000;

#[cfg(target_arch = "x86_64")]
pub const VSOCK_MMIO_BASE: u64 = 0xd010_0000;
#[cfg(target_arch = "aarch64")]
pub const VSOCK_MMIO_BASE: u64 = 0x4020_0000;
pub const VSOCK_GSI: u32 = 8;

const MAGIC: u32 = 0x7472_6976;
const VERSION_LEGACY: u32 = 1;
const DEVICE_ID_VSOCK: u32 = 19;
const VENDOR_ID: u32 = 0x4d56_4e50;

pub const HOST_CID: u64 = 2;
pub const SERVICE_PORT: u32 = 1234;
pub const LLM_PORT: u32 = 9100;
pub const RFB_PORT: u32 = 5900;

pub const NET_PORT: u32 = 9200;
pub const TERM_PORT: u32 = 9300;

pub const ACT_PORT: u32 = 9400;

pub const GUI_PORT: u32 = 9500;

const HDR_LEN: usize = 44;
const OUR_BUF_ALLOC: u32 = 256 * 1024;
const NUM_QUEUES: usize = 3;
const RXQ: usize = 0;
const TXQ: usize = 1;
const QUEUE_MAX: u16 = 256;

const OP_REQUEST: u16 = 1;
const OP_RESPONSE: u16 = 2;
const OP_RST: u16 = 3;
const OP_SHUTDOWN: u16 = 4;
const OP_RW: u16 = 5;
const OP_CREDIT_UPDATE: u16 = 6;
const OP_CREDIT_REQUEST: u16 = 7;
const TYPE_STREAM: u16 = 1;

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

fn wr32(data: &mut [u8], v: u32) {
    let b = v.to_le_bytes();
    for (i, o) in data.iter_mut().enumerate() {
        *o = if i < 4 { b[i] } else { 0 };
    }
}
fn rd32(data: &[u8]) -> u32 {
    let mut b = [0u8; 4];
    for (i, x) in data.iter().enumerate().take(4) {
        b[i] = *x;
    }
    u32::from_le_bytes(b)
}
fn align_up(v: u64, a: u64) -> u64 {
    (v + a - 1) & !(a - 1)
}

#[derive(Clone)]
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

struct Hdr {
    src_cid: u64,
    dst_cid: u64,
    src_port: u32,
    dst_port: u32,
    len: u32,
    typ: u16,
    op: u16,
    flags: u32,
    buf_alloc: u32,
    fwd_cnt: u32,
}
impl Hdr {
    fn parse(b: &[u8]) -> Option<Hdr> {
        if b.len() < HDR_LEN {
            return None;
        }
        let g32 = |o: usize| u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]]);
        let g64 = |o: usize| {
            u64::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3], b[o + 4], b[o + 5], b[o + 6], b[o + 7]])
        };
        let g16 = |o: usize| u16::from_le_bytes([b[o], b[o + 1]]);
        Some(Hdr {
            src_cid: g64(0),
            dst_cid: g64(8),
            src_port: g32(16),
            dst_port: g32(20),
            len: g32(24),
            typ: g16(28),
            op: g16(30),
            flags: g32(32),
            buf_alloc: g32(36),
            fwd_cnt: g32(40),
        })
    }
    fn bytes(&self) -> [u8; HDR_LEN] {
        let mut b = [0u8; HDR_LEN];
        b[0..8].copy_from_slice(&self.src_cid.to_le_bytes());
        b[8..16].copy_from_slice(&self.dst_cid.to_le_bytes());
        b[16..20].copy_from_slice(&self.src_port.to_le_bytes());
        b[20..24].copy_from_slice(&self.dst_port.to_le_bytes());
        b[24..28].copy_from_slice(&self.len.to_le_bytes());
        b[28..30].copy_from_slice(&self.typ.to_le_bytes());
        b[30..32].copy_from_slice(&self.op.to_le_bytes());
        b[32..36].copy_from_slice(&self.flags.to_le_bytes());
        b[36..40].copy_from_slice(&self.buf_alloc.to_le_bytes());
        b[40..44].copy_from_slice(&self.fwd_cnt.to_le_bytes());
        b
    }
}

pub struct VsockMmio {
    mmio_base: u64,
    irq: EventFd,
    guest_cid: u64,
    page_size: u32,
    features_sel: u32,
    driver_features_sel: u32,
    driver_features: u64,
    queue_sel: u32,
    queues: [Queue; NUM_QUEUES],
    status: u32,
    interrupt_status: u32,
    rx_backlog: VecDeque<Vec<u8>>,
    our_fwd_cnt: u32,
    peer_credit: HashMap<u32, (u32, u32)>,
    tx_cnt: HashMap<u32, u32>,
    seat: Option<UnixStream>,
    conn: Option<(u64, u32, u32)>,
    llm: Option<UnixStream>,
    conn_llm: Option<(u64, u32, u32)>,
    rfb: Option<UnixStream>,
    conn_rfb: Option<(u64, u32, u32)>,
    net: Option<UnixStream>,
    conn_net: Option<(u64, u32, u32)>,
    term: Option<UnixStream>,
    conn_term: Option<(u64, u32, u32)>,
    act: Option<UnixStream>,
    conn_act: Option<(u64, u32, u32)>,
    gui: Option<UnixStream>,
    conn_gui: Option<(u64, u32, u32)>,
}

impl VsockMmio {
    pub fn new(guest_cid: u64, irq: EventFd, mmio_base: u64) -> Self {
        VsockMmio {
            mmio_base,
            irq,
            guest_cid,
            page_size: 4096,
            features_sel: 0,
            driver_features_sel: 0,
            driver_features: 0,
            queue_sel: 0,
            queues: [Queue::new(), Queue::new(), Queue::new()],
            status: 0,
            interrupt_status: 0,
            rx_backlog: VecDeque::new(),
            our_fwd_cnt: 0,
            peer_credit: HashMap::new(),
            tx_cnt: HashMap::new(),
            seat: None,
            conn: None,
            llm: None,
            conn_llm: None,
            rfb: None,
            conn_rfb: None,
            net: None,
            conn_net: None,
            term: None,
            conn_term: None,
            act: None,
            conn_act: None,
            gui: None,
            conn_gui: None,
        }
    }

    pub fn set_seat(&mut self, s: UnixStream) {
        self.seat = Some(s);
    }

    pub fn set_llm(&mut self, s: UnixStream) {
        self.llm = Some(s);
    }

    pub fn deliver_rx(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn deliver_rx_llm(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_llm {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn set_rfb(&mut self, s: UnixStream) {
        self.rfb = Some(s);
    }

    pub fn deliver_rx_rfb(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_rfb {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn set_net(&mut self, s: UnixStream) {
        self.net = Some(s);
    }

    pub fn deliver_rx_net(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_net {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn set_term(&mut self, s: UnixStream) {
        self.term = Some(s);
    }

    pub fn deliver_rx_term(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_term {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn set_act(&mut self, s: UnixStream) {
        self.act = Some(s);
    }

    pub fn deliver_rx_act(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_act {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn set_gui(&mut self, s: UnixStream) {
        self.gui = Some(s);
    }

    pub fn deliver_rx_gui(&mut self, gm: &GuestMemoryMmap, data: &[u8]) {
        if let Some((gc, gp, hp)) = self.conn_gui {
            self.push_pkt(gc, gp, hp, OP_RW, data);
            self.flush_rx(gm);
        }
    }

    pub fn base(&self) -> u64 {
        self.mmio_base
    }
    pub fn contains(&self, addr: u64) -> bool {
        addr >= self.mmio_base && addr < self.mmio_base + VSOCK_MMIO_SIZE
    }

    pub fn mmio_read(&mut self, off: u64, data: &mut [u8]) {
        if off >= R_CONFIG {

            let c = (off - R_CONFIG) as usize;
            let cid = self.guest_cid.to_le_bytes();
            for (i, b) in data.iter_mut().enumerate() {
                *b = *cid.get(c + i).unwrap_or(&0);
            }
            return;
        }
        let qi = (self.queue_sel as usize).min(NUM_QUEUES - 1);
        let v: u32 = match off {
            R_MAGIC => MAGIC,
            R_VERSION => VERSION_LEGACY,
            R_DEVICE_ID => DEVICE_ID_VSOCK,
            R_VENDOR_ID => VENDOR_ID,
            R_DEVICE_FEATURES => 0,
            R_QUEUE_NUM_MAX => QUEUE_MAX as u32,
            R_QUEUE_PFN => self.queues[qi].pfn,
            R_INTERRUPT_STATUS => self.interrupt_status,
            R_STATUS => self.status,
            _ => 0,
        };
        wr32(data, v);
    }

    pub fn mmio_write(&mut self, off: u64, data: &[u8], gm: &GuestMemoryMmap) {
        let v = rd32(data);
        let qi = (self.queue_sel as usize).min(NUM_QUEUES - 1);
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
            R_QUEUE_NUM => self.queues[qi].num = v as u16,
            R_QUEUE_ALIGN => self.queues[qi].align = v,
            R_QUEUE_PFN => {
                self.queues[qi].pfn = v;
                self.queues[qi].last_avail = 0;
            }
            R_QUEUE_NOTIFY => {
                if v as usize == TXQ {
                    self.process_tx(gm);
                }

                self.flush_rx(gm);
            }
            R_INTERRUPT_ACK => self.interrupt_status &= !v,
            R_STATUS => {
                self.status = v;
                if v == 0 {
                    self.queues = [Queue::new(), Queue::new(), Queue::new()];
                    self.interrupt_status = 0;
                    self.rx_backlog.clear();
                    self.our_fwd_cnt = 0;
                    self.peer_credit.clear();
                    self.tx_cnt.clear();
                }
            }
            _ => {}
        }
        let _ = self.features_sel;
    }

    fn qaddrs(&self, qi: usize) -> (u64, u64, u64, u64) {
        let q = &self.queues[qi];
        let qsz = q.num as u64;
        let base = q.pfn as u64 * self.page_size as u64;
        let desc = base;
        let avail = base + 16 * qsz;
        let used = align_up(avail + 6 + 2 * qsz, q.align as u64);
        (qsz, desc, avail, used)
    }

    fn process_tx(&mut self, gm: &GuestMemoryMmap) {
        if !self.queues[TXQ].ready() {
            return;
        }
        let (qsz, desc, avail, used) = self.qaddrs(TXQ);
        let avail_idx: u16 = gm.read_obj(GuestAddress(avail + 2)).unwrap_or(0);
        let mut serviced = false;
        while self.queues[TXQ].last_avail != avail_idx {
            let slot = (self.queues[TXQ].last_avail as u64) % qsz;
            let head: u16 = gm.read_obj(GuestAddress(avail + 4 + slot * 2)).unwrap_or(0);
            let pkt = read_chain(gm, desc, qsz, head);
            self.handle_tx_packet(&pkt);

            let used_idx: u16 = gm.read_obj(GuestAddress(used + 2)).unwrap_or(0);
            let e = used + 4 + (used_idx as u64 % qsz) * 8;
            let _ = gm.write_obj(head as u32, GuestAddress(e));
            let _ = gm.write_obj(0u32, GuestAddress(e + 4));
            let _ = gm.write_obj(used_idx.wrapping_add(1), GuestAddress(used + 2));
            self.queues[TXQ].last_avail = self.queues[TXQ].last_avail.wrapping_add(1);
            serviced = true;
        }
        if serviced {
            self.interrupt_status |= 1;
        }
    }

    fn handle_tx_packet(&mut self, pkt: &[u8]) {
        let h = match Hdr::parse(pkt) {
            Some(h) => h,
            None => return,
        };

        self.peer_credit.insert(h.src_port, (h.buf_alloc, h.fwd_cnt));
        let payload = if pkt.len() > HDR_LEN {
            &pkt[HDR_LEN..HDR_LEN + (h.len as usize).min(pkt.len() - HDR_LEN)]
        } else {
            &[][..]
        };

        match h.op {
            OP_REQUEST => {

                if self.llm.is_some() && h.dst_port == LLM_PORT {
                    self.conn_llm = Some((h.src_cid, h.src_port, h.dst_port));
                } else if self.rfb.is_some() && h.dst_port == RFB_PORT {
                    self.conn_rfb = Some((h.src_cid, h.src_port, h.dst_port));
                } else if self.net.is_some() && h.dst_port == NET_PORT {
                    self.conn_net = Some((h.src_cid, h.src_port, h.dst_port));
                } else if self.term.is_some() && h.dst_port == TERM_PORT {
                    self.conn_term = Some((h.src_cid, h.src_port, h.dst_port));
                } else if self.act.is_some() && h.dst_port == ACT_PORT {
                    self.conn_act = Some((h.src_cid, h.src_port, h.dst_port));
                } else if self.gui.is_some() && h.dst_port == GUI_PORT {
                    self.conn_gui = Some((h.src_cid, h.src_port, h.dst_port));
                } else if matches!(h.dst_port,
                                   LLM_PORT | RFB_PORT | NET_PORT | TERM_PORT | ACT_PORT | GUI_PORT) {

                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_RST, &[]);
                    return;
                } else {
                    self.conn = Some((h.src_cid, h.src_port, h.dst_port));
                }
                self.tx_cnt.insert(h.src_port, 0);
                self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_RESPONSE, &[]);
            }
            OP_RW => {
                self.our_fwd_cnt = self.our_fwd_cnt.wrapping_add(payload.len() as u32);
                let is_llm = matches!(self.conn_llm, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                let is_rfb = matches!(self.conn_rfb, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                let is_net = matches!(self.conn_net, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                let is_term = matches!(self.conn_term, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                let is_act = matches!(self.conn_act, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                let is_gui = matches!(self.conn_gui, Some((c, p, _)) if c == h.src_cid && p == h.src_port);
                if is_llm {

                    if let Some(llm) = self.llm.as_mut() {
                        let _ = llm.write_all(payload);
                        let _ = llm.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if is_rfb {

                    if let Some(rfb) = self.rfb.as_mut() {
                        let _ = rfb.write_all(payload);
                        let _ = rfb.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if is_net {

                    if let Some(net) = self.net.as_mut() {
                        let _ = net.write_all(payload);
                        let _ = net.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if is_term {

                    if let Some(term) = self.term.as_mut() {
                        let _ = term.write_all(payload);
                        let _ = term.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if is_act {

                    if let Some(act) = self.act.as_mut() {
                        let _ = act.write_all(payload);
                        let _ = act.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if is_gui {

                    if let Some(gui) = self.gui.as_mut() {
                        let _ = gui.write_all(payload);
                        let _ = gui.flush();
                    }
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else if self.seat.is_some() {

                    if let Some(seat) = self.seat.as_mut() {
                        let _ = seat.write_all(payload);
                        let _ = seat.flush();
                    }

                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
                } else {

                    let echo: Vec<u8> = payload.iter().map(|c| c.to_ascii_uppercase()).collect();
                    self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_RW, &echo);
                }
            }
            OP_CREDIT_REQUEST => {
                self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_CREDIT_UPDATE, &[]);
            }
            OP_SHUTDOWN => {

                if matches!(self.conn_llm, Some((c, p, _)) if c == h.src_cid && p == h.src_port) {
                    self.conn_llm = None;
                } else if matches!(self.conn_rfb, Some((c, p, _)) if c == h.src_cid && p == h.src_port) {
                    self.conn_rfb = None;
                } else if matches!(self.conn_gui, Some((c, p, _)) if c == h.src_cid && p == h.src_port) {
                    self.conn_gui = None;
                } else if matches!(self.conn, Some((c, p, _)) if c == h.src_cid && p == h.src_port) {
                    self.conn = None;
                }
                self.peer_credit.remove(&h.src_port);
                self.tx_cnt.remove(&h.src_port);
                self.push_pkt(h.src_cid, h.src_port, h.dst_port, OP_RST, &[]);
            }
            _ => {}
        }
    }

    fn push_pkt(&mut self, guest_cid: u64, guest_port: u32, host_port: u32, op: u16, payload: &[u8]) {
        let hdr = Hdr {
            src_cid: HOST_CID,
            dst_cid: guest_cid,
            src_port: host_port,
            dst_port: guest_port,
            len: payload.len() as u32,
            typ: TYPE_STREAM,
            op,
            flags: 0,
            buf_alloc: OUR_BUF_ALLOC,
            fwd_cnt: self.our_fwd_cnt,
        };
        let mut p = Vec::with_capacity(HDR_LEN + payload.len());
        p.extend_from_slice(&hdr.bytes());
        p.extend_from_slice(payload);
        self.rx_backlog.push_back(p);
    }

    fn flush_rx(&mut self, gm: &GuestMemoryMmap) {
        if !self.queues[RXQ].ready() || self.rx_backlog.is_empty() {
            if self.interrupt_status & 1 != 0 {
                let _ = self.irq.write(1);
            }
            return;
        }
        let (qsz, desc, avail, used) = self.qaddrs(RXQ);
        let mut delivered = false;
        let mut blocked: HashSet<u32> = HashSet::new();
        loop {
            if self.rx_backlog.is_empty() {
                break;
            }
            let avail_idx: u16 = gm.read_obj(GuestAddress(avail + 2)).unwrap_or(0);
            if self.queues[RXQ].last_avail == avail_idx {
                break;
            }

            let mut pick: Option<usize> = None;
            for (i, p) in self.rx_backlog.iter().enumerate() {
                if i >= 1024 {
                    break;
                }
                let port = u32::from_le_bytes([p[20], p[21], p[22], p[23]]);
                if blocked.contains(&port) {
                    continue;
                }
                let op = u16::from_le_bytes([p[30], p[31]]);
                if op == OP_RW && p.len() > HDR_LEN {
                    let need = (p.len() - HDR_LEN) as u32;
                    let (balloc, fwd) = *self.peer_credit.get(&port).unwrap_or(&(OUR_BUF_ALLOC, 0));
                    let sent = *self.tx_cnt.get(&port).unwrap_or(&0);
                    if need > balloc.saturating_sub(sent.wrapping_sub(fwd)) {
                        blocked.insert(port);
                        continue;
                    }
                }
                pick = Some(i);
                break;
            }
            let pkt = match pick {
                Some(i) => self.rx_backlog.remove(i).unwrap(),
                None => break,
            };
            if pkt.len() > HDR_LEN && u16::from_le_bytes([pkt[30], pkt[31]]) == OP_RW {
                let port = u32::from_le_bytes([pkt[20], pkt[21], pkt[22], pkt[23]]);
                let e = self.tx_cnt.entry(port).or_insert(0);
                *e = e.wrapping_add((pkt.len() - HDR_LEN) as u32);
            }
            let slot = (self.queues[RXQ].last_avail as u64) % qsz;
            let head: u16 = gm.read_obj(GuestAddress(avail + 4 + slot * 2)).unwrap_or(0);
            let written = write_chain(gm, desc, qsz, head, &pkt);
            let used_idx: u16 = gm.read_obj(GuestAddress(used + 2)).unwrap_or(0);
            let e = used + 4 + (used_idx as u64 % qsz) * 8;
            let _ = gm.write_obj(head as u32, GuestAddress(e));
            let _ = gm.write_obj(written, GuestAddress(e + 4));
            let _ = gm.write_obj(used_idx.wrapping_add(1), GuestAddress(used + 2));
            self.queues[RXQ].last_avail = self.queues[RXQ].last_avail.wrapping_add(1);
            delivered = true;
        }
        if delivered {
            self.interrupt_status |= 1;
        }
        if self.interrupt_status & 1 != 0 {
            let _ = self.irq.write(1);
        }
    }
}

fn read_chain(gm: &GuestMemoryMmap, desc_addr: u64, qsz: u64, head: u16) -> Vec<u8> {
    let mut out = Vec::new();
    let mut idx = head;
    let mut guard = 0u64;
    loop {
        let d = desc_addr + (idx as u64) * 16;
        let addr: u64 = gm.read_obj(GuestAddress(d)).unwrap_or(0);
        let len: u32 = gm.read_obj(GuestAddress(d + 8)).unwrap_or(0);
        let flags: u16 = gm.read_obj(GuestAddress(d + 12)).unwrap_or(0);
        let next: u16 = gm.read_obj(GuestAddress(d + 14)).unwrap_or(0);
        if flags & F_WRITE == 0 && len > 0 {
            let mut buf = vec![0u8; len as usize];
            if gm.read_slice(&mut buf, GuestAddress(addr)).is_ok() {
                out.extend_from_slice(&buf);
            }
        }
        guard += 1;
        if flags & F_NEXT == 0 || guard > qsz {
            break;
        }
        idx = next;
    }
    out
}

fn write_chain(gm: &GuestMemoryMmap, desc_addr: u64, qsz: u64, head: u16, data: &[u8]) -> u32 {
    let mut off = 0usize;
    let mut idx = head;
    let mut guard = 0u64;
    loop {
        let d = desc_addr + (idx as u64) * 16;
        let addr: u64 = gm.read_obj(GuestAddress(d)).unwrap_or(0);
        let len: u32 = gm.read_obj(GuestAddress(d + 8)).unwrap_or(0);
        let flags: u16 = gm.read_obj(GuestAddress(d + 12)).unwrap_or(0);
        let next: u16 = gm.read_obj(GuestAddress(d + 14)).unwrap_or(0);
        if flags & F_WRITE != 0 && off < data.len() {
            let n = (len as usize).min(data.len() - off);
            if gm.write_slice(&data[off..off + n], GuestAddress(addr)).is_ok() {
                off += n;
            }
        }
        guard += 1;
        if flags & F_NEXT == 0 || guard > qsz || off >= data.len() {
            break;
        }
        idx = next;
    }
    off as u32
}
