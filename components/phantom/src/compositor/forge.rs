use super::consts::{AXIS_NOTCH, BTN_LEFT, BTN_RIGHT, BTN_MIDDLE};
use crate::Shared;
use phantom::sys;
use phantom::wire::{event, p_i32, p_u32};

pub(crate) fn modbit(code: u16) -> u32 {
    match code {
        42 | 54 => 1,
        29 | 97 => 4,
        56 => 8,
        100 => 128,
        _ => 0,
    }
}


fn traeger(g: &std::collections::HashMap<u64, crate::ClientState>, cid: u64) -> Option<(u64, u32)> {
    let st = g.get(&cid)?;
    let surface = st.surface?;
    let conn = match st.xparent {
        Some(p) if g.contains_key(&p) => p,
        Some(_) => return None,
        None => cid,
    };
    Some((conn, surface))
}

pub(crate) fn forge_enter(shared: &Shared, cid: u64) {
    let mut g = shared.lock().unwrap();
    let Some((conn, surface)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let kbds = kbd_ids(st);
    if kbds.is_empty() {
        return;
    }
    let serial = st.serial;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, serial);
    p_u32(&mut p, surface);
    p_u32(&mut p, 0);
    let mut out = Vec::new();
    for kbd in &kbds {
        out.extend(event(*kbd, 1, &p));
    }
    st.serial = serial + 1;
    crate::winreg::fokus_wechsel(conn, surface);
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn forge_leave(shared: &Shared, cid: u64) {
    let mut g = shared.lock().unwrap();
    let Some((conn, surface)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let kbds = kbd_ids(st);
    if kbds.is_empty() {
        return;
    }
    let serial = st.serial;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, serial);
    p_u32(&mut p, surface);
    let mut out = Vec::new();
    for kbd in &kbds {
        out.extend(event(*kbd, 2, &p));
    }
    st.serial = serial + 1;
    crate::winreg::fokus_loeschen(conn);
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn route_key(shared: &Shared, cid: u64, code: u16, down: bool, mask: u32, old_mask: u32) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let kbds = kbd_ids(st);
    if kbds.is_empty() {
        return;
    }
    let mut serial = st.serial;
    let time = st.time;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut out = Vec::new();
    for kbd in &kbds {
        if mask != old_mask {
            let mut p = Vec::new();
            p_u32(&mut p, serial);
            p_u32(&mut p, mask);
            p_u32(&mut p, 0);
            p_u32(&mut p, 0);
            p_u32(&mut p, 0);
            out.extend(event(*kbd, 4, &p));
            serial += 1;
        }
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, time);
        p_u32(&mut p, code as u32);
        p_u32(&mut p, if down { 1 } else { 0 });
        out.extend(event(*kbd, 3, &p));
        serial += 1;
    }
    st.serial = serial;
    st.time = time + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

#[inline]
pub(crate) fn fixed(v: f64) -> i32 {
    (v * 256.0).round() as i32
}

fn ptr_ids(st: &crate::ClientState) -> Vec<u32> {
    if st.pointers.is_empty() {
        st.pointer.into_iter().collect()
    } else {
        st.pointers.clone()
    }
}

fn kbd_ids(st: &crate::ClientState) -> Vec<u32> {
    if st.keyboards.is_empty() {
        st.keyboard.into_iter().collect()
    } else {
        st.keyboards.clone()
    }
}

pub(crate) fn pointer_enter(shared: &Shared, cid: u64, surface: u32, sx: f64, sy: f64) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let ptrs = ptr_ids(st);
    if ptrs.is_empty() {
        return;
    }
    let serial = st.serial;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, serial);
    p_u32(&mut p, surface);
    p_i32(&mut p, fixed(sx));
    p_i32(&mut p, fixed(sy));

    let mut out = Vec::new();
    for ptr in &ptrs {
        out.extend(event(*ptr, 0, &p));
        out.extend(event(*ptr, 5, &[]));
    }
    st.serial = serial + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn pointer_leave(shared: &Shared, cid: u64, surface: u32) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let ptrs = ptr_ids(st);
    if ptrs.is_empty() {
        return;
    }
    let serial = st.serial;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, serial);
    p_u32(&mut p, surface);
    let mut out = Vec::new();
    for ptr in &ptrs {
        out.extend(event(*ptr, 1, &p));
    }
    st.serial = serial + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn pointer_motion(shared: &Shared, cid: u64, sx: f64, sy: f64) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let ptrs = ptr_ids(st);
    if ptrs.is_empty() {
        return;
    }
    let time = st.time;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, time);
    p_i32(&mut p, fixed(sx));
    p_i32(&mut p, fixed(sy));
    let mut out = Vec::new();
    for ptr in &ptrs {
        out.extend(event(*ptr, 2, &p));
        out.extend(event(*ptr, 5, &[]));
    }
    st.time = time + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn pointer_button(shared: &Shared, cid: u64, code: u16, down: bool) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let ptrs = ptr_ids(st);
    if ptrs.is_empty() {
        return;
    }
    let serial = st.serial;
    let time = st.time;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, serial);
    p_u32(&mut p, time);
    p_u32(&mut p, code as u32);
    p_u32(&mut p, if down { 1 } else { 0 });
    let mut out = Vec::new();

    for ptr in ptrs.iter().take(1) {
        out.extend(event(*ptr, 3, &p));
        out.extend(event(*ptr, 5, &[]));
    }
    st.serial = serial + 1;
    st.time = time + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub(crate) fn pointer_axis(shared: &Shared, cid: u64, notches: i32) {
    let mut g = shared.lock().unwrap();
    let Some((conn, _)) = traeger(&g, cid) else { return };
    let Some(st) = g.get_mut(&conn) else { return };
    let ptrs = ptr_ids(st);
    if ptrs.is_empty() {
        return;
    }
    let time = st.time;
    let (writer, fd) = (st.writer.clone(), st.client_fd);
    let mut p = Vec::new();
    p_u32(&mut p, time);
    p_u32(&mut p, 0);
    p_i32(&mut p, fixed(-(notches as f64) * AXIS_NOTCH));
    let mut out = Vec::new();
    for ptr in &ptrs {
        out.extend(event(*ptr, 4, &p));
        out.extend(event(*ptr, 5, &[]));
    }
    st.time = time + 1;
    let _w = writer.lock().unwrap();
    let _ = sys::send_with_fds(fd, &out, &[]);
}

