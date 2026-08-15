use super::primitives::{ring, stroke};
use super::raster::{Canvas, Rgb};

#[derive(Clone, Copy)]
pub(crate) enum Glyph {
    Terminal,
    Files,
    Browser,
    Code,
    Claude,
    Settings,
    Calendar,
}

pub(crate) fn draw_glyph(c: &mut Canvas, cx: f64, cy: f64, s: f64, g: Glyph, col: Rgb, a: f64) {
    let line = |c: &mut Canvas, x0: f64, y0: f64, x1: f64, y1: f64, t: f64| {
        stroke(c, cx + x0 * s, cy + y0 * s, cx + x1 * s, cy + y1 * s, t, col, a);
    };
    match g {
        Glyph::Terminal => {
            line(c, -0.55, -0.3, -0.2, 0.0, 2.2);
            line(c, -0.2, 0.0, -0.55, 0.3, 2.2);
            line(c, 0.05, 0.32, 0.5, 0.32, 2.2);
        }
        Glyph::Files => {
            line(c, -0.5, -0.2, -0.1, -0.2, 2.0);
            line(c, -0.1, -0.2, 0.05, -0.35, 2.0);
            line(c, 0.05, -0.35, 0.5, -0.35, 2.0);
            line(c, -0.5, -0.2, -0.5, 0.35, 2.0);
            line(c, 0.5, -0.35, 0.5, 0.35, 2.0);
            line(c, -0.5, 0.35, 0.5, 0.35, 2.0);
        }
        Glyph::Browser => {
            ring(c, cx, cy, s * 0.5, 1.4, col, a);
            line(c, -0.5, 0.0, 0.5, 0.0, 1.4);
            ring(c, cx, cy, s * 0.5, 1.0, col, a * 0.6);
            line(c, 0.0, -0.5, 0.0, 0.5, 1.4);
        }
        Glyph::Code => {
            line(c, -0.15, -0.4, -0.5, 0.0, 2.0);
            line(c, -0.5, 0.0, -0.15, 0.4, 2.0);
            line(c, 0.15, -0.4, 0.5, 0.0, 2.0);
            line(c, 0.5, 0.0, 0.15, 0.4, 2.0);
        }
        Glyph::Claude => {
            for k in 0..6 {
                let a0 = k as f64 * std::f64::consts::PI / 3.0;
                line(c, 0.0, 0.0, 0.5 * a0.cos(), 0.5 * a0.sin(), 1.8);
            }
        }
        Glyph::Settings => {
            ring(c, cx, cy, s * 0.34, 2.0, col, a);
            for k in 0..6 {
                let a0 = k as f64 * std::f64::consts::PI / 3.0;
                line(c, 0.34 * a0.cos(), 0.34 * a0.sin(), 0.52 * a0.cos(), 0.52 * a0.sin(), 2.4);
            }
        }
        Glyph::Calendar => {

            line(c, -0.45, -0.3, 0.45, -0.3, 2.0);
            line(c, -0.45, 0.4, 0.45, 0.4, 2.0);
            line(c, -0.45, -0.3, -0.45, 0.4, 2.0);
            line(c, 0.45, -0.3, 0.45, 0.4, 2.0);
            line(c, -0.45, -0.05, 0.45, -0.05, 1.6);
            line(c, -0.22, -0.45, -0.22, -0.18, 2.0);
            line(c, 0.22, -0.45, 0.22, -0.18, 2.0);
        }
    }
}
