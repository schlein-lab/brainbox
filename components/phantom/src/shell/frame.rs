use super::clock::local_clock;
use super::font::{text, text_tracked, text_tracked_width, text_width, FontStyle};
use super::glyphs::draw_glyph;
use super::palette::{ACCENT, INK, INK_FAINT, INK_SOFT, LINE, OK_GREEN, PAPER, PAPER_ALT, PAPER_HI};
use super::primitives::{fill_circle, fill_rrect, ring, stroke, stroke_rrect};
use super::raster::{lerp, Canvas, Rgb};
use super::state::{Shell, ShellMode, PROGRAMS};

pub(crate) fn render_shell(c: &mut Canvas, _rot: f64, _sh: &Shell) {
    paint_paper(c);

    draw_status_cluster(c);
    draw_brand_mark(c);
}

pub(crate) fn render_idle(c: &mut Canvas, mode: ShellMode, sh: &Shell) {
    let (w, h) = (c.w, c.h);

    let idle_bg = lerp(PAPER, PAPER_ALT, 0.12);
    for y in 0..h {
        for x in 0..w {
            c.put(x, y, idle_bg);
        }
    }

    let (ex, ey, er) = (w as f64 * 0.5, h as f64 * 0.40, 96.0);

    let remote = mode == ShellMode::Remote;
    breathing_pulse(c, ex, ey, er * 1.6, ACCENT);
    blit_hero_scaled(c, ex, ey, er * 2.0, 0.48);
    if remote {
        ring(c, ex, ey, er * 0.92, 3.0, ACCENT, 0.55);
    }

    let title = match mode {
        ShellMode::Auto => "VOLLAUTOMATISIERUNG L\u{00c4}UFT",
        ShellMode::Remote => "REMOTE L\u{00c4}UFT",
        ShellMode::Onsite => "L\u{00c4}UFT",
    };
    let tw = text_tracked_width(title, FontStyle::Caption, 2);
    let tx = (ex - tw as f64 / 2.0) as i32;
    let title_y = (ey + er + 30.0) as i32;
    text_tracked(c, tx, title_y, title, FontStyle::Caption, INK_SOFT, 2);

    for k in 0..tw {
        c.blend_i(tx + k, title_y + 18, ACCENT, 0.45);
    }

    match mode {
        ShellMode::Remote => {

            let line = "DRIVING FROM ELSEWHERE";
            let lw = text_width(line, 1);
            let lx = (ex - lw as f64 / 2.0) as i32;
            text(c, lx, (ey + er + 56.0) as i32, line, 1, INK_FAINT);
        }
        ShellMode::Auto => {
            let line = "SCREEN ~99% OFF \u{00b7} HEADLESS \u{00b7} APPS NOT RENDERED";
            let lw = text_width(line, 1);
            text(c, (ex - lw as f64 / 2.0) as i32, (ey + er + 56.0) as i32, line, 1, INK_FAINT);
        }
        ShellMode::Onsite => {}
    }

    if let Some((date, time)) = local_clock().or_else(|| {

        if !sh.clock_hms.is_empty() {
            Some((sh.date.clone(), sh.clock_hms.clone()))
        } else {
            None
        }
    }) {
        let stamp = format!("{} \u{00b7} {}", date, time);
        let sw = text_width(&stamp, 3);
        let sy = (ey + er + 92.0) as i32;
        clock_glow(c, ex, (sy + 11) as f64, sw as f64 * 0.5 + 30.0);
        text(c, (ex - sw as f64 / 2.0) as i32, sy, &stamp, 3, INK);
    }

    let tag = match mode {
        ShellMode::Auto => "shell \u{00b7} vollautomatisierung \u{00b7} the screen is off, the work is not",
        ShellMode::Remote => "shell \u{00b7} remote \u{00b7} driven from elsewhere",
        ShellMode::Onsite => "shell \u{00b7} automation-first",
    };

    text_tracked(c, 70, h as i32 - 50, "PHANTOM", FontStyle::Heading, INK, 3);
    text(c, 70, h as i32 - 26, tag, 1, INK_FAINT);
}

fn breathing_pulse(c: &mut Canvas, cx: f64, cy: f64, r: f64, col: Rgb) {
    let lo_y = (cy - r).floor().max(0.0) as usize;
    let hi_y = (cy + r).ceil().min(c.h as f64) as usize;
    let lo_x = (cx - r).floor().max(0.0) as usize;
    let hi_x = (cx + r).ceil().min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let dx = x as f64 - cx;
            let dy = y as f64 - cy;
            let d = (dx * dx + dy * dy).sqrt();
            let g = (1.0 - (d / r).clamp(0.0, 1.0)).powf(2.6);
            if g > 0.003 {
                c.blend(x, y, col, g * 0.10);
            }
        }
    }
}

