use super::state::PROGRAM_COUNT;

pub(crate) const DOCK_ICON: f64 = 46.0;
pub(crate) const DOCK_GAP: f64 = 18.0;
pub(crate) const DOCK_LIFT: f64 = 34.0;
pub(crate) const DOCK_MARGIN: f64 = 13.0;
pub(crate) const DOCK_LABEL_H: f64 = 24.0;

pub(crate) fn dock_row_w() -> f64 {
    let n = PROGRAM_COUNT as f64;
    n * DOCK_ICON + (n - 1.0) * DOCK_GAP
}

pub fn dock_overlay_w() -> u32 {
    (dock_row_w() + DOCK_MARGIN * 2.0) as u32
}
pub fn dock_overlay_h() -> u32 {
    (DOCK_LABEL_H + DOCK_ICON + DOCK_MARGIN * 2.0) as u32
}

pub fn dock_height() -> u32 {
    dock_overlay_h()
}

pub fn dock_overlay_origin(w: u32, h: u32) -> (u32, u32) {
    let x = ((w as f64 - dock_overlay_w() as f64) / 2.0).max(0.0) as u32;
    let y = h.saturating_sub(dock_overlay_h() + DOCK_LIFT as u32);
    (x, y)
}

pub fn dock_reveal_top(h: u32) -> u32 {
    h.saturating_sub((dock_overlay_h() as f64 + DOCK_LIFT + 30.0) as u32)
}

pub fn dock_overlay_contains(px: f64, py: f64, w: u32, h: u32) -> bool {
    let (ox, oy) = dock_overlay_origin(w, h);
    let (ow, oh) = (dock_overlay_w(), dock_overlay_h());
    px >= ox as f64 && py >= oy as f64 && px < (ox + ow) as f64 && py < (oy + oh) as f64
}

pub(crate) fn dock_icon_center(i: usize, w: u32, h: u32) -> (f64, f64) {
    let (ox, oy) = dock_overlay_origin(w, h);
    let cx = ox as f64 + DOCK_MARGIN + DOCK_ICON / 2.0 + i as f64 * (DOCK_ICON + DOCK_GAP);
    let cy = oy as f64 + DOCK_LABEL_H + DOCK_MARGIN + DOCK_ICON / 2.0;
    (cx, cy)
}
