use super::consts::MIN_SUBSTANTIAL;
use super::state::focused_override;
use crate::{BufferInfo, ClientState, Shared};
use phantom::sys;
use std::collections::HashMap;
use std::sync::Arc;


fn eigner(g: &HashMap<u64, ClientState>, cid: u64) -> Option<(&ClientState, u32)> {
    let st = g.get(&cid)?;
    let surface = st.surface?;
    match st.xparent {
        Some(p) => Some((g.get(&p)?, surface)),
        None => Some((st, surface)),
    }
}

pub(crate) fn beats(ov: Option<u64>, cand: u64, best: Option<u64>) -> bool {
    match best {
        None => true,
        Some(b) => {
            let (c_ov, b_ov) = (ov == Some(cand), ov == Some(b));
            if c_ov != b_ov {
                c_ov
            } else {
                cand > b
            }
        }
    }
}

fn surf_substantial(st: &ClientState, surf: u32, min: i32) -> bool {
    let Some(&buf_id) = st.surf_buffer.get(&surf) else { return false };
    let Some(&info) = st.buffers.get(&buf_id) else { return false };
    info.width >= min.max(1) && info.height >= min.max(1) && info.stride > 0
}

fn subtree_substantial(st: &ClientState, parent: u32, min: i32, depth: u32) -> bool {
    if depth > 8 {
        return false;
    }
    for (&child, &p) in st.subsurf_parent.iter() {
        if p != parent {
            continue;
        }
        if surf_substantial(st, child, min) || subtree_substantial(st, child, min, depth + 1) {
            return true;
        }
    }
    false
}

fn cid_has_content(g: &HashMap<u64, ClientState>, cid: u64, min: i32) -> bool {
    let Some((own, top)) = eigner(g, cid) else { return false };
    if own.popup_surf.contains(&top) {
        return false;
    }
    surf_substantial(own, top, min) || subtree_substantial(own, top, min, 0)
}

fn bestes_ziel(g: &HashMap<u64, ClientState>, min: i32) -> Option<u64> {
    let ov = focused_override();
    let mut best: Option<u64> = None;
    for &cid in g.keys() {
        if cid_has_content(g, cid, min) && beats(ov, cid, best) {
            best = Some(cid);
        }
    }
    best
}

pub(crate) fn principal_cid(shared: &Shared) -> Option<u64> {
    let g = shared.lock().unwrap();
    bestes_ziel(&g, MIN_SUBSTANTIAL)
}

pub fn foreground_cid(shared: &Shared) -> Option<u64> {
    principal_cid(shared)
}

pub(crate) fn focused_buffer_min(shared: &Shared, min: i32) -> Option<(Arc<sys::Mmap>, BufferInfo)> {
    let g = shared.lock().unwrap();
    let cid = bestes_ziel(&g, min)?;
    let (st, surface) = eigner(&g, cid)?;

    let &buf_id = st.surf_buffer.get(&surface)?;
    let &info = st.buffers.get(&buf_id)?;
    let mmap = st.pools.get(&info.pool)?;
    if info.width < min.max(1) || info.height < min.max(1) || info.stride <= 0 {
        return None;
    }
    let need = (info.offset as u64)
        .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
        .saturating_add((info.width as u64).saturating_mul(4));
    if need > mmap.len() as u64 {
        return None;
    }
    Some((mmap.clone(), info))
}

pub(crate) fn focused_buffer(shared: &Shared) -> Option<(Arc<sys::Mmap>, BufferInfo)> {
    focused_buffer_min(shared, MIN_SUBSTANTIAL)
}

pub(crate) fn buffer_for_cid(shared: &Shared, cid: u64) -> Option<(Arc<sys::Mmap>, BufferInfo)> {
    let g = shared.lock().unwrap();
    let (st, surface) = eigner(&g, cid)?;
    let buf_id = *st.surf_buffer.get(&surface)?;
    let info = *st.buffers.get(&buf_id)?;
    let mmap = st.pools.get(&info.pool)?;
    if info.width <= 0 || info.height <= 0 || info.stride <= 0 {
        return None;
    }
    let need = (info.offset as u64)
        .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
        .saturating_add((info.width as u64).saturating_mul(4));
    if need > mmap.len() as u64 {
        return None;
    }
    Some((mmap.clone(), info))
}

