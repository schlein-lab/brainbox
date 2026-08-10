use super::dock::{dock_icon_center, DOCK_GAP, DOCK_ICON, DOCK_LABEL_H, DOCK_MARGIN};
use super::font::{text_tracked, text_tracked_width, FontStyle};
use super::frame::{render_idle, render_shell};
use super::geom::{icon_geom, ring_center};
use super::glyphs::{draw_glyph, Glyph};
use super::palette::{ACCENT, INK, INK_SOFT, PAPER, PAPER_HI};
use super::primitives::{fill_circle, fill_rrect, ring};
use super::raster::Canvas;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ShellMode {
    Onsite,
    Auto,
    Remote,
}

pub(crate) const PROGRAMS: [(Glyph, &str); 7] = [
    (Glyph::Terminal, "TERMINAL"),
    (Glyph::Files, "FILES"),
    (Glyph::Browser, "BROWSER"),
    (Glyph::Code, "CODE"),
    (Glyph::Claude, "CLAUDE"),
    (Glyph::Settings, "SETTINGS"),
    (Glyph::Calendar, "CALENDAR"),
];

pub(crate) const PROGRAM_COUNT: usize = PROGRAMS.len();

pub struct Shell {

    pub rotation: f32,

    pub selected: usize,

    pub mode: ShellMode,

    pub clock_hms: String,

    pub date: String,
}

impl Shell {

    pub fn new() -> Shell {
        let mut s = Shell {
            rotation: 0.0,
            selected: 0,
            mode: ShellMode::Onsite,
            clock_hms: String::new(),
            date: String::new(),
        };
        s.recompute_selected();
        s
    }

    pub fn spin(&mut self, delta: f32) {
        let tau = std::f32::consts::TAU;
        self.rotation = (self.rotation + delta).rem_euclid(tau);
        self.recompute_selected();
    }

    pub fn cycle_mode(&mut self) {
        self.mode = match self.mode {
            ShellMode::Onsite => ShellMode::Auto,
            ShellMode::Auto => ShellMode::Remote,
            ShellMode::Remote => ShellMode::Onsite,
        };
    }

    pub fn set_mode(&mut self, m: ShellMode) {
        self.mode = m;
    }

    pub fn selected_app(&self) -> &str {
        PROGRAMS.get(self.selected).map(|p| p.1).unwrap_or("")
    }

    pub fn select_app(&mut self, name: &str) -> bool {
        let upper = name.to_ascii_uppercase();
        if let Some(i) = PROGRAMS.iter().position(|p| p.1 == upper) {
            let n = PROGRAMS.len() as f32;
            let front = std::f32::consts::FRAC_PI_2;

            let tau = std::f32::consts::TAU;
            self.rotation = (front - (i as f32) * tau / n).rem_euclid(tau);
            self.recompute_selected();
            true
        } else {
            false
        }
    }

    pub fn icon_at(&self, px: f64, py: f64, w: u32, h: u32) -> Option<usize> {
        if self.mode != ShellMode::Onsite {
            return None;
        }
        let (cx, cy, er) = ring_center(w as f64, h as f64);
        let mut best: Option<(usize, f64)> = None;
        for i in 0..PROGRAMS.len() {
            let (ax, ay, size, _f) = icon_geom(i, cx, cy, er, self.rotation as f64);
            let r = size * 0.5;
            if (px - ax).abs() <= r
                && (py - ay).abs() <= r
                && best.map(|(_, s)| size > s).unwrap_or(true)
            {
                best = Some((i, size));
            }
        }
        best.map(|(i, _)| i)
    }

    pub fn select_index(&mut self, i: usize) {
        if let Some((_, label)) = PROGRAMS.get(i) {
            self.select_app(label);
        }
    }

    pub fn app_label(&self, i: usize) -> &'static str {
        PROGRAMS.get(i).map(|p| p.1).unwrap_or("")
    }

    pub fn dock_icon_at(&self, px: f64, py: f64, w: u32, h: u32) -> Option<usize> {
        let r = DOCK_ICON / 2.0 + 2.0;
        for i in 0..PROGRAMS.len() {
            let (cx, cy) = dock_icon_center(i, w, h);
            if (px - cx).powi(2) + (py - cy).powi(2) <= r * r {
                return Some(i);
            }
        }
        None
    }

    pub fn render_dock_tray(&self, buf: &mut [u8], w: u32, h: u32, hover: Option<usize>) {
        if w == 0 || h == 0 {
            return;
        }
        let mut c = Canvas::wrap(buf, w as usize, h as usize);

        let cy = DOCK_LABEL_H + DOCK_MARGIN + DOCK_ICON / 2.0;
        let r = DOCK_ICON / 2.0;
        for i in 0..PROGRAMS.len() {
            let cx = DOCK_MARGIN + DOCK_ICON / 2.0 + i as f64 * (DOCK_ICON + DOCK_GAP);
            let hot = hover == Some(i);

            fill_circle(&mut c, cx + 2.0, cy + 3.0, r, INK_SOFT);
            fill_circle(&mut c, cx, cy, r, if hot { PAPER_HI } else { PAPER });
            ring(&mut c, cx, cy, r, 2.0, INK, 0.95);
            if hot {
                ring(&mut c, cx, cy, r - 1.0, 2.0, ACCENT, 0.9);
            }

            draw_glyph(&mut c, cx, cy, DOCK_ICON * 0.42, PROGRAMS[i].0, INK, 1.0);
        }

        if let Some(i) = hover {
            if let Some((_, label)) = PROGRAMS.get(i) {
                let cx = DOCK_MARGIN + DOCK_ICON / 2.0 + i as f64 * (DOCK_ICON + DOCK_GAP);
                let tw = text_tracked_width(label, FontStyle::Caption, 1) as f64;
                let pad = 9.0;
                let pw = tw + pad * 2.0;
                let ph = DOCK_LABEL_H - 4.0;
                let lx = (cx - pw / 2.0).clamp(1.0, (w as f64 - pw - 1.0).max(1.0));
                let ly = 1.0;
                fill_rrect(&mut c, lx, ly, pw, ph, 6.0, INK, 1.0);
                text_tracked(
                    &mut c,
                    (lx + pad) as i32,
                    (ly + 4.0) as i32,
                    label,
                    FontStyle::Caption,
                    PAPER_HI,
                    1,
                );
            }
        }
    }

    fn recompute_selected(&mut self) {
        let n = PROGRAMS.len();
        let tau = std::f32::consts::TAU;
        let front = std::f32::consts::FRAC_PI_2;
        let mut best = 0usize;
        let mut best_d = f32::INFINITY;
        for i in 0..n {
            let ang = (self.rotation + (i as f32) * tau / n as f32).rem_euclid(tau);
            let mut da = (ang - front).rem_euclid(tau);
            if da > std::f32::consts::PI {
                da = tau - da;
            }
            if da < best_d {
                best_d = da;
                best = i;
            }
        }
        self.selected = best;
    }

    pub fn render(&self, buf: &mut [u8], w: u32, h: u32) {
        if w == 0 || h == 0 {
            return;
        }
        let mut c = Canvas::wrap(buf, w as usize, h as usize);
        match self.mode {
            ShellMode::Onsite => render_shell(&mut c, self.rotation as f64, self),
            ShellMode::Auto => render_idle(&mut c, ShellMode::Auto, self),
            ShellMode::Remote => render_idle(&mut c, ShellMode::Remote, self),
        }
    }
}

impl Default for Shell {
    fn default() -> Self {
        Shell::new()
    }
}
