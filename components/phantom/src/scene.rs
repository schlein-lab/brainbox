#![allow(dead_code)]

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: u32,
    pub h: u32,
}

impl Rect {
    pub const fn new(x: i32, y: i32, w: u32, h: u32) -> Rect {
        Rect { x, y, w, h }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Crop {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fit {

    Stretch,

    Contain,

    Cover,

    None,
}

#[derive(Clone, Copy)]
pub struct SurfaceView<'a> {
    pub bytes: &'a [u8],
    pub off: usize,
    pub w: u32,
    pub h: u32,
    pub stride: u32,
    pub has_alpha: bool,
}

pub enum Src<'a> {

    Surface(SurfaceView<'a>),

    Solid([u8; 3]),
}

pub struct Placement<'a> {
    pub src: Src<'a>,
    pub crop: Option<Crop>,
    pub dest: Rect,
    pub fit: Fit,
    pub z: i32,
    pub opacity: u8,
}

pub fn compose(out: &mut [u8], w: u32, h: u32, pitch: u32, placements: &mut [PlacementIdx], scene: &[Placement]) {

    let n = scene.len().min(placements.len());
    for (i, p) in placements.iter_mut().enumerate().take(n) {
        p.0 = i as u32;
    }
    placements[..n].sort_by_key(|p| scene[p.0 as usize].z);
    for p in &placements[..n] {
        draw(out, w, h, pitch, &scene[p.0 as usize]);
    }
}

#[derive(Clone, Copy)]
pub struct PlacementIdx(pub u32);

pub fn compose_vec(out: &mut [u8], w: u32, h: u32, pitch: u32, scene: &mut [Placement]) {
    scene.sort_by_key(|p| p.z);
    for p in scene.iter() {
        draw(out, w, h, pitch, p);
    }
}

fn draw(out: &mut [u8], w: u32, h: u32, pitch: u32, p: &Placement) {
    if p.opacity == 0 || p.dest.w == 0 || p.dest.h == 0 {
        return;
    }
    match &p.src {
        Src::Solid(rgb) => {

            fill_rect(out, w, h, pitch, p.dest, *rgb, p.opacity);
        }
        Src::Surface(view) => {
            let crop = clamp_crop(p.crop, view.w, view.h);
            if crop.w == 0 || crop.h == 0 {
                return;
            }
            let dest = fit_dest(p.fit, crop.w, crop.h, p.dest);
            if dest.w == 0 || dest.h == 0 {
                return;
            }
            scale_blit(out, w, h, pitch, view, crop, dest, p.opacity);
        }
    }
}

fn clamp_crop(crop: Option<Crop>, sw: u32, sh: u32) -> Crop {
    match crop {
        None => Crop { x: 0, y: 0, w: sw, h: sh },
        Some(c) => {
            let x = c.x.min(sw);
            let y = c.y.min(sh);
            Crop { x, y, w: c.w.min(sw - x), h: c.h.min(sh - y) }
        }
    }
}

pub fn fit_dest(fit: Fit, crop_w: u32, crop_h: u32, dest: Rect) -> Rect {
    match fit {
        Fit::Stretch => dest,
        Fit::None => Rect { x: dest.x, y: dest.y, w: crop_w.min(dest.w), h: crop_h.min(dest.h) },
        Fit::Contain | Fit::Cover => {
            if crop_w == 0 || crop_h == 0 || dest.w == 0 || dest.h == 0 {
                return dest;
            }

            let sx_num = dest.w as u64 * crop_h as u64;
            let sy_num = dest.h as u64 * crop_w as u64;
            let use_x = match fit {
                Fit::Contain => sx_num <= sy_num,
                _ => sx_num >= sy_num,
            };
            let (nw, nh) = if use_x {
                let nw = dest.w;
                let nh = ((dest.w as u64 * crop_h as u64) / crop_w as u64) as u32;
                (nw, nh.max(1))
            } else {
                let nh = dest.h;
                let nw = ((dest.h as u64 * crop_w as u64) / crop_h as u64) as u32;
                (nw.max(1), nh)
            };

            let x = dest.x + (dest.w as i32 - nw as i32) / 2;
            let y = dest.y + (dest.h as i32 - nh as i32) / 2;
            Rect { x, y, w: nw, h: nh }
        }
    }
}

fn fill_rect(out: &mut [u8], ow: u32, oh: u32, pitch: u32, dest: Rect, rgb: [u8; 3], opacity: u8) {
    let (x0, y0, x1, y1) = clip_dest(dest, ow, oh);
    let pitch = pitch as usize;
    for dy in y0..y1 {
        let row = dy as usize * pitch;
        for dx in x0..x1 {
            let o = row + dx as usize * 4;
            if o + 4 <= out.len() {
                blend_px(&mut out[o..o + 4], rgb[0], rgb[1], rgb[2], opacity);
            }
        }
    }
}

fn scale_blit(out: &mut [u8], ow: u32, oh: u32, pitch: u32, view: &SurfaceView, crop: Crop, dest: Rect, opacity: u8) {
    let (x0, y0, x1, y1) = clip_dest(dest, ow, oh);
    if x1 <= x0 || y1 <= y0 {
        return;
    }
    let pitch = pitch as usize;
    let sstride = view.stride as usize;
    let dw = dest.w as u64;
    let dh = dest.h as u64;
    for dy in y0..y1 {

        let dy_rel = (dy as i64 - dest.y as i64) as u64;
        let sy = crop.y as u64 + dy_rel * crop.h as u64 / dh;
        let srow = view.off + (sy as usize) * sstride;
        let orow = dy as usize * pitch;
        for dx in x0..x1 {
            let dx_rel = (dx as i64 - dest.x as i64) as u64;
            let sx = crop.x as u64 + dx_rel * crop.w as u64 / dw;
            let so = srow + (sx as usize) * 4;
            let dofs = orow + dx as usize * 4;
            if so + 4 <= view.bytes.len() && dofs + 4 <= out.len() {
                let s = &view.bytes[so..so + 4];

                let sa = if view.has_alpha { s[3] } else { 255 };
                let a = (sa as u16 * opacity as u16 / 255) as u8;
                blend_px(&mut out[dofs..dofs + 4], s[2], s[1], s[0], a);
            }
        }
    }
}

fn clip_dest(dest: Rect, ow: u32, oh: u32) -> (u32, u32, u32, u32) {
    let x0 = dest.x.max(0) as u32;
    let y0 = dest.y.max(0) as u32;
    let x1 = (dest.x as i64 + dest.w as i64).clamp(0, ow as i64) as u32;
    let y1 = (dest.y as i64 + dest.h as i64).clamp(0, oh as i64) as u32;
    (x0.min(ow), y0.min(oh), x1, y1)
}

#[inline]
fn blend_px(px: &mut [u8], r: u8, g: u8, b: u8, a: u8) {
    match a {
        0 => {}
        255 => {
            px[0] = b;
            px[1] = g;
            px[2] = r;
            px[3] = 0xff;
        }
        _ => {
            let ia = 255 - a as u16;
            px[0] = ((b as u16 * a as u16 + px[0] as u16 * ia) / 255) as u8;
            px[1] = ((g as u16 * a as u16 + px[1] as u16 * ia) / 255) as u8;
            px[2] = ((r as u16 * a as u16 + px[2] as u16 * ia) / 255) as u8;
            px[3] = 0xff;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn out(w: u32, h: u32) -> Vec<u8> {
        vec![0u8; (w * h * 4) as usize]
    }
    fn px(buf: &[u8], w: u32, x: u32, y: u32) -> [u8; 4] {
        let o = (y * w + x) as usize * 4;
        [buf[o], buf[o + 1], buf[o + 2], buf[o + 3]]
    }

    fn surf(w: u32, h: u32, r: u8, g: u8, b: u8) -> Vec<u8> {
        let mut v = vec![0u8; (w * h * 4) as usize];
        for p in v.chunks_mut(4) {
            p[0] = b;
            p[1] = g;
            p[2] = r;
            p[3] = 0xff;
        }
        v
    }

    #[test]
    fn solid_fill_clipped_and_offscreen_safe() {
        let mut o = out(4, 4);

        let mut s = vec![Placement {
            src: Src::Solid([200, 10, 20]),
            crop: None,
            dest: Rect::new(1, 1, 2, 2),
            fit: Fit::Stretch,
            z: 0,
            opacity: 255,
        }];
        compose_vec(&mut o, 4, 4, 16, &mut s);
        assert_eq!(px(&o, 4, 0, 0), [0, 0, 0, 0], "outside stays untouched");
        assert_eq!(px(&o, 4, 1, 1), [20, 10, 200, 255], "B,G,R,X inside");
        assert_eq!(px(&o, 4, 2, 2), [20, 10, 200, 255]);
        assert_eq!(px(&o, 4, 3, 3), [0, 0, 0, 0], "3,3 outside the 2×2");

        let mut s2 = vec![Placement {
            src: Src::Solid([1, 2, 3]),
            crop: None,
            dest: Rect::new(-5, -5, 100, 100),
            fit: Fit::Stretch,
            z: 0,
            opacity: 255,
        }];
        compose_vec(&mut o, 4, 4, 16, &mut s2);
        assert_eq!(px(&o, 4, 0, 0), [3, 2, 1, 255], "clipped fill covers on-screen part");
    }

    #[test]
    fn surface_1to1_and_crop() {

        let g = surf(4, 4, 0, 180, 0);
        let view = SurfaceView { bytes: &g, off: 0, w: 4, h: 4, stride: 16, has_alpha: true };
        let mut o = out(4, 4);
        let mut s = vec![Placement {
            src: Src::Surface(view),
            crop: Some(Crop { x: 1, y: 1, w: 2, h: 2 }),
            dest: Rect::new(0, 0, 2, 2),
            fit: Fit::None,
            z: 0,
            opacity: 255,
        }];
        compose_vec(&mut o, 4, 4, 16, &mut s);
        assert_eq!(px(&o, 4, 0, 0), [0, 180, 0, 255], "cutout drawn");
        assert_eq!(px(&o, 4, 1, 1), [0, 180, 0, 255]);
        assert_eq!(px(&o, 4, 2, 2), [0, 0, 0, 0], "1:1 crop is only 2×2");
    }

    #[test]
    fn surface_scale_up_stretch() {

        let b = surf(2, 2, 0, 0, 240);
        let view = SurfaceView { bytes: &b, off: 0, w: 2, h: 2, stride: 8, has_alpha: false };
        let mut o = out(4, 4);
        let mut s = vec![Placement {
            src: Src::Surface(view),
            crop: None,
            dest: Rect::new(0, 0, 4, 4),
            fit: Fit::Stretch,
            z: 0,
            opacity: 255,
        }];
        compose_vec(&mut o, 4, 4, 16, &mut s);
        for y in 0..4 {
            for x in 0..4 {
                assert_eq!(px(&o, 4, x, y), [240, 0, 0, 255], "every upscaled px is blue");
            }
        }
    }

    #[test]
    fn zorder_and_opacity_blend() {
        let mut o = out(2, 2);

        let mut s = vec![
            Placement { src: Src::Solid([255, 0, 0]), crop: None, dest: Rect::new(0, 0, 2, 2), fit: Fit::Stretch, z: 0, opacity: 255 },
            Placement { src: Src::Solid([255, 255, 255]), crop: None, dest: Rect::new(0, 0, 2, 2), fit: Fit::Stretch, z: 1, opacity: 128 },
        ];
        compose_vec(&mut o, 2, 2, 8, &mut s);
        let p = px(&o, 2, 0, 0);

        assert!(p[2] >= 250, "R high");
        assert!((120..=140).contains(&(p[1] as i32)), "G ~128, got {}", p[1]);
        assert!((120..=140).contains(&(p[0] as i32)), "B ~128, got {}", p[0]);
    }

    #[test]
    fn contain_letterboxes_preserving_aspect() {

        let src = surf(2, 1, 10, 20, 30);
        let view = SurfaceView { bytes: &src, off: 0, w: 2, h: 1, stride: 8, has_alpha: false };
        let d = fit_dest(Fit::Contain, view.w, view.h, Rect::new(0, 0, 4, 4));
        assert_eq!(d, Rect::new(0, 1, 4, 2), "contain preserves 2:1 aspect, centred");
    }

    #[test]
    fn absurd_geometry_never_panics() {

        let tiny = surf(1, 1, 9, 9, 9);
        let view = SurfaceView { bytes: &tiny, off: 0, w: 10_000, h: 10_000, stride: 40_000, has_alpha: true };
        let mut o = out(8, 8);
        let mut s = vec![Placement {
            src: Src::Surface(view),
            crop: Some(Crop { x: 5000, y: 5000, w: 9999, h: 9999 }),
            dest: Rect::new(-3, -3, 9_999, 9_999),
            fit: Fit::Cover,
            z: 0,
            opacity: 200,
        }];
        compose_vec(&mut o, 8, 8, 32, &mut s);
    }
}