pub(crate) fn foreground_subsurfaces(shared: &Shared) -> Vec<(Arc<sys::Mmap>, BufferInfo, i32, i32)> {
    let g = shared.lock().unwrap();
    let Some(cid) = bestes_ziel(&g, MIN_SUBSTANTIAL) else { return Vec::new() };
    let Some((st, primary)) = eigner(&g, cid) else { return Vec::new() };
    let mut out = Vec::new();
    collect_subsurfaces(st, primary, 0, 0, &mut out, 0);
    out
}

pub(crate) struct SceneLayer {
    pub(crate) surface_id: u32,
    pub(crate) mmap: Arc<sys::Mmap>,
    pub(crate) info: BufferInfo,
    pub(crate) x: i32,
    pub(crate) y: i32,
}


pub(crate) fn scene_layers_for(shared: &Shared, cid: u64) -> Vec<SceneLayer> {
    let g = shared.lock().unwrap();
    let Ok((rcid, primary)) = crate::xwl::window_resources(&g, cid) else {
        return Vec::new();
    };
    let Some(st) = g.get(&rcid) else { return Vec::new() };
    let mut out: Vec<SceneLayer> = Vec::new();
    if let Some(&buf_id) = st.surf_buffer.get(&primary) {
        if let Some(&info) = st.buffers.get(&buf_id) {
            if info.width > 0 && info.height > 0 && info.stride > 0 {
                if let Some(mmap) = st.pools.get(&info.pool) {
                    let need = (info.offset as u64)
                        .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
                        .saturating_add((info.width as u64).saturating_mul(4));
                    if need <= mmap.len() as u64 {
                        out.push(SceneLayer { surface_id: primary, mmap: mmap.clone(), info, x: 0, y: 0 });
                    }
                }
            }
        }
    }
    collect_scene_layers(st, primary, 0, 0, &mut out, 0);
    out
}

pub(crate) fn foreground_scene_layers(shared: &Shared) -> Vec<SceneLayer> {
    let g = shared.lock().unwrap();
    let Some(cid) = bestes_ziel(&g, MIN_SUBSTANTIAL) else { return Vec::new() };
    let Some((st, primary)) = eigner(&g, cid) else { return Vec::new() };
    let mut out: Vec<SceneLayer> = Vec::new();

    if let Some(&buf_id) = st.surf_buffer.get(&primary) {
        if let Some(&info) = st.buffers.get(&buf_id) {
            if info.width > 0 && info.height > 0 && info.stride > 0 {
                if let Some(mmap) = st.pools.get(&info.pool) {
                    let need = (info.offset as u64)
                        .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
                        .saturating_add((info.width as u64).saturating_mul(4));
                    if need <= mmap.len() as u64 {
                        out.push(SceneLayer { surface_id: primary, mmap: mmap.clone(), info, x: 0, y: 0 });
                    }
                }
            }
        }
    }

    collect_scene_layers(st, primary, 0, 0, &mut out, 0);
    out
}


