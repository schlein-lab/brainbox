use std::collections::{HashMap, HashSet};
use std::sync::atomic::Ordering;
use std::sync::{Mutex, OnceLock};

use crate::state::{ClientState, Shared, CLIENT_SEQ};

#[derive(Default)]
struct XwlReg {

    wl: HashMap<u64, (u32, u64)>,

    vcid: HashMap<u64, u64>,

    by_surface: HashMap<(u64, u32), u64>,
}

static XREG: OnceLock<Mutex<XwlReg>> = OnceLock::new();

fn xreg() -> &'static Mutex<XwlReg> {
    XREG.get_or_init(|| Mutex::new(XwlReg::default()))
}

pub(crate) fn xwl_associate(shared: &Shared, serial: u64, surface: u32, parent_cid: u64) {
    if serial == 0 {
        return;
    }
    let mut reg = xreg().lock().unwrap();
    reg.wl.insert(serial, (surface, parent_cid));
    reg.by_surface.insert((parent_cid, surface), serial);
    if reg.vcid.contains_key(&serial) {
        return;
    }

    let mut g = shared.lock().unwrap();
    let Some(parent) = g.get(&parent_cid) else { return };
    let vst = ClientState {
        client_fd: parent.client_fd,
        writer: parent.writer.clone(),
        objects: HashMap::new(),
        pid: parent.pid,
        seat: parent.seat,
        keyboard: parent.keyboard,
        pointer: parent.pointer,
        pointers: parent.pointers.clone(),
        keyboards: parent.keyboards.clone(),
        surface: Some(surface),
        title: Some(format!("X11 window {serial}")),
        app_id: Some("xwayland".into()),
        serial: 1,
        time: 1,
        pools: HashMap::new(),
        buffers: HashMap::new(),
        pending_attach: HashMap::new(),
        surf_buffer: HashMap::new(),
        surf_damage: HashMap::new(),
        pend_damage: HashMap::new(),
        xdg_surf_wl: HashMap::new(),
        popup_surf: HashSet::new(),
        popup_objs: HashMap::new(),
        subsurface_obj: std::collections::HashMap::new(),
        subsurf_parent: std::collections::HashMap::new(),
        subsurf_pos: std::collections::HashMap::new(),
        keylog: String::new(),
        xparent: Some(parent_cid),
        commits: 0,
    };
    let vcid = CLIENT_SEQ.fetch_add(1, Ordering::Relaxed);
    g.insert(vcid, vst);
    reg.vcid.insert(serial, vcid);
    eprintln!("[h{parent_cid}] X11 window serial={serial} surface=#{surface} → virtual cid={vcid}");
    let pid = g.get(&vcid).and_then(|s| s.pid).unwrap_or(-1);
    crate::winbus::melde(&format!(
        "\"ereignis\":\"window_added\",\"cid\":{vcid},\"traeger\":{parent_cid},\"pid\":{pid},\"x11\":true"
    ));
}

pub(crate) fn xwl_set_title(shared: &Shared, serial: u64, title: &str) -> bool {
    let vcid = { xreg().lock().unwrap().vcid.get(&serial).copied() };
    if let Some(vcid) = vcid {
        if let Some(st) = shared.lock().unwrap().get_mut(&vcid) {
            if !title.is_empty() {
                st.title = Some(title.to_string());
                crate::winbus::melde(&format!(
                    "\"ereignis\":\"title_changed\",\"cid\":{vcid},\"x11\":true,\"titel\":\"{}\"",
                    crate::winbus::json_str(title)
                ));
            }
            return true;
        }
    }
    false
}


pub(crate) fn xwl_set_meta(shared: &Shared, serial: u64, pid: Option<i32>, klasse: &str) -> bool {
    let vcid = { xreg().lock().unwrap().vcid.get(&serial).copied() };
    if let Some(vcid) = vcid {
        if let Some(st) = shared.lock().unwrap().get_mut(&vcid) {
            if let Some(p) = pid {
                if p > 0 {
                    st.pid = Some(p);
                }
            }
            if !klasse.is_empty() {
                st.app_id = Some(klasse.to_string());
            }
            crate::winbus::melde(&format!(
                "\"ereignis\":\"window_meta\",\"cid\":{vcid},\"x11\":true,\"pid\":{},\"app\":\"{}\"",
                pid.unwrap_or(-1),
                crate::winbus::json_str(klasse)
            ));
            return true;
        }
    }
    false
}

pub(crate) fn xwl_surface_gone(shared: &Shared, parent_cid: u64, surface: u32) {
    let mut reg = xreg().lock().unwrap();
    let Some(serial) = reg.by_surface.remove(&(parent_cid, surface)) else { return };
    reg.wl.remove(&serial);
    if let Some(vcid) = reg.vcid.remove(&serial) {
        shared.lock().unwrap().remove(&vcid);
        eprintln!("[h{parent_cid}] X11 window serial={serial} closed → drop virtual cid={vcid}");
    }
}

pub(crate) fn xwl_parent_gone(shared: &Shared, parent_cid: u64) {
    let mut reg = xreg().lock().unwrap();
    let serials: Vec<u64> = reg
        .wl
        .iter()
        .filter(|(_, &(_, p))| p == parent_cid)
        .map(|(&s, _)| s)
        .collect();
    if serials.is_empty() {
        return;
    }
    let mut g = shared.lock().unwrap();
    for s in serials {
        reg.wl.remove(&s);
        if let Some(vcid) = reg.vcid.remove(&s) {
            g.remove(&vcid);
        }
    }
    reg.by_surface.retain(|&(p, _), _| p != parent_cid);
}

pub(crate) fn window_resources(g: &HashMap<u64, ClientState>, cid: u64) -> Result<(u64, u32), String> {
    let st = g.get(&cid).ok_or("no such client")?;
    let surface = st.surface.ok_or("client has no surface")?;
    match st.xparent {
        Some(p) if g.contains_key(&p) => Ok((p, surface)),
        Some(_) => Err("X window's Xwayland connection is gone".into()),
        None => Ok((cid, surface)),
    }
}

pub(crate) fn cid_ready(g: &HashMap<u64, ClientState>, cid: u64) -> bool {
    let Some(st) = g.get(&cid) else { return false };
    if st.surface.is_none() {
        return false;
    }
    match st.xparent {
        Some(p) => g.get(&p).map(|pp| pp.keyboard.is_some()).unwrap_or(false),
        None => st.keyboard.is_some(),
    }
}
