use std::sync::{Mutex, OnceLock};

use phantom::input::{Modifier, VirtualInput};
use phantom::wire::{event, p_u32};
use phantom::{png, sys};

use crate::state::Shared;
use crate::xwl::window_resources;

static KBD: OnceLock<Mutex<Option<VirtualInput>>> = OnceLock::new();

pub(crate) fn with_kbd(f: impl FnOnce(&mut VirtualInput) -> std::io::Result<()>) -> String {
    let mut guard = KBD.get_or_init(|| Mutex::new(None)).lock().unwrap();
    if guard.is_none() {
        match VirtualInput::new() {
            Ok(vi) => *guard = Some(vi),
            Err(e) => {

                let hint = if e.kind() == std::io::ErrorKind::PermissionDenied {
                    " — /dev/uinput not writable by this user; install the udev rule \
                     (MODE 0666) or add the user to the 'input' group, then restart phantom-hub"
                } else {
                    ""
                };
                return format!("err: uinput unavailable: {e}{hint}\n");
            }
        }
    }
    let vi = guard.as_mut().unwrap();
    match f(vi) {
        Ok(()) => "ok\n".into(),
        Err(e) => format!("err: {e}\n"),
    }
}

fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

pub(crate) fn uid_to_name(uid: u32) -> Option<String> {
    let pw = std::fs::read_to_string("/etc/passwd").ok()?;
    for line in pw.lines() {
        let mut f = line.split(':');
        let name = f.next()?;
        let _ = f.next();
        if f.next()?.parse::<u32>().ok()? == uid {
            return Some(name.to_string());
        }
    }
    None
}

pub(crate) fn sense_text(shared: &Shared, cid: u64) -> String {
    use std::os::unix::fs::MetadataExt;
    let (token, app_pid) = {
        let g = shared.lock().unwrap();
        let Some(st) = g.get(&cid) else { return "error: no such client\n".into() };
        let token = st
            .app_id
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| st.title.clone())
            .unwrap_or_default();
        (token, st.pid)
    };
    if token.is_empty() {
        return "error: no app_id/title to address the a11y tree\n".into();
    }
    let tool = std::env::var("PHANTOM_A11Y").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        format!("{home}/uiapi/uiapi.py")
    });
    if !std::path::Path::new(&tool).exists() {
        return "note: text-sense needs the a11y reader (set PHANTOM_A11Y=path/to/uiapi.py)\n".into();
    }
    let inner = format!("python3 {} tree {} --text --max 400", shell_quote(&tool), shell_quote(&token));
    let my_uid = std::fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(0);
    let app_uid = app_pid
        .and_then(|p| std::fs::metadata(format!("/proc/{p}")).ok())
        .map(|m| m.uid());
    let mut cmd = if my_uid == 0 {
        if let Some(uid) = app_uid.filter(|&u| u != 0) {
            let user = uid_to_name(uid).unwrap_or_else(|| uid.to_string());
            let env = format!(
                "XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"
            );
            let mut c = std::process::Command::new("su");
            c.arg(user).arg("-c").arg(format!("{env} {inner}"));
            c
        } else {
            let mut c = std::process::Command::new("sh");
            c.arg("-c").arg(inner);
            c
        }
    } else {
        let mut c = std::process::Command::new("sh");
        c.arg("-c").arg(inner);
        c
    };
    match cmd.output() {
        Ok(o) if !o.stdout.is_empty() => String::from_utf8_lossy(&o.stdout).into_owned(),
        Ok(o) => {
            let err = String::from_utf8_lossy(&o.stderr);
            let last = err.lines().last().unwrap_or("no output");
            format!("note: a11y read empty ({last}). For VS Code, launch it with --force-renderer-accessibility for the full tree.\n")
        }
        Err(e) => format!("error: a11y read: {e}\n"),
    }
}

pub(crate) fn do_snapshot(shared: &Shared, cid: u64, path: Option<String>) -> Result<String, String> {
    let (mmap, info) = {
        let g = shared.lock().unwrap();

        let (rcid, surface) = window_resources(&g, cid)?;
        let st = &g[&rcid];
        let buf_id = *st
            .surf_buffer
            .get(&surface)
            .ok_or("surface has no committed buffer yet")?;
        let info = *st.buffers.get(&buf_id).ok_or("buffer geometry unknown")?;
        let mmap = st.pools.get(&info.pool).ok_or("buffer's pool not mapped")?.clone();
        (mmap, info)
    };
    if info.width <= 0 || info.height <= 0 || info.stride <= 0 {
        return Err("buffer has zero size".into());
    }
    let (w, h, stride, start) = (
        info.width as usize,
        info.height as usize,
        info.stride as usize,
        info.offset as usize,
    );
    let data = mmap.as_slice();
    let need = start + (h - 1) * stride + w * 4;
    if need > data.len() {
        return Err(format!("buffer exceeds pool ({need} > {} bytes)", data.len()));
    }

    let mut rgba = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        let row = start + y * stride;
        for x in 0..w {
            let p = row + x * 4;
            rgba.push(data[p + 2]);
            rgba.push(data[p + 1]);
            rgba.push(data[p]);
            rgba.push(if info.format == 1 { 255 } else { data[p + 3] });
        }
    }
    let bytes = png::encode_rgba(w as u32, h as u32, &rgba);
    let out = path.unwrap_or_else(|| format!("/tmp/phantom-snap-{cid}.png"));
    std::fs::write(&out, &bytes).map_err(|e| e.to_string())?;
    Ok(format!("{out} ({w}x{h})"))
}