pub(crate) fn welt_scene_layers(shared: &Shared, fest: Option<(i32, i32)>) -> (Vec<SceneLayer>, i32, i32) {
    let g = shared.lock().unwrap();
    let ov = focused_override();
    let mut cids: Vec<u64> = g
        .keys()
        .copied()
        .filter(|&cid| cid_has_content(&g, cid, MIN_SUBSTANTIAL))
        .collect();
    cids.sort_unstable();
    if let Some(f) = ov {
        if let Some(pos) = cids.iter().position(|&c| c == f) {
            let f = cids.remove(pos);
            cids.push(f);
        }
    }
    let mut fenster: Vec<(u64, u32, i32, i32)> = Vec::new();
    let mut lw: i32 = 0;
    let mut lh: i32 = 0;
    for &cid in &cids {
        let Some((st, primary)) = eigner(&g, cid) else { continue };
        let Some(&buf_id) = st.surf_buffer.get(&primary) else { continue };
        let Some(&info) = st.buffers.get(&buf_id) else { continue };
        if info.width <= 0 || info.height <= 0 {
            continue;
        }
        lw = lw.max(info.width);
        lh = lh.max(info.height);
        fenster.push((cid, primary, info.width, info.height));
    }
    if let Some((fw, fh)) = fest {
        lw = fw;
        lh = fh;
    }
    if lw <= 0 || lh <= 0 || fenster.is_empty() {
        return (Vec::new(), 0, 0);
    }
    let mut out: Vec<SceneLayer> = Vec::new();
    for (cid, primary, fw, fh) in fenster {
        let Some((st, _)) = eigner(&g, cid) else { continue };
        let ox = (lw - fw) / 2;
        let oy = (lh - fh) / 2;
        if let Some(&buf_id) = st.surf_buffer.get(&primary) {
            if let Some(&info) = st.buffers.get(&buf_id) {
                if info.width > 0 && info.height > 0 && info.stride > 0 {
                    if let Some(mmap) = st.pools.get(&info.pool) {
                        let need = (info.offset as u64)
                            .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
                            .saturating_add((info.width as u64).saturating_mul(4));
                        if need <= mmap.len() as u64 {
                            out.push(SceneLayer { surface_id: primary, mmap: mmap.clone(), info, x: ox, y: oy });
                        }
                    }
                }
            }
        }
        collect_scene_layers(st, primary, ox, oy, &mut out, 0);
    }
    (out, lw, lh)
}


pub(crate) fn damage_seit(shared: &Shared, leser: &str) -> std::collections::HashMap<u32, Vec<(i32, i32, i32, i32)>> {
    use std::sync::{Mutex, OnceLock};
    static CURSOR: OnceLock<Mutex<std::collections::HashMap<String, u64>>> = OnceLock::new();
    let cm = CURSOR.get_or_init(|| Mutex::new(std::collections::HashMap::new()));

    let g = shared.lock().unwrap();
    let ziel = bestes_ziel(&g, MIN_SUBSTANTIAL)
        .map(|cid| g.get(&cid).and_then(|st| st.xparent).unwrap_or(cid));
    let Some(st) = ziel.and_then(|cid| g.get(&cid)) else {
        return std::collections::HashMap::new();
    };

    let mut c = cm.lock().unwrap_or_else(|p| p.into_inner());
    if c.len() > 32 {
        c.clear(); 
    }
    let stand = c.entry(leser.to_string()).or_insert(0);
    let mut out = std::collections::HashMap::new();
    let mut max = *stand;
    for (surf, ring) in &st.surf_damage {
        let (rects, m) = phantom::sehwerk::schaden_seit(ring, *stand);
        if !rects.is_empty() {
            out.insert(*surf, rects);
        }
        if m > max {
            max = m;
        }
    }
    *stand = max;
    out
}


#[allow(dead_code)]
pub(crate) fn drain_foreground_damage(shared: &Shared) -> std::collections::HashMap<u32, Vec<(i32, i32, i32, i32)>> {
    damage_seit(shared, "_legacy")
}

fn collect_scene_layers(
    st: &ClientState,
    parent: u32,
    base_x: i32,
    base_y: i32,
    out: &mut Vec<SceneLayer>,
    depth: u32,
) {
    if depth > 8 {
        return;
    }
    let mut kids: Vec<u32> = st
        .subsurf_parent
        .iter()
        .filter(|(_, &p)| p == parent)
        .map(|(&c, _)| c)
        .collect();
    kids.sort_unstable();
    for child in kids {
        let (rx, ry) = st.subsurf_pos.get(&child).copied().unwrap_or((0, 0));
        let (ax, ay) = (base_x + rx, base_y + ry);
        if let Some(&buf_id) = st.surf_buffer.get(&child) {
            if let Some(&info) = st.buffers.get(&buf_id) {
                if info.width > 0 && info.height > 0 && info.stride > 0 {
                    if let Some(mmap) = st.pools.get(&info.pool) {
                        let need = (info.offset as u64)
                            .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
                            .saturating_add((info.width as u64).saturating_mul(4));
                        if need <= mmap.len() as u64 {
                            out.push(SceneLayer { surface_id: child, mmap: mmap.clone(), info, x: ax, y: ay });
                        }
                    }
                }
            }
        }
        collect_scene_layers(st, child, ax, ay, out, depth + 1);
    }
}

