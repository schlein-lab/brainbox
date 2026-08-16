


use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::{Mutex, OnceLock};

use crate::state::{ClientState, Shared, CLIENT_SEQ};

#[derive(Default)]
struct WinReg {
    
    vcid: HashMap<(u64, u32), u64>,
    
    
    
    
    
    
    fokus: HashMap<u64, u32>,
}

static WREG: OnceLock<Mutex<WinReg>> = OnceLock::new();

fn wreg() -> &'static Mutex<WinReg> {
    WREG.get_or_init(|| Mutex::new(WinReg::default()))
}


pub(crate) fn window_added(shared: &Shared, conn_cid: u64, surface: u32) {
    let mut reg = wreg().lock().unwrap();
    let mut g = shared.lock().unwrap();
    let Some(conn) = g.get_mut(&conn_cid) else { return };

    match conn.surface {
        None => {
            conn.surface = Some(surface);
            return;
        }
        Some(s) if s == surface => return,
        Some(_) => {}
    }

    if reg.vcid.contains_key(&(conn_cid, surface)) {
        return;
    }

    
    
    let vst = ClientState {
        client_fd: conn.client_fd,
        writer: conn.writer.clone(),
        objects: HashMap::new(),
        pid: conn.pid,
        seat: conn.seat,
        keyboard: conn.keyboard,
        pointer: conn.pointer,
        pointers: conn.pointers.clone(),
        keyboards: conn.keyboards.clone(),
        surface: Some(surface),
        title: None,
        app_id: conn.app_id.clone(),
        serial: 1,
        time: 1,
        pools: HashMap::new(),
        buffers: HashMap::new(),
        pending_attach: HashMap::new(),
        surf_buffer: HashMap::new(),
        surf_damage: HashMap::new(),
        pend_damage: HashMap::new(),
        xdg_surf_wl: HashMap::new(),
        popup_surf: Default::default(),
        popup_objs: Default::default(),
        subsurface_obj: HashMap::new(),
        subsurf_parent: HashMap::new(),
        subsurf_pos: HashMap::new(),
        keylog: String::new(),
        xparent: Some(conn_cid),
        commits: 0,
    };
    let vcid = CLIENT_SEQ.fetch_add(1, Ordering::Relaxed);
    g.insert(vcid, vst);
    reg.vcid.insert((conn_cid, surface), vcid);
    eprintln!("[h{conn_cid}] weiteres Fenster surface=#{surface} -> eigenes Ziel cid={vcid}");
    let pid = g.get(&vcid).and_then(|s| s.pid).unwrap_or(-1);
    crate::winbus::melde(&format!(
        "\"ereignis\":\"window_added\",\"cid\":{vcid},\"traeger\":{conn_cid},\"pid\":{pid}"
    ));
}


pub(crate) fn cid_for_surface(conn_cid: u64, surface: u32) -> u64 {
    wreg()
        .lock()
        .unwrap()
        .vcid
        .get(&(conn_cid, surface))
        .copied()
        .unwrap_or(conn_cid)
}


pub(crate) fn window_gone(shared: &Shared, conn_cid: u64, surface: u32) {
    let mut reg = wreg().lock().unwrap();
    if let Some(vcid) = reg.vcid.remove(&(conn_cid, surface)) {
        shared.lock().unwrap().remove(&vcid);
        eprintln!("[h{conn_cid}] Fenster surface=#{surface} zu -> Ziel cid={vcid} faellt weg");
        crate::winbus::melde(&format!("\"ereignis\":\"window_gone\",\"cid\":{vcid}"));
        if crate::compositor::focus_override_tot(vcid) {
            eprintln!("[h{conn_cid}] focus-override lag auf toter cid={vcid} -> auto (B14)");
            crate::winbus::melde(&format!(
                "\"ereignis\":\"focus_auto_heilung\",\"cid\":{vcid}"
            ));
        }
        return;
    }

    let mut g = shared.lock().unwrap();
    if g.get(&conn_cid).map(|st| st.surface) != Some(Some(surface)) {
        return;
    }
    
    
    
    let mut andere: Vec<(u64, u32)> = reg
        .vcid
        .iter()
        .filter(|((c, _), _)| *c == conn_cid)
        .map(|(&(_, s), &v)| (v, s))
        .collect();
    andere.sort_unstable();
    match andere.first().copied() {
        Some((v, s)) => {
            let erbe = g.remove(&v);
            reg.vcid.remove(&(conn_cid, s));
            crate::compositor::focus_override_tot(v);
            if let Some(conn) = g.get_mut(&conn_cid) {
                conn.surface = Some(s);
                conn.title = erbe.as_ref().and_then(|e| e.title.clone());
                if let Some(a) = erbe.and_then(|e| e.app_id) {
                    conn.app_id = Some(a);
                }
            }
            eprintln!("[h{conn_cid}] Hauptfenster zu -> surface=#{s} rueckt nach, cid={v} faellt weg");
            crate::winbus::melde(&format!("\"ereignis\":\"window_gone\",\"cid\":{v}"));
        }
        None => {
            if let Some(conn) = g.get_mut(&conn_cid) {
                conn.surface = None;
                conn.title = None;
            }
            eprintln!("[h{conn_cid}] letztes Fenster zu -> kein Ziel mehr");
            crate::winbus::melde(&format!("\"ereignis\":\"window_gone\",\"cid\":{conn_cid}"));
        }
    }
}


pub(crate) fn fokus_wechsel(conn_cid: u64, neu: u32) -> Option<u32> {
    let mut reg = wreg().lock().unwrap();
    match reg.fokus.insert(conn_cid, neu) {
        Some(alt) if alt != neu => Some(alt),
        _ => None,
    }
}


pub(crate) fn fokus_loeschen(conn_cid: u64) {
    wreg().lock().unwrap().fokus.remove(&conn_cid);
}


pub(crate) fn conn_gone(shared: &Shared, conn_cid: u64) {
    let mut reg = wreg().lock().unwrap();
    reg.fokus.remove(&conn_cid);
    let weg: Vec<((u64, u32), u64)> = reg
        .vcid
        .iter()
        .filter(|((c, _), _)| *c == conn_cid)
        .map(|(&k, &v)| (k, v))
        .collect();
    if weg.is_empty() {
        return;
    }
    let mut g = shared.lock().unwrap();
    for (k, v) in weg {
        reg.vcid.remove(&k);
        g.remove(&v);
        crate::compositor::focus_override_tot(v);
    }
}
