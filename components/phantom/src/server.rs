use phantom::sys;
use phantom::wire::{event, p_i32, p_str, p_u32, parse_bind, read_str, u32le};

use crate::compositor::SharedDamage;
use crate::{BufferInfo, ClientState, Shared};
use phantom::input::{active_layout, Layout};

use std::collections::{HashMap, HashSet};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::raw::c_int;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub struct Present {
    generation: AtomicU64,
}

impl Present {
    pub fn new() -> Present {
        Present { generation: AtomicU64::new(0) }
    }

    pub fn present(&self) {
        self.generation.fetch_add(1, Ordering::Release);
    }
    fn gen(&self) -> u64 {
        self.generation.load(Ordering::Acquire)
    }
}

pub type Presenter = Arc<Present>;

const OUT_W: i32 = 1280;
const OUT_H: i32 = 800;

static SCREEN_W: AtomicU32 = AtomicU32::new(OUT_W as u32);
static SCREEN_H: AtomicU32 = AtomicU32::new(OUT_H as u32);

pub fn set_screen_size(w: u32, h: u32) {
    if w > 0 && h > 0 {
        SCREEN_W.store(w, Ordering::Relaxed);
        SCREEN_H.store(h, Ordering::Relaxed);
    }
}

fn screen_wh() -> (i32, i32) {
    (SCREEN_W.load(Ordering::Relaxed) as i32, SCREEN_H.load(Ordering::Relaxed) as i32)
}

const GLOBALS: &[(u32, &str, u32)] = &[
    (1, "wl_compositor", 4),
    (2, "wl_shm", 1),
    (3, "wl_seat", 5),
    (4, "wl_output", 2),
    (5, "xdg_wm_base", 3),
    (6, "wl_data_device_manager", 3),
    (7, "wl_subcompositor", 1),

    (8, "xwayland_shell_v1", 1),
];

fn keymap_string() -> String {
    let sym = match active_layout() {
        Layout::De => "pc+de",
        Layout::Us => "pc+us",
    };
    format!(
        "xkb_keymap {{\n\
         xkb_keycodes {{ include \"evdev+aliases(qwerty)\" }};\n\
         xkb_types    {{ include \"complete\" }};\n\
         xkb_compat   {{ include \"complete\" }};\n\
         xkb_symbols  {{ include \"{sym}\" }};\n\
         }};\n"
    )
}

pub fn run(listen_path: &str, shared: Shared) {
    run_with(listen_path, shared, None, None)
}

pub fn run_with(listen_path: &str, shared: Shared, damage: Option<SharedDamage>, present: Option<Presenter>) {
    let _ = std::fs::remove_file(listen_path);
    let listener = match UnixListener::bind(listen_path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom: cannot bind {listen_path}: {e}");
            std::process::exit(1);
        }
    };
    for client in listener.incoming() {
        let Ok(client) = client else { continue };
        let shared = shared.clone();
        let damage = damage.clone();
        let present = present.clone();
        thread::spawn(move || {
            let cid = crate::CLIENT_SEQ.fetch_add(1, Ordering::Relaxed);
            serve_client(client, cid, shared.clone(), damage, present);
            crate::xwl_parent_gone(&shared, cid);
            shared.lock().unwrap().remove(&cid);
            eprintln!("[h{cid}] closed");
        });
    }
}