fn collect_subsurfaces(
    st: &ClientState,
    parent: u32,
    base_x: i32,
    base_y: i32,
    out: &mut Vec<(Arc<sys::Mmap>, BufferInfo, i32, i32)>,
    depth: u32,
) {
    if depth > 8 {
        return;
    }
    let mut kids: Vec<u32> = st
        .subsurf_parent
        .iter()
        .filter(|(_, &p)| p == parent)
        .map(|(&c, _)| c)
        .collect();
    kids.sort_unstable();
    for child in kids {
        let (rx, ry) = st.subsurf_pos.get(&child).copied().unwrap_or((0, 0));
        let (ax, ay) = (base_x + rx, base_y + ry);
        if let Some(&buf_id) = st.surf_buffer.get(&child) {
            if let Some(&info) = st.buffers.get(&buf_id) {
                if info.width > 0 && info.height > 0 && info.stride > 0 {
                    if let Some(mmap) = st.pools.get(&info.pool) {
                        let need = (info.offset as u64)
                            .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
                            .saturating_add((info.width as u64).saturating_mul(4));
                        if need <= mmap.len() as u64 {
                            out.push((mmap.clone(), info, ax, ay));
                        }
                    }
                }
            }
        }
        collect_subsurfaces(st, child, ax, ay, out, depth + 1);
    }
}

pub(crate) fn subsurface_at(shared: &Shared, cid: u64, lx: f64, ly: f64) -> Option<(u32, i32, i32)> {
    let g = shared.lock().unwrap();
    let (st, primary) = eigner(&g, cid)?;
    let mut rects: Vec<(u32, i32, i32, u32, u32)> = Vec::new();
    collect_subsurface_rects(st, primary, 0, 0, &mut rects, 0);
    if std::env::var_os("PHANTOM_DEBUG").is_some() {
        eprintln!("SUBSURF cid={cid} primary={primary} pt=({lx:.0},{ly:.0}) rects={rects:?}");
    }

    for &(sid, ax, ay, w, h) in rects.iter().rev() {
        if lx >= ax as f64 && ly >= ay as f64 && lx < (ax + w as i32) as f64 && ly < (ay + h as i32) as f64 {
            return Some((sid, ax, ay));
        }
    }
    None
}

fn collect_subsurface_rects(
    st: &ClientState,
    parent: u32,
    base_x: i32,
    base_y: i32,
    out: &mut Vec<(u32, i32, i32, u32, u32)>,
    depth: u32,
) {
    if depth > 8 {
        return;
    }
    let mut kids: Vec<u32> = st
        .subsurf_parent
        .iter()
        .filter(|(_, &p)| p == parent)
        .map(|(&c, _)| c)
        .collect();
    kids.sort_unstable();
    for child in kids {
        let (rx, ry) = st.subsurf_pos.get(&child).copied().unwrap_or((0, 0));
        let (ax, ay) = (base_x + rx, base_y + ry);
        if let Some(&buf_id) = st.surf_buffer.get(&child) {
            if let Some(&info) = st.buffers.get(&buf_id) {
                if info.width > 0 && info.height > 0 {
                    out.push((child, ax, ay, info.width as u32, info.height as u32));
                }
            }
        }
        collect_subsurface_rects(st, child, ax, ay, out, depth + 1);
    }
}

pub(crate) fn any_client_surface(shared: &Shared) -> bool {
    let g = shared.lock().unwrap();
    g.keys().any(|&cid| match eigner(&g, cid) {
        Some((own, s)) => !own.popup_surf.contains(&s),
        None => false,
    })
}