fn clock_glow(c: &mut Canvas, cx: f64, cy: f64, rx: f64) {
    let ry = 34.0;
    let lo_y = (cy - ry).max(0.0) as usize;
    let hi_y = (cy + ry).min(c.h as f64) as usize;
    let lo_x = (cx - rx).max(0.0) as usize;
    let hi_x = (cx + rx).min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let dx = (x as f64 - cx) / rx;
            let dy = (y as f64 - cy) / ry;
            let d = (dx * dx + dy * dy).sqrt();
            let g = (1.0 - d).clamp(0.0, 1.0).powf(2.2);
            if g > 0.003 {
                c.blend(x, y, ACCENT, g * 0.07);
            }
        }
    }
}

fn paint_paper(c: &mut Canvas) {
    let (w, h) = (c.w, c.h);
    let (cx, cy) = (w as f64 * 0.5, h as f64 * 0.46);
    for y in 0..h {
        for x in 0..w {
            let dx = x as f64 - cx;
            let dy = (y as f64 - cy) * 1.15;
            let d = (dx * dx + dy * dy).sqrt();

            let lift = (1.0 - (d / 420.0)).clamp(0.0, 1.0).powf(1.5);
            let col = lerp(PAPER, PAPER_HI, lift * 0.55);
            c.put(x, y, col);
        }
    }

    for x in (0..w).step_by(28) {
        let bold = x % 140 == 0;
        let a = if bold { 0.11 } else { 0.06 };
        for y in 0..h {
            c.blend(x, y, ACCENT, a);
        }
    }
    for y in (0..h).step_by(28) {
        let bold = y % 140 == 0;
        let a = if bold { 0.11 } else { 0.06 };
        for x in 0..w {
            c.blend(x, y, ACCENT, a);
        }
    }
}

fn draw_hero(c: &mut Canvas, cx: f64, cy: f64, r: f64) {

    let glow_r = r * 1.9;
    let lo_y = (cy - glow_r).floor().max(0.0) as usize;
    let hi_y = (cy + glow_r).ceil().min(c.h as f64) as usize;
    let lo_x = (cx - glow_r).floor().max(0.0) as usize;
    let hi_x = (cx + glow_r).ceil().min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let dx = x as f64 - cx;
            let dy = y as f64 - cy;
            let d = (dx * dx + dy * dy).sqrt();
            if d <= r * 0.85 {
                continue;
            }
            let glow = (1.0 - ((d - r * 0.85) / (glow_r - r * 0.85)).clamp(0.0, 1.0)).powf(2.6);
            if glow > 0.003 {
                c.blend(x, y, ACCENT, glow * 0.10);
            }
        }
    }

    if !blit_hero_scaled(c, cx, cy, r * 2.0, 1.0) {
        draw_hero_soft(c, cx, cy, r);
    }
}

fn blit_hero_scaled(c: &mut Canvas, cx: f64, cy: f64, target: f64, opacity: f64) -> bool {
    use crate::shell_hero::{HERO_RGBA, HERO_SIZE};
    let n = HERO_SIZE;
    if n == 0 || HERO_RGBA.len() < n * n * 4 || target <= 0.0 {
        return false;
    }
    let dst = target.round().max(1.0) as i32;
    let x0 = (cx - target / 2.0).round() as i32;
    let y0 = (cy - target / 2.0).round() as i32;
    let opacity = opacity.clamp(0.0, 1.0);
    for dy in 0..dst {

        let sy = ((dy as f64 + 0.5) * n as f64 / dst as f64) as usize;
        let sy = sy.min(n - 1);
        let py = y0 + dy;
        let srow = sy * n * 4;
        for dx in 0..dst {
            let sx = ((dx as f64 + 0.5) * n as f64 / dst as f64) as usize;
            let sx = sx.min(n - 1);
            let p = srow + sx * 4;
            let a = HERO_RGBA[p + 3];
            if a == 0 {
                continue;
            }
            let rgb = Rgb(HERO_RGBA[p], HERO_RGBA[p + 1], HERO_RGBA[p + 2]);
            c.blend_i(x0 + dx, py, rgb, (a as f64 / 255.0) * opacity);
        }
    }
    true
}

