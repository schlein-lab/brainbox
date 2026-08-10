use std::collections::VecDeque;
use std::io::{Read, Write};
use std::os::fd::FromRawFd;
use std::os::unix::net::UnixStream;
use std::process::{Child, Command};
use std::sync::mpsc;
use std::time::Duration;

extern "C" {
    fn pipe(fds: *mut i32) -> i32;
    fn socketpair(domain: i32, ty: i32, proto: i32, sv: *mut i32) -> i32;
    fn fcntl(fd: i32, cmd: i32, arg: i32) -> i32;
    fn close(fd: i32) -> i32;
}
const AF_UNIX: i32 = 1;
const SOCK_STREAM: i32 = 1;
const F_SETFD: i32 = 2;
const FD_CLOEXEC: i32 = 1;

pub struct XServer {
    pub display: u32,
    pub child: Child,
    pub rootless: bool,
}

impl Drop for XServer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub fn spawn(wayland_display: &str, runtime: &str) -> std::io::Result<XServer> {
    spawn_inner(wayland_display, runtime, false)
}

pub fn spawn_rootless(wayland_display: &str, runtime: &str) -> std::io::Result<XServer> {
    spawn_inner(wayland_display, runtime, true)
}

fn spawn_inner(wayland_display: &str, runtime: &str, rootless: bool) -> std::io::Result<XServer> {

    let mut fds = [0i32; 2];
    if unsafe { pipe(fds.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let (rd, wr) = (fds[0], fds[1]);

    let mut cmd = Command::new("Xwayland");
    cmd.arg("-displayfd")
        .arg(wr.to_string())

        .arg("-shm")
        .arg("-noreset")

        .arg("-ac")
        .env("WAYLAND_DISPLAY", wayland_display)
        .env("XDG_RUNTIME_DIR", runtime);

    let mut wm_phantom: i32 = -1;
    let mut wm_child: i32 = -1;
    if rootless {
        let mut sv = [0i32; 2];
        if unsafe { socketpair(AF_UNIX, SOCK_STREAM, 0, sv.as_mut_ptr()) } != 0 {
            unsafe {
                close(rd);
                close(wr);
            }
            return Err(std::io::Error::last_os_error());
        }
        wm_phantom = sv[0];
        wm_child = sv[1];
        unsafe { fcntl(wm_phantom, F_SETFD, FD_CLOEXEC) };
        cmd.arg("-rootless").arg("-wm").arg(wm_child.to_string());
    }

    let spawn_res = cmd.spawn();

    unsafe { close(wr) };
    if rootless {
        unsafe { close(wm_child) };
    }

    let child = match spawn_res {
        Ok(c) => c,
        Err(e) => {
            unsafe { close(rd) };
            if rootless {
                unsafe { close(wm_phantom) };
            }
            return Err(std::io::Error::new(
                e.kind(),
                format!("could not start Xwayland ({e}). Install it (e.g. `apt install xwayland`)."),
            ));
        }
    };

    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut f = unsafe { std::fs::File::from_raw_fd(rd) };
        let mut s = String::new();
        let mut byte = [0u8; 1];
        while let Ok(1) = f.read(&mut byte) {
            if byte[0] == b'\n' {
                break;
            }
            s.push(byte[0] as char);
            if s.len() > 16 {
                break;
            }
        }
        let _ = tx.send(s);
    });

    let reported = rx.recv_timeout(Duration::from_secs(10)).unwrap_or_default();
    let display = match reported.trim().parse::<u32>() {
        Ok(n) => n,
        Err(_) => {
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
            if rootless {
                unsafe { close(wm_phantom) };
            }
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                format!("Xwayland did not report a display number (got {reported:?})"),
            ));
        }
    };

    let rt = runtime.to_string();
    if rootless {
        std::thread::spawn(move || run_xwm(wm_phantom, rt));
    } else {
        std::thread::spawn(move || connect_and_manage(display, rt));
    }

    Ok(XServer { display, child, rootless })
}

fn run_xwm(fd: i32, runtime: String) {
    manage(unsafe { UnixStream::from_raw_fd(fd) }, runtime, true);
}

