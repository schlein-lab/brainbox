use super::palette::{ACCENT, INK};
use super::raster::Rgb;

const CURSOR_POLY: [(f64, f64); 7] = [
    (0.0, 0.0),
    (0.0, 15.0),
    (3.6, 11.4),
    (6.4, 17.0),
    (8.6, 16.0),
    (5.9, 10.6),
    (10.4, 10.4),
];
const CURSOR_FILL: Rgb = Rgb(0xfb, 0xfa, 0xf2);
const CURSOR_EDGE: Rgb = INK;
const CURSOR_GLOW: Rgb = ACCENT;

fn cursor_sdf(px: f64, py: f64) -> f64 {
    let pts = &CURSOR_POLY;
    let n = pts.len();
    let mut inside = false;
    let mut min_d = f64::INFINITY;
    let mut j = n - 1;
    for i in 0..n {
        let (xi, yi) = pts[i];
        let (xj, yj) = pts[j];
        if (yi > py) != (yj > py) {
            let t = (py - yi) / (yj - yi);
            if px < xi + t * (xj - xi) {
                inside = !inside;
            }
        }
        let (ex, ey) = (xj - xi, yj - yi);
        let len2 = ex * ex + ey * ey;
        let tt = if len2 > 0.0 {
            (((px - xi) * ex + (py - yi) * ey) / len2).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let (qx, qy) = (xi + tt * ex, yi + tt * ey);
        let d = ((px - qx).powi(2) + (py - qy).powi(2)).sqrt();
        if d < min_d {
            min_d = d;
        }
        j = i;
    }
    if inside {
        -min_d
    } else {
        min_d
    }
}

#[inline]
fn cursor_blend(buf: &mut [u8], off: usize, col: Rgb, a: f64) {
    if a <= 0.0 || off + 3 >= buf.len() {
        return;
    }
    let a = a.min(1.0);
    let ib = buf[off] as f64;
    let ig = buf[off + 1] as f64;
    let ir = buf[off + 2] as f64;
    buf[off] = (ib + (col.2 as f64 - ib) * a) as u8;
    buf[off + 1] = (ig + (col.1 as f64 - ig) * a) as u8;
    buf[off + 2] = (ir + (col.0 as f64 - ir) * a) as u8;
    buf[off + 3] = 0xff;
}

pub fn draw_cursor(buf: &mut [u8], w: u32, h: u32, stride: u32, cx: f64, cy: f64) {
    let (w, h, stride) = (w as i64, h as i64, stride as usize);
    let pad = 6.0;
    let lo_x = (cx - pad).floor() as i64;
    let hi_x = (cx + 11.0 + pad).ceil() as i64;
    let lo_y = (cy - pad).floor() as i64;
    let hi_y = (cy + 17.0 + pad).ceil() as i64;
    let (gcx, gcy) = (5.0, 8.0);
    for sy in lo_y..hi_y {
        if sy < 0 || sy >= h {
            continue;
        }
        let row = sy as usize * stride;
        for sx in lo_x..hi_x {
            if sx < 0 || sx >= w {
                continue;
            }
            let off = row + sx as usize * 4;
            let lx = sx as f64 - cx;
            let ly = sy as f64 - cy;

            let gd = ((lx - gcx).powi(2) + (ly - gcy).powi(2)).sqrt();
            let glow = (1.0 - gd / 16.0).clamp(0.0, 1.0).powf(2.6);
            if glow > 0.01 {
                cursor_blend(buf, off, CURSOR_GLOW, glow * 0.22);
            }

            let sd = cursor_sdf(lx, ly);
            let edge = (1.0 - (sd.abs() / 1.6)).clamp(0.0, 1.0);
            if edge > 0.0 {
                cursor_blend(buf, off, CURSOR_EDGE, edge * 0.92);
            }
            let fill = (-(sd + 1.0) + 0.5).clamp(0.0, 1.0);
            if fill > 0.0 {
                cursor_blend(buf, off, CURSOR_FILL, fill);
            }
        }
    }
}
