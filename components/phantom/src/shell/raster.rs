#[derive(Clone, Copy)]
pub(crate) struct Rgb(pub(crate) u8, pub(crate) u8, pub(crate) u8);

pub(crate) fn lerp(a: Rgb, b: Rgb, t: f64) -> Rgb {
    let t = t.clamp(0.0, 1.0);
    Rgb(
        (a.0 as f64 + (b.0 as f64 - a.0 as f64) * t) as u8,
        (a.1 as f64 + (b.1 as f64 - a.1 as f64) * t) as u8,
        (a.2 as f64 + (b.2 as f64 - a.2 as f64) * t) as u8,
    )
}

pub struct Canvas<'a> {
    pub(crate) w: usize,
    pub(crate) h: usize,
    pub(crate) buf: &'a mut [u8],
}

impl<'a> Canvas<'a> {

    pub fn wrap(buf: &'a mut [u8], w: usize, h: usize) -> Canvas<'a> {
        let max_rows = buf.len() / (w.max(1) * 4);
        let h = h.min(max_rows);
        Canvas { w, h, buf }
    }

    #[inline]
    pub(crate) fn put(&mut self, x: usize, y: usize, c: Rgb) {
        if x >= self.w || y >= self.h {
            return;
        }
        let o = (y * self.w + x) * 4;
        if o + 3 >= self.buf.len() {
            return;
        }
        self.buf[o] = c.2;
        self.buf[o + 1] = c.1;
        self.buf[o + 2] = c.0;
        self.buf[o + 3] = 0xff;
    }

    #[inline]
    pub(crate) fn blend(&mut self, x: usize, y: usize, c: Rgb, a: f64) {
        if x >= self.w || y >= self.h || a <= 0.0 {
            return;
        }
        let o = (y * self.w + x) * 4;
        if o + 3 >= self.buf.len() {
            return;
        }
        let a = a.min(1.0);
        let ib = self.buf[o] as f64;
        let ig = self.buf[o + 1] as f64;
        let ir = self.buf[o + 2] as f64;
        self.buf[o] = (ib + (c.2 as f64 - ib) * a) as u8;
        self.buf[o + 1] = (ig + (c.1 as f64 - ig) * a) as u8;
        self.buf[o + 2] = (ir + (c.0 as f64 - ir) * a) as u8;
        self.buf[o + 3] = 0xff;
    }

    #[inline]
    pub(crate) fn blend_i(&mut self, x: i32, y: i32, c: Rgb, a: f64) {
        if x < 0 || y < 0 {
            return;
        }
        self.blend(x as usize, y as usize, c, a);
    }

    pub fn to_rgba(&self) -> Vec<u8> {
        let mut out = vec![0u8; self.w * self.h * 4];
        for i in 0..self.w * self.h {
            let s = i * 4;
            if s + 3 < self.buf.len() {
                out[i * 4] = self.buf[s + 2];
                out[i * 4 + 1] = self.buf[s + 1];
                out[i * 4 + 2] = self.buf[s];
                out[i * 4 + 3] = 0xff;
            }
        }
        out
    }
}