fn serve_client(stream: UnixStream, cid: u64, shared: Shared, damage: Option<SharedDamage>, present: Option<Presenter>) {
    let client_fd = stream.as_raw_fd();
    let writer = Arc::new(Mutex::new(()));
    {
        let mut objects = HashMap::new();
        objects.insert(1u32, "wl_display".to_string());
        let st = ClientState {
            client_fd,
            writer: writer.clone(),
            objects,
            pid: sys::peer_pid(client_fd),
            seat: None,
            keyboard: None,
            pointer: None,
            pointers: Vec::new(),
            keyboards: Vec::new(),
            surface: None,
            title: None,
            app_id: None,
            serial: 1,
            time: 1,
            pools: HashMap::new(),
            buffers: HashMap::new(),
            pending_attach: HashMap::new(),
            surf_buffer: HashMap::new(),
            surf_damage: HashMap::new(),
            xdg_surf_wl: HashMap::new(),
            popup_surf: HashSet::new(),
            subsurface_obj: HashMap::new(),
            subsurf_parent: HashMap::new(),
            subsurf_pos: HashMap::new(),
            keylog: String::new(),
            xparent: None,
        };
        shared.lock().unwrap().insert(cid, st);
    }
    eprintln!("[h{cid}] connected (pid={:?})", sys::peer_pid(client_fd));

    let frames = Arc::new(Mutex::new(Vec::<u32>::new()));
    let alive = Arc::new(AtomicBool::new(true));
    let start = Instant::now();

    {
        let (frames, writer, alive, present) = (frames.clone(), writer.clone(), alive.clone(), present.clone());
        thread::spawn(move || {
            let mut last_gen = present.as_ref().map(|p| p.gen()).unwrap_or(0);
            let mut since_fire = Instant::now();

            let cap = Duration::from_millis(100);
            while alive.load(Ordering::Relaxed) {

                thread::sleep(Duration::from_millis(16));
                let fire = match present.as_ref() {
                    Some(p) => {
                        let g = p.gen();
                        let advanced = g != last_gen;
                        let capped = since_fire.elapsed() >= cap;
                        if advanced {
                            last_gen = g;
                        }
                        advanced || capped
                    }
                    None => true,
                };
                if !fire {
                    continue;
                }
                let due: Vec<u32> = std::mem::take(&mut *frames.lock().unwrap());
                if due.is_empty() {
                    continue;
                }
                since_fire = Instant::now();
                let now = start.elapsed().as_millis() as u32;
                let mut out = Vec::new();
                for cb in due {
                    let mut p = Vec::new();
                    p_u32(&mut p, now);
                    out.extend(event(cb, 0, &p));
                    let mut d = Vec::new();
                    p_u32(&mut d, cb);
                    out.extend(event(1, 1, &d));
                }
                let _g = writer.lock().unwrap();
                if sys::send_with_fds(client_fd, &out, &[]).is_err() {
                    break;
                }
            }
        });
    }

    let mut srv = Srv {
        cid,
        shared: shared.clone(),
        damage,
        frames: frames.clone(),
        objects: {
            let mut m = HashMap::new();
            m.insert(1u32, "wl_display".to_string());
            m
        },
        pending: Vec::new(),
        output_obj: None,
        surface_xdg: HashMap::new(),
        xdg_toplevel: HashMap::new(),
        configured: HashSet::new(),
        entered: HashSet::new(),
        debug: std::env::var_os("PHANTOM_DEBUG").is_some(),
        xwl_surf: HashMap::new(),
    };

    let mut acc: Vec<u8> = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let mut fds: Vec<c_int> = Vec::new();
        match sys::recv_with_fds(client_fd, &mut buf, &mut fds) {
            Ok(0) => break,
            Ok(n) => {
                for f in fds {
                    srv.pending.push(unsafe { OwnedFd::from_raw_fd(f) });
                }
                acc.extend_from_slice(&buf[..n]);
                let mut off = 0usize;
                while acc.len() - off >= 8 {
                    let sender = u32le(&acc, off);
                    let word2 = u32le(&acc, off + 4);
                    let size = (word2 >> 16) as usize;
                    let opcode = (word2 & 0xffff) as u16;
                    if size < 8 || off + size > acc.len() {
                        break;
                    }
                    let (out, fd) = srv.handle(sender, opcode, &acc[off + 8..off + size]);
                    if !out.is_empty() {
                        let _g = writer.lock().unwrap();
                        let fdv: Vec<c_int> = fd.as_ref().map(|f| vec![f.as_raw_fd()]).unwrap_or_default();
                        let _ = sys::send_with_fds(client_fd, &out, &fdv);
                    }
                    off += size;
                }
                if off > 0 {
                    acc.drain(0..off);
                }
            }
            Err(e) => {
                if e.raw_os_error() == Some(4) {
                    continue;
                }
                break;
            }
        }
    }
    alive.store(false, Ordering::Relaxed);
}

struct Srv {
    cid: u64,
    shared: Shared,

    damage: Option<SharedDamage>,
    frames: Arc<Mutex<Vec<u32>>>,
    objects: HashMap<u32, String>,
    pending: Vec<OwnedFd>,
    output_obj: Option<u32>,
    surface_xdg: HashMap<u32, u32>,
    xdg_toplevel: HashMap<u32, u32>,
    configured: HashSet<u32>,
    entered: HashSet<u32>,
    debug: bool,
    xwl_surf: HashMap<u32, u32>,
}