pub(crate) fn covering_buffer(shared: &Shared, sw: u32, sh: u32) -> Option<(Arc<sys::Mmap>, BufferInfo)> {
    let g = shared.lock().unwrap();
    let mut best: Option<(u64, Arc<sys::Mmap>, BufferInfo)> = None;
    for &cid in g.keys() {
        let Some((st, surface)) = eigner(&g, cid) else { continue };
        if st.popup_surf.contains(&surface) {
            continue;
        }
        let Some(&buf_id) = st.surf_buffer.get(&surface) else { continue };
        let Some(&info) = st.buffers.get(&buf_id) else { continue };
        let Some(mmap) = st.pools.get(&info.pool) else { continue };
        if info.width <= 0 || info.height <= 0 || info.stride <= 0 {
            continue;
        }
        let (w, h) = (info.width as u32, info.height as u32);
        let covers =
            sw.saturating_sub(w) / 2 == 0 && sh.saturating_sub(h) / 2 == 0 && w >= sw && h >= sh;
        if !covers {
            continue;
        }
        let need = (info.offset as u64)
            .saturating_add((info.height as u64).saturating_sub(1).saturating_mul(info.stride as u64))
            .saturating_add((info.width as u64).saturating_mul(4));
        if need > mmap.len() as u64 {
            continue;
        }
        if best.as_ref().map(|(c, _, _)| cid > *c).unwrap_or(true) {
            best = Some((cid, mmap.clone(), info));
        }
    }
    best.map(|(_, m, i)| (m, i))
}

pub(crate) fn focused_geom(shared: &Shared, screen_w: u32, screen_h: u32) -> Option<(u32, u32, u32, u32)> {

    if let Some((_, info)) = focused_buffer(shared) {
        let w = info.width.max(0) as u32;
        let h = info.height.max(0) as u32;
        let ox = screen_w.saturating_sub(w) / 2;
        let oy = screen_h.saturating_sub(h) / 2;
        return Some((ox, oy, w, h));
    }

    let subs = foreground_subsurfaces(shared);
    let (_, info, ax, ay) = subs
        .into_iter()
        .max_by_key(|(_, i, _, _)| (i.width as i64) * (i.height as i64))?;
    Some((
        ax.max(0) as u32,
        ay.max(0) as u32,
        info.width.max(0) as u32,
        info.height.max(0) as u32,
    ))
}

pub(crate) fn client_covers_screen(shared: &Shared, w: u32, h: u32) -> bool {
    match focused_geom(shared, w, h) {
        Some((ox, oy, cw, ch)) => ox == 0 && oy == 0 && cw >= w && ch >= h,
        None => false,
    }
}

pub(crate) fn over_client(geom: Option<(u32, u32, u32, u32)>, x: f64, y: f64) -> bool {
    if let Some((ox, oy, w, h)) = geom {
        if w == 0 || h == 0 {
            return false;
        }
        x >= ox as f64 && y >= oy as f64 && x < (ox + w) as f64 && y < (oy + h) as f64
    } else {
        false
    }
}

pub(crate) fn focused_cid(shared: &Shared) -> Option<u64> {
    let g = shared.lock().unwrap();
    let ov = focused_override();
    let mut best: Option<u64> = None;
    
    
    for &cid in g.keys() {
        if crate::xwl::cid_ready(&g, cid) && beats(ov, cid, best) {
            best = Some(cid);
        }
    }
    best
}

pub(crate) fn focused_pointer(shared: &Shared) -> Option<(u64, u32)> {
    let g = shared.lock().unwrap();
    let ov = focused_override();
    let mut best: Option<(u64, u32)> = None;
    for &cid in g.keys() {
        let Some((own, surface)) = eigner(&g, cid) else { continue };
        if own.pointer.is_some() && beats(ov, cid, best.map(|(c, _)| c)) {
            best = Some((cid, surface));
        }
    }
    best
}