pub fn act_pointer(shared: &Shared, cid: u64, action: &str, args: &[&str]) -> Result<String, String> {

    let surface = {
        let g = shared.lock().unwrap();
        if g.get(&cid).is_none() {
            return Err(format!("no client cid={cid}"));
        }
        let (conn, surface) =
            traeger(&g, cid).ok_or_else(|| format!("cid={cid} has no surface"))?;
        if g.get(&conn).and_then(|st| st.pointer).is_none() {
            return Err(format!("cid={cid} has no wl_pointer (client never called get_pointer)"));
        }
        surface
    };
    let num = |i: usize| -> Result<f64, String> {
        let s = args.get(i).ok_or_else(|| "missing coordinate".to_string())?;
        s.parse::<f64>().map_err(|_| format!("bad number {s:?}"))
    };
    let button = |name: Option<&&str>| match name.copied() {
        Some("right") => BTN_RIGHT,
        Some("middle") => BTN_MIDDLE,
        _ => BTN_LEFT,
    };

    let target = |_x: f64, _y: f64| -> (u32, i32, i32) { (surface, 0, 0) };
    match action {
        "move" => {
            let (x, y) = (num(0)?, num(1)?);
            let (tgt, ox, oy) = target(x, y);
            let (lx, ly) = (x - ox as f64, y - oy as f64);
            pointer_enter(shared, cid, tgt, lx, ly);
            pointer_motion(shared, cid, lx, ly);
            Ok(format!("ok: moved to {x},{y} on cid={cid} surf={tgt}\n"))
        }
        "click" => {
            let (x, y) = (num(0)?, num(1)?);
            let btn = button(args.get(2));
            let (tgt, ox, oy) = target(x, y);
            let (lx, ly) = (x - ox as f64, y - oy as f64);
            pointer_enter(shared, cid, tgt, lx, ly);
            pointer_motion(shared, cid, lx, ly);
            pointer_button(shared, cid, btn, true);
            pointer_button(shared, cid, btn, false);
            Ok(format!("ok: clicked {x},{y} on cid={cid} surf={tgt}\n"))
        }
        "drag" => {
            let (x1, y1, x2, y2) = (num(0)?, num(1)?, num(2)?, num(3)?);
            let btn = button(args.get(4));

            let (tgt, ox, oy) = target(x1, y1);
            let (ox, oy) = (ox as f64, oy as f64);
            pointer_enter(shared, cid, tgt, x1 - ox, y1 - oy);
            pointer_motion(shared, cid, x1 - ox, y1 - oy);
            pointer_button(shared, cid, btn, true);

            let steps = 16;
            for i in 1..=steps {
                let t = i as f64 / steps as f64;
                pointer_motion(shared, cid, (x1 + (x2 - x1) * t) - ox, (y1 + (y2 - y1) * t) - oy);
            }
            pointer_button(shared, cid, btn, false);
            Ok(format!("ok: dragged {x1},{y1}->{x2},{y2} on cid={cid} surf={tgt}\n"))
        }
        "scroll" => {
            let (x, y, n) = (num(0)?, num(1)?, num(2)? as i32);
            let (tgt, ox, oy) = target(x, y);
            let (lx, ly) = (x - ox as f64, y - oy as f64);
            pointer_enter(shared, cid, tgt, lx, ly);
            pointer_motion(shared, cid, lx, ly);
            pointer_axis(shared, cid, n);
            Ok(format!("ok: scrolled {n} at {x},{y} on cid={cid} surf={tgt}\n"))
        }
        other => Err(format!("unknown pointer action {other:?} (move|click|drag|scroll)")),
    }
}