impl Srv {
    fn with_state<R>(&self, f: impl FnOnce(&mut ClientState) -> R) -> Option<R> {
        let mut g = self.shared.lock().unwrap();
        g.get_mut(&self.cid).map(f)
    }

    fn next_serial(&self) -> u32 {
        self.with_state(|st| {
            st.serial += 1;
            st.serial
        })
        .unwrap_or(1)
    }

    fn handle(&mut self, sender: u32, opcode: u16, payload: &[u8]) -> (Vec<u8>, Option<OwnedFd>) {
        let iface = if sender == 1 {
            "wl_display".to_string()
        } else {
            self.objects.get(&sender).cloned().unwrap_or_default()
        };
        if self.debug {
            eprintln!("[h{}] {iface}.{opcode} ({}B, {} fds pend)", self.cid, payload.len(), self.pending.len());
        }
        let mut out: Vec<u8> = Vec::new();
        let mut fd_out: Option<OwnedFd> = None;

        match (iface.as_str(), opcode) {

            ("wl_display", 0) => {

                let cb = u32le(payload, 0);
                let mut p = Vec::new();
                p_u32(&mut p, 0);
                out.extend(event(cb, 0, &p));
                let mut d = Vec::new();
                p_u32(&mut d, cb);
                out.extend(event(1, 1, &d));
            }
            ("wl_display", 1) => {

                let reg = u32le(payload, 0);
                self.objects.insert(reg, "wl_registry".into());
                for &(name, ifc, ver) in GLOBALS {
                    let mut p = Vec::new();
                    p_u32(&mut p, name);
                    p_str(&mut p, ifc);
                    p_u32(&mut p, ver);
                    out.extend(event(reg, 0, &p));
                }
            }

            ("wl_registry", 0) => {

                let (ifc, _ver, new_id) = parse_bind(payload);
                match ifc.as_str() {
                    "wl_shm" => {
                        for fmt in [0u32, 1u32] {
                            let mut p = Vec::new();
                            p_u32(&mut p, fmt);
                            out.extend(event(new_id, 0, &p));
                        }
                    }
                    "wl_seat" => {
                        let mut p = Vec::new();
                        p_u32(&mut p, 3);
                        out.extend(event(new_id, 0, &p));
                        let mut n = Vec::new();
                        p_str(&mut n, "seat0");
                        out.extend(event(new_id, 1, &n));
                        self.with_state(|st| st.seat = Some(new_id));
                    }
                    "wl_output" => {
                        self.output_obj = Some(new_id);
                        out.extend(output_events(new_id));
                    }
                    _ => {}
                }
                self.objects.insert(new_id, ifc);
            }

            ("wl_compositor", 0) => {
                self.objects.insert(u32le(payload, 0), "wl_surface".into());
            }
            ("wl_compositor", 1) => {
                self.objects.insert(u32le(payload, 0), "wl_region".into());
            }

            ("wl_shm", 0) => {

                let pool = u32le(payload, 0);
                let size = u32le(payload, 4) as usize;
                self.objects.insert(pool, "wl_shm_pool".into());
                if !self.pending.is_empty() {
                    let fd = self.pending.remove(0);
                    if let Ok(m) = sys::Mmap::map_read(fd.as_raw_fd(), size) {
                        let m = Arc::new(m);
                        self.with_state(|st| {
                            st.pools.insert(pool, m);
                        });
                    }
                }
            }
            ("wl_shm_pool", 0) => {

                let id = u32le(payload, 0);
                self.objects.insert(id, "wl_buffer".into());
                let info = BufferInfo {
                    pool: sender,
                    offset: u32le(payload, 4) as i32,
                    width: u32le(payload, 8) as i32,
                    height: u32le(payload, 12) as i32,
                    stride: u32le(payload, 16) as i32,
                    format: u32le(payload, 20),
                };
                self.with_state(|st| {
                    st.buffers.insert(id, info);
                });
            }

            ("wl_seat", 0) => {

                let id = u32le(payload, 0);
                self.objects.insert(id, "wl_pointer".into());
                self.with_state(|st| {
                    st.pointer = Some(id);
                    if !st.pointers.contains(&id) {
                        st.pointers.push(id);
                    }
                });
            }
            ("wl_seat", 1) => {

                let id = u32le(payload, 0);
                self.objects.insert(id, "wl_keyboard".into());
                self.with_state(|st| {
                    st.keyboard = Some(id);
                    if !st.keyboards.contains(&id) {
                        st.keyboards.push(id);
                    }
                });
                let mut km = keymap_string().into_bytes();
                km.push(0);
                if let Ok(fd) = sys::memfd_with(&km) {
                    let mut p = Vec::new();
                    p_u32(&mut p, 1);
                    p_u32(&mut p, km.len() as u32);
                    out.extend(event(id, 0, &p));
                    let mut r = Vec::new();
                    p_i32(&mut r, 0);
                    p_i32(&mut r, 0);
                    out.extend(event(id, 5, &r));
                    fd_out = Some(fd);
                }
            }
            ("wl_seat", 2) => {
                self.objects.insert(u32le(payload, 0), "wl_touch".into());
            }

            ("wl_pointer", 1) => {
                self.with_state(|st| {
                    st.pointers.retain(|&p| p != sender);
                    if st.pointer == Some(sender) {
                        st.pointer = st.pointers.last().copied();
                    }
                });
            }

            ("wl_keyboard", 0) => {
                self.with_state(|st| {
                    st.keyboards.retain(|&k| k != sender);
                    if st.keyboard == Some(sender) {
                        st.keyboard = st.keyboards.last().copied();
                    }
                });
            }

            ("xdg_wm_base", 1) => {
                self.objects.insert(u32le(payload, 0), "xdg_positioner".into());
            }
            ("xdg_wm_base", 2) => {

                let xs = u32le(payload, 0);
                let surf = u32le(payload, 4);
                self.objects.insert(xs, "xdg_surface".into());
                self.surface_xdg.insert(surf, xs);
            }

            ("xdg_surface", 1) => {

                let tl = u32le(payload, 0);
                self.objects.insert(tl, "xdg_toplevel".into());
                self.xdg_toplevel.insert(sender, tl);
                if let Some(surf) = self.surface_xdg.iter().find(|(_, &x)| x == sender).map(|(&s, _)| s) {
                    self.with_state(|st| {
                        st.popup_surf.remove(&surf);
                        st.surface = Some(surf);
                    });
                }
            }
            ("xdg_surface", 2) => {

                self.objects.insert(u32le(payload, 0), "xdg_popup".into());
                if let Some(surf) = self.surface_xdg.iter().find(|(_, &x)| x == sender).map(|(&s, _)| s) {
                    self.with_state(|st| {
                        st.popup_surf.insert(surf);
                    });
                }
            }

            ("xdg_toplevel", 2) => {
                let t = read_str(payload, 0);
                self.with_state(|st| st.title = Some(t));
            }
            ("xdg_toplevel", 3) => {
                let a = read_str(payload, 0);
                self.with_state(|st| st.app_id = Some(a));
            }

            ("wl_surface", 1) => {

                let b = u32le(payload, 0);
                self.with_state(|st| {
                    if b == 0 {
                        st.pending_attach.remove(&sender);
                    } else {
                        st.pending_attach.insert(sender, b);
                    }
                });
            }
            ("wl_surface", 2) => {

                let x = u32le(payload, 0) as i32;
                let y = u32le(payload, 4) as i32;
                let w = u32le(payload, 8) as i32;
                let h = u32le(payload, 12) as i32;
                if w > 0 && h > 0 {
                    self.with_state(|st| {
                        st.surf_damage.entry(sender).or_default().push((x, y, w, h));
                    });
                }
            }
            ("wl_surface", 9) => {

                let x = u32le(payload, 0) as i32;
                let y = u32le(payload, 4) as i32;
                let w = u32le(payload, 8) as i32;
                let h = u32le(payload, 12) as i32;
                if w > 0 && h > 0 {
                    self.with_state(|st| {
                        st.surf_damage.entry(sender).or_default().push((x, y, w, h));
                    });
                }
            }
            ("wl_surface", 3) => {

                let cb = u32le(payload, 0);
                self.objects.insert(cb, "wl_callback".into());
                self.frames.lock().unwrap().push(cb);
            }
            ("wl_surface", 6) => {

                if let Some(&xs) = self.surface_xdg.get(&sender) {
                    if !self.configured.contains(&xs) {

                        self.configured.insert(xs);
                        if let Some(&tl) = self.xdg_toplevel.get(&xs) {
                            let (w, h) = screen_wh();
                            let mut p = Vec::new();
                            p_i32(&mut p, w);
                            p_i32(&mut p, h);

                            p_u32(&mut p, 8);
                            p_u32(&mut p, 1);
                            p_u32(&mut p, 4);
                            out.extend(event(tl, 0, &p));
                        }
                        let serial = self.next_serial();
                        let mut p = Vec::new();
                        p_u32(&mut p, serial);
                        out.extend(event(xs, 0, &p));
                        return (out, None);
                    }
                }

                let (pending, prev) = self
                    .with_state(|st| {
                        (
                            st.pending_attach.get(&sender).copied(),
                            st.surf_buffer.get(&sender).copied(),
                        )
                    })
                    .unwrap_or((None, None));
                if let Some(b) = pending {
                    if let Some(prev) = prev {
                        if prev != b {
                            out.extend(event(prev, 0, &[]));
                        }
                    }
                    self.with_state(|st| {
                        st.surf_buffer.insert(sender, b);
                    });

                    if let Some(d) = &self.damage {
                        d.mark();
                    }
                    if !self.entered.contains(&sender) {
                        if let Some(o) = self.output_obj {
                            self.entered.insert(sender);
                            let mut p = Vec::new();
                            p_u32(&mut p, o);
                            out.extend(event(sender, 0, &p));
                        }
                    }
                }
            }

            ("xwayland_shell_v1", 1) => {

                let id = u32le(payload, 0);
                let surf = u32le(payload, 4);
                self.objects.insert(id, "xwayland_surface_v1".into());
                self.xwl_surf.insert(id, surf);
                eprintln!("[h{}] xwayland_surface_v1 #{id} wraps wl_surface #{surf}", self.cid);
            }
            ("xwayland_surface_v1", 0) => {

                let lo = u32le(payload, 0) as u64;
                let hi = u32le(payload, 4) as u64;
                let serial = lo | (hi << 32);
                if let Some(&surf) = self.xwl_surf.get(&sender) {
                    crate::xwl_associate(&self.shared, serial, surf, self.cid);
                }
            }
            ("xwayland_surface_v1", 1) => {

                if let Some(surf) = self.xwl_surf.remove(&sender) {
                    crate::xwl_surface_gone(&self.shared, self.cid, surf);
                }
            }
            ("wl_surface", 0) => {

                crate::xwl_surface_gone(&self.shared, self.cid, sender);
            }

            ("wl_data_device_manager", 0) => {
                self.objects.insert(u32le(payload, 0), "wl_data_source".into());
            }
            ("wl_data_device_manager", 1) => {
                self.objects.insert(u32le(payload, 0), "wl_data_device".into());
            }

            ("wl_subcompositor", 1) => {
                let sub_id = u32le(payload, 0);
                let surf = u32le(payload, 4);
                let parent = u32le(payload, 8);
                self.objects.insert(sub_id, "wl_subsurface".into());
                self.with_state(|st| {
                    st.subsurface_obj.insert(sub_id, surf);
                    st.subsurf_parent.insert(surf, parent);
                    st.subsurf_pos.entry(surf).or_insert((0, 0));
                });
            }

            ("wl_subsurface", 1) => {
                let x = u32le(payload, 0) as i32;
                let y = u32le(payload, 4) as i32;
                self.with_state(|st| {
                    if let Some(&surf) = st.subsurface_obj.get(&sender) {
                        st.subsurf_pos.insert(surf, (x, y));
                    }
                });
            }

            ("wl_subsurface", 0) => {
                self.with_state(|st| {
                    if let Some(surf) = st.subsurface_obj.remove(&sender) {
                        st.subsurf_parent.remove(&surf);
                        st.subsurf_pos.remove(&surf);
                    }
                });
            }
            _ => {}
        }
        (out, fd_out)
    }
}

fn output_events(id: u32) -> Vec<u8> {
    let mut out = Vec::new();
    let mut g = Vec::new();
    p_i32(&mut g, 0);
    p_i32(&mut g, 0);
    p_i32(&mut g, 300);
    p_i32(&mut g, 200);
    p_i32(&mut g, 0);
    p_str(&mut g, "phantom");
    p_str(&mut g, "headless");
    p_i32(&mut g, 0);
    out.extend(event(id, 0, &g));

    let mut m = Vec::new();
    p_u32(&mut m, 3);
    let (ow, oh) = screen_wh();
    p_i32(&mut m, ow);
    p_i32(&mut m, oh);
    p_i32(&mut m, 60000);
    out.extend(event(id, 1, &m));

    let mut s = Vec::new();
    p_i32(&mut s, 1);
    out.extend(event(id, 3, &s));

    out.extend(event(id, 2, &[]));
    out
}