fn draw_hero_soft(c: &mut Canvas, cx: f64, cy: f64, r: f64) {
    let body = r * 0.78;
    fill_circle(c, cx, cy, body, PAPER_HI);
    ring(c, cx, cy, body, 3.0, INK, 1.0);
    ring(c, cx, cy, body * 1.18, 2.0, ACCENT, 0.6);

    fill_circle(c, cx - body * 0.28, cy - body * 0.12, body * 0.10, INK);
    fill_circle(c, cx + body * 0.28, cy - body * 0.12, body * 0.10, INK);
    let sr = body * 0.42;
    let mut a = 0.35_f64;
    while a < std::f64::consts::PI - 0.35 {
        let xx = cx + sr * a.cos();
        let yy = cy + body * 0.16 + sr * a.sin();
        fill_circle(c, xx, yy, 2.0, INK);
        a += 0.06;
    }
}

fn draw_program_ring(c: &mut Canvas, cx: f64, cy: f64, er: f64, rot: f64) {
    let n = PROGRAMS.len();
    let orbit = er + 88.0;
    let front = std::f64::consts::FRAC_PI_2;

    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&a, &b| {
        sin_of(rot, a, n)
            .partial_cmp(&sin_of(rot, b, n))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    for &i in &order {
        let ang = rot + (i as f64) * std::f64::consts::TAU / n as f64;
        let ax = cx + orbit * ang.cos();
        let ay = cy + orbit * ang.sin();

        let frontness = {
            let mut da = (ang - front).rem_euclid(std::f64::consts::TAU);
            if da > std::f64::consts::PI {
                da = std::f64::consts::TAU - da;
            }
            1.0 - da / std::f64::consts::PI
        };

        let size = 46.0 + 64.0 * frontness;
        let opacity = 0.55 + 0.45 * frontness;
        let pull = (1.0 - frontness) * 26.0;
        let ax = ax - (ax - cx) * (pull / orbit);
        let ay = ay - (ay - cy) * (pull / orbit);

        if frontness > 0.6 {
            let hr = size * 0.62;
            ring(c, ax, ay, hr, 2.5, ACCENT, 0.55 * frontness);

            let gr = size * 0.9;
            let lo_y = (ay - gr).max(0.0) as usize;
            let hi_y = (ay + gr).min(c.h as f64) as usize;
            let lo_x = (ax - gr).max(0.0) as usize;
            let hi_x = (ax + gr).min(c.w as f64) as usize;
            for y in lo_y..hi_y {
                for x in lo_x..hi_x {
                    let d = ((x as f64 - ax).powi(2) + (y as f64 - ay).powi(2)).sqrt();
                    let halo = (1.0 - (d / gr)).clamp(0.0, 1.0).powf(2.6);
                    if halo > 0.004 {
                        c.blend(x, y, ACCENT, halo * 0.12 * frontness);
                    }
                }
            }
        }

        if !blit_icon(c, i, ax, ay, size, opacity) {

            draw_glyph(c, ax, ay, size * 0.32, PROGRAMS[i].0, INK, opacity);
        }

        if frontness > 0.82 {
            let label = PROGRAMS[i].1;
            let tw = text_tracked_width(label, FontStyle::Heading, 2);
            text_tracked(
                c,
                (ax - tw as f64 / 2.0) as i32,
                (ay + size * 0.55 + 14.0) as i32,
                label,
                FontStyle::Heading,
                INK,
                2,
            );
            let hint = "SPIN \u{00b7} SELECT \u{00b7} LAUNCH";
            let hw = text_tracked_width(hint, FontStyle::Caption, 1);
            text_tracked(
                c,
                (ax - hw as f64 / 2.0) as i32,
                (ay + size * 0.55 + 40.0) as i32,
                hint,
                FontStyle::Caption,
                ACCENT,
                1,
            );
        }
    }
}

fn sin_of(rot: f64, i: usize, n: usize) -> f64 {
    (rot + (i as f64) * std::f64::consts::TAU / n as f64).sin()
}

fn blit_icon(c: &mut Canvas, idx: usize, cx: f64, cy: f64, size: f64, opacity: f64) -> bool {
    use crate::shell_icons::{ICONS_RGBA, ICON_COUNT, ICON_SIZE};
    let n = ICON_SIZE;
    if n == 0 || idx >= ICON_COUNT || size <= 0.0 {
        return false;
    }
    let stride = n * n * 4;
    let base = idx * stride;
    if base + stride > ICONS_RGBA.len() {
        return false;
    }
    let tile = &ICONS_RGBA[base..base + stride];
    let dst = size.round().max(1.0) as i32;
    let x0 = (cx - size / 2.0).round() as i32;
    let y0 = (cy - size / 2.0).round() as i32;
    let opacity = opacity.clamp(0.0, 1.0);
    for dy in 0..dst {
        let sy = (((dy as f64 + 0.5) * n as f64 / dst as f64) as usize).min(n - 1);
        let py = y0 + dy;
        let srow = sy * n * 4;
        for dx in 0..dst {
            let sx = (((dx as f64 + 0.5) * n as f64 / dst as f64) as usize).min(n - 1);
            let p = srow + sx * 4;
            let a = tile[p + 3];
            if a == 0 {
                continue;
            }
            let rgb = Rgb(tile[p], tile[p + 1], tile[p + 2]);
            c.blend_i(x0 + dx, py, rgb, (a as f64 / 255.0) * opacity);
        }
    }
    true
}

fn draw_handshake_node(c: &mut Canvas, cx: f64, cy: f64, er: f64, rot: f64) {
    let ang = -0.62 - rot * 0.18;
    let nx = cx + (er + 4.0) * ang.cos();
    let ny = cy + (er + 4.0) * ang.sin();
    let nr = 15.0;

    let lo_y = (ny - 30.0).max(0.0) as usize;
    let hi_y = (ny + 30.0).min(c.h as f64) as usize;
    let lo_x = (nx - 30.0).max(0.0) as usize;
    let hi_x = (nx + 30.0).min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let d = ((x as f64 - nx).powi(2) + (y as f64 - ny).powi(2)).sqrt();
            let halo = (1.0 - (d / 30.0)).clamp(0.0, 1.0).powf(2.4);
            if halo > 0.004 {
                c.blend(x, y, ACCENT, halo * 0.16);
            }
        }
    }

    fill_circle(c, nx, ny, nr, PAPER_HI);
    ring(c, nx, ny, nr, 2.0, INK, 1.0);
    ring(c, nx, ny, nr - 3.0, 1.5, ACCENT, 0.7);

    ring(c, nx - 3.0, ny - 2.0, 3.2, 1.6, ACCENT, 1.0);
    stroke(c, nx - 0.5, ny + 0.2, nx + 5.0, ny + 5.7, 1.8, ACCENT, 1.0);
    stroke(c, nx + 4.0, ny + 4.7, nx + 6.0, ny + 3.0, 1.6, ACCENT, 1.0);

    let line1 = "ZYRKEL \u{2192} REMOTE";
    let line2 = "AUTHORIZE \u{00b7} CAPABILITY GATE";
    let tw = text_tracked_width(line1, FontStyle::Body, 1)
        .max(text_tracked_width(line2, FontStyle::Caption, 1));
    let pad = 16;
    let pw = tw + pad * 2;
    let ph = 52;
    let lpx = (nx + nr + 14.0) as i32;
    let lpy = (ny - ph as f64 / 2.0) as i32;
    card(c, lpx, lpy, pw, ph, 10.0);
    text_tracked(c, lpx + pad, lpy + 11, line1, FontStyle::Body, ACCENT, 1);
    text_tracked(c, lpx + pad, lpy + 31, line2, FontStyle::Caption, INK_SOFT, 1);
}

