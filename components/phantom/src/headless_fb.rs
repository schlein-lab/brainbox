#![allow(dead_code)]

use std::io;
use std::sync::{Arc, Mutex};

struct FrameSlot {
    rgba: Vec<u8>,
    width: u32,
    height: u32,

    generation: u64,
}

#[derive(Clone)]
pub struct FrameTap {
    slot: Arc<Mutex<FrameSlot>>,
}

impl FrameTap {

    pub fn snapshot(&self) -> Option<(Vec<u8>, u32, u32, u64)> {
        let s = self.slot.lock().ok()?;
        if s.generation == 0 {
            return None;
        }
        Some((s.rgba.clone(), s.width, s.height, s.generation))
    }

    pub fn generation(&self) -> u64 {
        self.slot.lock().map(|s| s.generation).unwrap_or(0)
    }
}

pub struct HeadlessFb {
    pub width: u32,
    pub height: u32,
    pub pitch: u32,

    buf: Vec<u8>,

    frame: u64,

    tap: Arc<Mutex<FrameSlot>>,
}

impl HeadlessFb {

    pub fn open(width: u32, height: u32) -> io::Result<HeadlessFb> {
        let pitch = width.saturating_mul(4);
        let len = (pitch as usize).saturating_mul(height as usize);
        let tap = Arc::new(Mutex::new(FrameSlot { rgba: Vec::new(), width, height, generation: 0 }));
        Ok(HeadlessFb { width, height, pitch, buf: vec![0u8; len], frame: 0, tap })
    }

    pub fn frame_tap(&self) -> FrameTap {
        FrameTap { slot: self.tap.clone() }
    }

    pub fn open_from_env() -> io::Result<HeadlessFb> {
        let (w, h) = std::env::var("PHANTOM_HEADLESS_SIZE")
            .ok()
            .and_then(|s| {
                let (a, b) = s.split_once(['x', 'X'])?;
                Some((a.trim().parse().ok()?, b.trim().parse().ok()?))
            })
            .unwrap_or((1920u32, 1080u32));
        HeadlessFb::open(w.max(1), h.max(1))
    }

    #[inline]
    pub fn put(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8) {
        if x >= self.width || y >= self.height {
            return;
        }

        let off = y as usize * self.pitch as usize + x as usize * 4;
        let buf = &mut self.buf;
        if off + 4 <= buf.len() {

            buf[off] = b;
            buf[off + 1] = g;
            buf[off + 2] = r;
            buf[off + 3] = 0xff;
        }
    }

    pub fn clear(&mut self, r: u8, g: u8, b: u8) {
        let (w, h, pitch) = (self.width as usize, self.height as usize, self.pitch as usize);
        let buf = &mut self.buf;
        for y in 0..h {
            let row = y * pitch;
            for x in 0..w {
                let p = row + x * 4;
                if p + 4 <= buf.len() {
                    buf[p] = b;
                    buf[p + 1] = g;
                    buf[p + 2] = r;
                    buf[p + 3] = 0xff;
                }
            }
        }
    }

    pub fn blit_bgra(&mut self, ox: u32, oy: u32, src: &[u8], src_off: usize, src_w: u32, src_h: u32, src_stride: u32) {
        if ox >= self.width || oy >= self.height {
            return;
        }
        let copy_w = src_w.min(self.width - ox) as usize * 4;
        let (pitch, screen_h) = (self.pitch as usize, self.height);
        let buf = &mut self.buf;
        for y in 0..src_h {
            let dy = oy + y;
            if dy >= screen_h {
                break;
            }
            let drow = dy as usize * pitch + ox as usize * 4;
            let srow = src_off + y as usize * src_stride as usize;
            if srow + copy_w <= src.len() && drow + copy_w <= buf.len() {
                buf[drow..drow + copy_w].copy_from_slice(&src[srow..srow + copy_w]);
            }
        }
    }

    pub fn snapshot_rgba(&self) -> Vec<u8> {
        let buf = &self.buf;
        let (w, h, pitch) = (self.width as usize, self.height as usize, self.pitch as usize);
        let mut out = vec![0u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let s = y * pitch + x * 4;
                let d = (y * w + x) * 4;
                if s + 4 <= buf.len() {
                    out[d] = buf[s + 2];
                    out[d + 1] = buf[s + 1];
                    out[d + 2] = buf[s];
                    out[d + 3] = 0xff;
                }
            }
        }
        out
    }

    pub fn present(&mut self) {
        self.frame = self.frame.wrapping_add(1);

        let rgba = self.snapshot_rgba();
        if let Ok(mut s) = self.tap.lock() {
            s.rgba = rgba;
            s.width = self.width;
            s.height = self.height;
            s.generation = self.frame;
        }
    }

    pub fn frame(&self) -> u64 {
        self.frame
    }

    pub fn raw(&self) -> &[u8] {
        &self.buf
    }

    pub fn map_mut(&mut self) -> &mut [u8] {
        &mut self.buf
    }
}