fn connect_and_manage(display: u32, runtime: String) {
    let path = format!("/tmp/.X11-unix/X{display}");
    for _ in 0..50 {
        if std::path::Path::new(&path).exists() {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    match UnixStream::connect(&path) {
        Ok(s) => manage(s, runtime, false),
        Err(e) => eprintln!("phantom-xwm: cannot connect to X display :{display} ({e})"),
    }
}

fn report_title(runtime: &str, serial: u64, title: &str) {
    let path = format!("{runtime}/phantom.ctl");
    if let Ok(mut s) = UnixStream::connect(&path) {
        let _ = s.write_all(format!("xwin {serial} {title}\n").as_bytes());
        let _ = s.shutdown(std::net::Shutdown::Write);
        let mut buf = String::new();
        let _ = s.read_to_string(&mut buf);
    }
}

fn manage(mut s: UnixStream, runtime: String, rootless: bool) {

    let setup: [u8; 12] = [0x6c, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    if s.write_all(&setup).is_err() {
        return;
    }

    let mut hdr = [0u8; 8];
    if s.read_exact(&mut hdr).is_err() {
        return;
    }
    if hdr[0] != 1 {
        eprintln!("phantom-xwm: X11 setup not accepted (code {})", hdr[0]);
        return;
    }
    let addlen = u16::from_le_bytes([hdr[6], hdr[7]]) as usize * 4;
    let mut body = vec![0u8; addlen];
    if s.read_exact(&mut body).is_err() || body.len() < 32 {
        eprintln!("phantom-xwm: short X11 setup body");
        return;
    }

    let vendor_len = u16::from_le_bytes([body[16], body[17]]) as usize;
    let num_formats = body[21] as usize;
    let screen0 = 32 + ((vendor_len + 3) & !3) + 8 * num_formats;
    if screen0 + 4 > body.len() {
        eprintln!("phantom-xwm: could not locate root window in setup");
        return;
    }
    let root = u32::from_le_bytes([
        body[screen0],
        body[screen0 + 1],
        body[screen0 + 2],
        body[screen0 + 3],
    ]);

    let mut pending: VecDeque<[u8; 32]> = VecDeque::new();

    if rootless {
        match query_extension(&mut s, "Composite", &mut pending) {
            Some(major) => {

                let _ = s.write_all(&x_ext_req(major, 0, &[0, 4]));
                let _ = read_reply(&mut s, &mut pending);

                let _ = s.write_all(&x_ext_req(major, 2, &[root, 1]));
                eprintln!("phantom-xwm: Composite manual-redirect on root 0x{root:x} (opcode {major})");
            }
            None => {
                eprintln!("phantom-xwm: WARNING — Composite extension unavailable; rootless windows will not present");
            }
        }
    }

    let _ = s.write_all(&x_req(2, 0, &[root, 0x800, 0x0018_0000]));
    eprintln!("phantom-xwm: managing X root 0x{root:x} (map + input focus)");

    let serial_atom = intern_atom(&mut s, "WL_SURFACE_SERIAL", &mut pending);
    let net_wm_name = intern_atom(&mut s, "_NET_WM_NAME", &mut pending);
    const WM_NAME: u32 = 39;

    loop {
        let ev = match pending.pop_front() {
            Some(e) => e,
            None => {
                let mut e = [0u8; 32];
                if s.read_exact(&mut e).is_err() {
                    break;
                }
                e
            }
        };
        if std::env::var("PHANTOM_XWM_DEBUG").is_ok() {
            eprintln!("phantom-xwm: event type {}", ev[0] & 0x7f);
        }
        match ev[0] & 0x7f {
            0 => eprintln!("phantom-xwm: X error (code {} op {}:{})", ev[1], ev[10], ev[8]),
            20 => {

                let w = u32::from_le_bytes([ev[8], ev[9], ev[10], ev[11]]);
                let _ = s.write_all(&x_req(8, 0, &[w]));
            }
            19 => {

                let w = u32::from_le_bytes([ev[8], ev[9], ev[10], ev[11]]);
                let _ = s.write_all(&x_req(42, 1, &[w, 0]));
            }
            23 => {

                let _ = s.write_all(&configure_from_request(&ev));
            }
            33 => {

                let win = u32::from_le_bytes([ev[4], ev[5], ev[6], ev[7]]);
                let atom = u32::from_le_bytes([ev[8], ev[9], ev[10], ev[11]]);
                if serial_atom != 0 && atom == serial_atom {
                    let lo = u32::from_le_bytes([ev[12], ev[13], ev[14], ev[15]]) as u64;
                    let hi = u32::from_le_bytes([ev[16], ev[17], ev[18], ev[19]]) as u64;
                    let serial = lo | (hi << 32);

                    let mut t = get_property(&mut s, win, net_wm_name, &mut pending);
                    if t.is_empty() {
                        t = get_property(&mut s, win, WM_NAME, &mut pending);
                    }
                    let title = String::from_utf8_lossy(&t).trim_matches('\0').trim().to_string();
                    if std::env::var("PHANTOM_XWM_DEBUG").is_ok() {
                        eprintln!("phantom-xwm: window 0x{win:x} serial={serial} title={title:?}");
                    }
                    report_title(&runtime, serial, &title);
                }
            }
            _ => {}
        }
    }
}

fn read_reply(s: &mut UnixStream, pending: &mut VecDeque<[u8; 32]>) -> Option<([u8; 32], Vec<u8>)> {
    for _ in 0..64 {
        let mut m = [0u8; 32];
        if s.read_exact(&mut m).is_err() {
            return None;
        }
        match m[0] {
            0 => {
                eprintln!("phantom-xwm: X error (code {} op {}:{})", m[1], m[10], m[8]);
                return Some((m, Vec::new()));
            }
            1 => {
                let extra = u32::from_le_bytes([m[4], m[5], m[6], m[7]]) as usize * 4;
                let mut tail = vec![0u8; extra];
                if extra > 0 && s.read_exact(&mut tail).is_err() {
                    return None;
                }
                return Some((m, tail));
            }
            _ => pending.push_back(m),
        }
    }
    None
}

fn intern_atom(s: &mut UnixStream, name: &str, pending: &mut VecDeque<[u8; 32]>) -> u32 {
    let nb = name.as_bytes();
    let n = nb.len();
    let pad = (4 - (n % 4)) % 4;
    let len_words = 2 + (n + pad) / 4;
    let mut req = Vec::with_capacity(len_words * 4);
    req.push(16);
    req.push(0);
    req.extend_from_slice(&(len_words as u16).to_le_bytes());
    req.extend_from_slice(&(n as u16).to_le_bytes());
    req.extend_from_slice(&[0, 0]);
    req.extend_from_slice(nb);
    req.extend(std::iter::repeat(0u8).take(pad));
    if s.write_all(&req).is_err() {
        return 0;
    }
    match read_reply(s, pending) {
        Some((m, _)) => u32::from_le_bytes([m[8], m[9], m[10], m[11]]),
        None => 0,
    }
}

fn get_property(s: &mut UnixStream, window: u32, property: u32, pending: &mut VecDeque<[u8; 32]>) -> Vec<u8> {
    if property == 0 {
        return Vec::new();
    }
    let mut req = Vec::with_capacity(24);
    req.push(20);
    req.push(0);
    req.extend_from_slice(&6u16.to_le_bytes());
    req.extend_from_slice(&window.to_le_bytes());
    req.extend_from_slice(&property.to_le_bytes());
    req.extend_from_slice(&0u32.to_le_bytes());
    req.extend_from_slice(&0u32.to_le_bytes());
    req.extend_from_slice(&256u32.to_le_bytes());
    if s.write_all(&req).is_err() {
        return Vec::new();
    }
    match read_reply(s, pending) {

        Some((m, tail)) => {
            let vlen = u32::from_le_bytes([m[16], m[17], m[18], m[19]]) as usize;
            let fmt = m[1] as usize;
            let bytes = vlen * (fmt / 8).max(1);
            tail.into_iter().take(bytes).collect()
        }
        None => Vec::new(),
    }
}

fn query_extension(s: &mut UnixStream, name: &str, pending: &mut VecDeque<[u8; 32]>) -> Option<u8> {
    let nb = name.as_bytes();
    let n = nb.len();
    let pad = (4 - (n % 4)) % 4;
    let len_words = 2 + (n + pad) / 4;
    let mut req = Vec::with_capacity(len_words * 4);
    req.push(98);
    req.push(0);
    req.extend_from_slice(&(len_words as u16).to_le_bytes());
    req.extend_from_slice(&(n as u16).to_le_bytes());
    req.extend_from_slice(&[0, 0]);
    req.extend_from_slice(nb);
    req.extend(std::iter::repeat(0u8).take(pad));
    if s.write_all(&req).is_err() {
        return None;
    }
    let (reply, _) = read_reply(s, pending)?;

    let present = reply[8] != 0;
    let major = reply[9];
    if present {
        Some(major)
    } else {
        None
    }
}

fn x_ext_req(major: u8, minor: u8, words: &[u32]) -> Vec<u8> {
    let len = (1 + words.len()) as u16;
    let mut v = Vec::with_capacity(len as usize * 4);
    v.push(major);
    v.push(minor);
    v.extend_from_slice(&len.to_le_bytes());
    for w in words {
        v.extend_from_slice(&w.to_le_bytes());
    }
    v
}

fn x_req(opcode: u8, data: u8, words: &[u32]) -> Vec<u8> {
    let len = (1 + words.len()) as u16;
    let mut v = Vec::with_capacity(len as usize * 4);
    v.push(opcode);
    v.push(data);
    v.extend_from_slice(&len.to_le_bytes());
    for w in words {
        v.extend_from_slice(&w.to_le_bytes());
    }
    v
}

fn configure_from_request(ev: &[u8; 32]) -> Vec<u8> {
    let window = u32::from_le_bytes([ev[8], ev[9], ev[10], ev[11]]);
    let x = i16::from_le_bytes([ev[16], ev[17]]) as i32 as u32;
    let y = i16::from_le_bytes([ev[18], ev[19]]) as i32 as u32;
    let w = u16::from_le_bytes([ev[20], ev[21]]) as u32;
    let h = u16::from_le_bytes([ev[22], ev[23]]) as u32;
    let bw = u16::from_le_bytes([ev[24], ev[25]]) as u32;

    let mut v = Vec::with_capacity(32);
    v.push(12);
    v.push(0);
    v.extend_from_slice(&8u16.to_le_bytes());
    v.extend_from_slice(&window.to_le_bytes());
    v.extend_from_slice(&0x1f_u32.to_le_bytes());
    v.extend_from_slice(&x.to_le_bytes());
    v.extend_from_slice(&y.to_le_bytes());
    v.extend_from_slice(&w.to_le_bytes());
    v.extend_from_slice(&h.to_le_bytes());
    v.extend_from_slice(&bw.to_le_bytes());
    v
}