pub(crate) fn do_inject(shared: &Shared, cid: u64, keys: &[(u16, Modifier)]) -> Result<usize, String> {
    let (rcid, kbds, surface, writer, client_fd, mut serial, mut time) = {
        let g = shared.lock().unwrap();

        let (rcid, surface) = window_resources(&g, cid)?;
        let st = &g[&rcid];

        let kbds: Vec<u32> = if st.keyboards.is_empty() {
            st.keyboard.into_iter().collect()
        } else {
            st.keyboards.iter().take(1).copied().collect()
        };
        if kbds.is_empty() {
            return Err("client has no keyboard yet".into());
        }
        (rcid, kbds, surface, st.writer.clone(), st.client_fd, st.serial, st.time)
    };

    let mut out: Vec<u8> = Vec::new();
    let mut count = 0usize;

    for &kbd in &kbds {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        serial += 1;
        p_u32(&mut p, surface);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 1, &p));
    }

    let modifiers = |out: &mut Vec<u8>, kbd: u32, serial: u32, depressed: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, depressed);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 4, &p));
    };
    let key = |out: &mut Vec<u8>, kbd: u32, serial: u32, time: u32, code: u16, state: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, time);
        p_u32(&mut p, code as u32);
        p_u32(&mut p, state);
        out.extend(event(kbd, 3, &p));
    };

    for &(code, modi) in keys {
        let mask = match modi {
            Modifier::None => 0,
            Modifier::Shift => 1,
            Modifier::AltGr => 128,
        };
        let (tp, tr) = (time, time + 1);
        for &kbd in &kbds {
            if mask != 0 {
                modifiers(&mut out, kbd, serial, mask);
                serial += 1;
            }
            key(&mut out, kbd, serial, tp, code, 1);
            serial += 1;
            key(&mut out, kbd, serial, tr, code, 0);
            serial += 1;
            if mask != 0 {
                modifiers(&mut out, kbd, serial, 0);
                serial += 1;
            }
        }
        time += 2;
        count += 1;
    }

    {
        let _g = writer.lock().unwrap();
        sys::send_with_fds(client_fd, &out, &[]).map_err(|e| e.to_string())?;
    }
    if let Some(st) = shared.lock().unwrap().get_mut(&rcid) {
        st.serial = serial;
        st.time = time;
    }
    Ok(count)
}

pub(crate) fn do_inject_keycode(shared: &Shared, cid: u64, code: u16, mods: u32) -> Result<(), String> {
    let (rcid, kbds, surface, writer, client_fd, mut serial, mut time) = {
        let g = shared.lock().unwrap();
        let (rcid, surface) = window_resources(&g, cid)?;
        let st = &g[&rcid];
        let kbds: Vec<u32> = if st.keyboards.is_empty() {
            st.keyboard.into_iter().collect()
        } else {
            st.keyboards.iter().take(1).copied().collect()
        };
        if kbds.is_empty() {
            return Err("client has no keyboard yet".into());
        }
        (rcid, kbds, surface, st.writer.clone(), st.client_fd, st.serial, st.time)
    };

    let mut out: Vec<u8> = Vec::new();

    for &kbd in &kbds {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        serial += 1;
        p_u32(&mut p, surface);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 1, &p));
    }

    let modifiers = |out: &mut Vec<u8>, kbd: u32, serial: u32, depressed: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, depressed);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 4, &p));
    };
    let key = |out: &mut Vec<u8>, kbd: u32, serial: u32, time: u32, code: u16, state: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, time);
        p_u32(&mut p, code as u32);
        p_u32(&mut p, state);
        out.extend(event(kbd, 3, &p));
    };

    let (tp, tr) = (time, time + 1);
    for &kbd in &kbds {
        if mods != 0 {
            modifiers(&mut out, kbd, serial, mods);
            serial += 1;
        }
        key(&mut out, kbd, serial, tp, code, 1);
        serial += 1;
        key(&mut out, kbd, serial, tr, code, 0);
        serial += 1;
        if mods != 0 {
            modifiers(&mut out, kbd, serial, 0);
            serial += 1;
        }
    }
    time += 2;

    {
        let _g = writer.lock().unwrap();
        sys::send_with_fds(client_fd, &out, &[]).map_err(|e| e.to_string())?;
    }
    if let Some(st) = shared.lock().unwrap().get_mut(&rcid) {
        st.serial = serial;
        st.time = time;
    }
    Ok(())
}
