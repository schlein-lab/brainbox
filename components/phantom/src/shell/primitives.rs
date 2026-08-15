use super::raster::{Canvas, Rgb};

pub(crate) fn fill_circle(c: &mut Canvas, cx: f64, cy: f64, r: f64, col: Rgb) {
    let lo_y = (cy - r - 1.0).max(0.0) as usize;
    let hi_y = (cy + r + 1.0).min(c.h as f64) as usize;
    let lo_x = (cx - r - 1.0).max(0.0) as usize;
    let hi_x = (cx + r + 1.0).min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let d = ((x as f64 - cx).powi(2) + (y as f64 - cy).powi(2)).sqrt();
            let cov = (r + 0.5 - d).clamp(0.0, 1.0);
            if cov > 0.0 {
                c.blend(x, y, col, cov);
            }
        }
    }
}

pub(crate) fn ring(c: &mut Canvas, cx: f64, cy: f64, r: f64, t: f64, col: Rgb, alpha: f64) {
    let lo_y = (cy - r - t - 1.0).max(0.0) as usize;
    let hi_y = (cy + r + t + 1.0).min(c.h as f64) as usize;
    let lo_x = (cx - r - t - 1.0).max(0.0) as usize;
    let hi_x = (cx + r + t + 1.0).min(c.w as f64) as usize;
    for y in lo_y..hi_y {
        for x in lo_x..hi_x {
            let d = ((x as f64 - cx).powi(2) + (y as f64 - cy).powi(2)).sqrt();
            let cov = (1.0 - (d - r).abs() / (t + 0.5)).clamp(0.0, 1.0);
            if cov > 0.0 {
                c.blend(x, y, col, cov * alpha);
            }
        }
    }
}

#[inline]
pub(crate) fn rrect_sd(px: f64, py: f64, x: f64, y: f64, w: f64, h: f64, rad: f64) -> f64 {
    let cx = x + w * 0.5;
    let cy = y + h * 0.5;
    let hx = w * 0.5 - rad;
    let hy = h * 0.5 - rad;
    let dx = (px - cx).abs() - hx;
    let dy = (py - cy).abs() - hy;
    let outside = (dx.max(0.0).powi(2) + dy.max(0.0).powi(2)).sqrt();
    let inside = dx.max(dy).min(0.0);
    outside + inside - rad
}

pub(crate) fn fill_rrect(c: &mut Canvas, x: f64, y: f64, w: f64, h: f64, rad: f64, col: Rgb, alpha: f64) {
    let rad = rad.min(w * 0.5).min(h * 0.5).max(0.0);
    let lo_y = (y - 1.0).max(0.0) as usize;
    let hi_y = (y + h + 1.0).min(c.h as f64) as usize;
    let lo_x = (x - 1.0).max(0.0) as usize;
    let hi_x = (x + w + 1.0).min(c.w as f64) as usize;
    for yy in lo_y..hi_y {
        for xx in lo_x..hi_x {
            let sd = rrect_sd(xx as f64 + 0.5, yy as f64 + 0.5, x, y, w, h, rad);
            let cov = (-sd + 0.5).clamp(0.0, 1.0);
            if cov > 0.0 {
                c.blend(xx, yy, col, cov * alpha);
            }
        }
    }
}

pub(crate) fn stroke_rrect(c: &mut Canvas, x: f64, y: f64, w: f64, h: f64, rad: f64, t: f64, col: Rgb) {
    let rad = rad.min(w * 0.5).min(h * 0.5).max(0.0);
    let lo_y = (y - t - 1.0).max(0.0) as usize;
    let hi_y = (y + h + t + 1.0).min(c.h as f64) as usize;
    let lo_x = (x - t - 1.0).max(0.0) as usize;
    let hi_x = (x + w + t + 1.0).min(c.w as f64) as usize;
    for yy in lo_y..hi_y {
        for xx in lo_x..hi_x {
            let sd = rrect_sd(xx as f64 + 0.5, yy as f64 + 0.5, x, y, w, h, rad);

            let cov = (1.0 - (sd.abs() - t * 0.5).max(0.0) / 0.8).clamp(0.0, 1.0);
            if cov > 0.0 {
                c.blend(xx, yy, col, cov);
            }
        }
    }
}

#[allow(dead_code)]
pub(crate) fn tri(c: &mut Canvas, x0: f64, y0: f64, x1: f64, y1: f64, x2: f64, y2: f64, col: Rgb) {
    let minx = x0.min(x1).min(x2).floor().max(0.0) as usize;
    let maxx = x0.max(x1).max(x2).ceil().min(c.w as f64) as usize;
    let miny = y0.min(y1).min(y2).floor().max(0.0) as usize;
    let maxy = y0.max(y1).max(y2).ceil().min(c.h as f64) as usize;
    let area = edge(x0, y0, x1, y1, x2, y2);
    if area.abs() < 1e-6 {
        return;
    }
    for y in miny..maxy {
        for x in minx..maxx {
            let px = x as f64 + 0.5;
            let py = y as f64 + 0.5;
            let w0 = edge(x1, y1, x2, y2, px, py) / area;
            let w1 = edge(x2, y2, x0, y0, px, py) / area;
            let w2 = edge(x0, y0, x1, y1, px, py) / area;
            if w0 >= -0.02 && w1 >= -0.02 && w2 >= -0.02 {
                c.blend(x, y, col, 1.0);
            }
        }
    }
}

#[allow(dead_code)]
pub(crate) fn edge(ax: f64, ay: f64, bx: f64, by: f64, cx: f64, cy: f64) -> f64 {
    (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
}

pub(crate) fn stroke(c: &mut Canvas, x0: f64, y0: f64, x1: f64, y1: f64, t: f64, col: Rgb, a: f64) {
    let minx = (x0.min(x1) - t - 1.0).max(0.0) as usize;
    let maxx = (x0.max(x1) + t + 1.0).min(c.w as f64) as usize;
    let miny = (y0.min(y1) - t - 1.0).max(0.0) as usize;
    let maxy = (y0.max(y1) + t + 1.0).min(c.h as f64) as usize;
    let dx = x1 - x0;
    let dy = y1 - y0;
    let len2 = dx * dx + dy * dy;
    for y in miny..maxy {
        for x in minx..maxx {
            let px = x as f64 + 0.5;
            let py = y as f64 + 0.5;
            let tt = if len2 > 0.0 {
                (((px - x0) * dx + (py - y0) * dy) / len2).clamp(0.0, 1.0)
            } else {
                0.0
            };
            let qx = x0 + tt * dx;
            let qy = y0 + tt * dy;
            let d = ((px - qx).powi(2) + (py - qy).powi(2)).sqrt();
            let cov = (t * 0.5 + 0.5 - d).clamp(0.0, 1.0);
            if cov > 0.0 {
                c.blend(x, y, col, cov * a);
            }
        }
    }
}