fn card(c: &mut Canvas, x: i32, y: i32, w: i32, h: i32, rad: f64) {

    fill_rrect(c, (x + 4) as f64, (y + 4) as f64, w as f64, h as f64, rad, INK, 1.0);

    fill_rrect(c, x as f64, y as f64, w as f64, h as f64, rad, PAPER_HI, 1.0);
    stroke_rrect(c, x as f64, y as f64, w as f64, h as f64, rad, 2.0, INK);
}

fn draw_status_cluster(c: &mut Canvas) {
    let clock = local_clock();

    let (pw, ph) = (180, 44);
    let px = c.w as i32 - 70 - pw;
    let py = c.h as i32 - 64;
    card(c, px, py, pw, ph, 11.0);
    let ty = py + ph / 2 - 8;
    let mut x = px + 18;

    if let Some((_, ref time)) = clock {
        text(c, x, ty, time, 2, INK);
        x += text_width(time, 2) + 16;
        sep(c, x, py, ph);
        x += 16;
    }

    text(c, x, ty, "\u{203a}", 2, ACCENT);
    text(c, x + 16, ty, "TTY", 2, INK_SOFT);
}

fn sep(c: &mut Canvas, x: i32, py: i32, ph: i32) {
    for k in 11..(ph - 11) {
        c.blend_i(x, py + k, LINE, 0.7);
    }
}

fn draw_brand_mark(c: &mut Canvas) {
    let bx = 70;
    let by = c.h as i32 - 50;

    text_tracked(c, bx, by, "PHANTOM", FontStyle::Heading, INK, 3);
    text(c, bx, by + 24, "shell \u{00b7} automation-first", 1, INK_SOFT);
}
