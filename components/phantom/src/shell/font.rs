use super::raster::{Canvas, Rgb};

use crate::shell_font::{Glyph as AtlasGlyph, ATLAS, GLYPHS};
pub(crate) use crate::shell_font::{FontStyle, STYLE_METRICS};

#[allow(dead_code)]
pub(crate) const TRACK: i32 = 2;

pub(crate) fn scale_to_style(scale: i32) -> FontStyle {
    match scale {
        s if s <= 1 => FontStyle::Caption,
        2 => FontStyle::Body,
        _ => FontStyle::Large,
    }
}

fn style_run(style: FontStyle) -> (usize, usize) {
    let target = style as u8;
    let mut lo = GLYPHS.len();
    let mut hi = 0usize;
    for (i, g) in GLYPHS.iter().enumerate() {
        if g.style == target {
            if i < lo {
                lo = i;
            }
            hi = i + 1;
        }
    }
    if lo > hi {
        (0, 0)
    } else {
        (lo, hi)
    }
}

fn lookup_glyph(style: FontStyle, cp: u32) -> Option<&'static AtlasGlyph> {
    let (lo, hi) = style_run(style);
    if lo >= hi {
        return None;
    }
    let run = &GLYPHS[lo..hi];
    match run.binary_search_by(|g| g.cp.cmp(&cp)) {
        Ok(i) => Some(&run[i]),
        Err(_) => {

            if cp != '?' as u32 {
                lookup_glyph(style, '?' as u32)
            } else {
                None
            }
        }
    }
}

fn blit_glyph(c: &mut Canvas, g: &AtlasGlyph, gx: i32, gy: i32, col: Rgb) {
    let (w, h) = (g.w as usize, g.h as usize);
    if w == 0 || h == 0 {
        return;
    }
    let start = g.offset as usize;
    let end = start + w * h;
    if end > ATLAS.len() {
        return;
    }
    let tile = &ATLAS[start..end];
    for row in 0..h {
        let ry = gy + row as i32;
        let base = row * w;
        for cx in 0..w {
            let cov = tile[base + cx];
            if cov == 0 {
                continue;
            }
            c.blend_i(gx + cx as i32, ry, col, cov as f64 / 255.0);
        }
    }
}

pub(crate) fn text_styled(c: &mut Canvas, x: i32, y: i32, s: &str, style: FontStyle, col: Rgb) -> i32 {
    let sm = STYLE_METRICS[style as usize];
    let baseline = y + sm.ascent;
    let mut pen = x;
    for ch in s.chars() {
        if ch == ' ' {
            pen += sm.space_advance;
            continue;
        }
        match lookup_glyph(style, ch as u32) {
            Some(g) => {
                let gx = pen + g.bearing_x as i32;
                let gy = baseline - g.bearing_y as i32;
                blit_glyph(c, g, gx, gy, col);
                pen += g.advance as i32;
            }
            None => pen += sm.space_advance,
        }
    }
    pen
}

pub(crate) fn text(c: &mut Canvas, x: i32, y: i32, s: &str, scale: i32, col: Rgb) {
    let _ = text_styled(c, x, y, s, scale_to_style(scale), col);
}

pub(crate) fn text_width_styled(s: &str, style: FontStyle) -> i32 {
    let sm = STYLE_METRICS[style as usize];
    let mut w = 0;
    for ch in s.chars() {
        if ch == ' ' {
            w += sm.space_advance;
        } else if let Some(g) = lookup_glyph(style, ch as u32) {
            w += g.advance as i32;
        } else {
            w += sm.space_advance;
        }
    }
    w.max(0)
}

pub(crate) fn text_width(s: &str, scale: i32) -> i32 {
    text_width_styled(s, scale_to_style(scale))
}

pub(crate) fn text_tracked(c: &mut Canvas, x: i32, y: i32, s: &str, style: FontStyle, col: Rgb, track: i32) -> i32 {
    let sm = STYLE_METRICS[style as usize];
    let baseline = y + sm.ascent;
    let mut pen = x;
    for ch in s.chars() {
        if ch == ' ' {
            pen += sm.space_advance + track;
            continue;
        }
        match lookup_glyph(style, ch as u32) {
            Some(g) => {
                let gx = pen + g.bearing_x as i32;
                let gy = baseline - g.bearing_y as i32;
                blit_glyph(c, g, gx, gy, col);
                pen += g.advance as i32 + track;
            }
            None => pen += sm.space_advance + track,
        }
    }
    pen
}

pub(crate) fn text_tracked_width(s: &str, style: FontStyle, track: i32) -> i32 {
    let n = s.chars().count() as i32;
    (text_width_styled(s, style) + track * n - track).max(0)
}
