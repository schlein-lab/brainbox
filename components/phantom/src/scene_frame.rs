use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use phantom::headless_fb::FrameTap;
use phantom::render_placement::{decide_placement, ClientCaps, PlacementPolicy, ServerCaps};
use phantom::streamd::SceneSource;

use crate::compositor::foreground_scene_layers;
use crate::state::BufferInfo;
use crate::Shared;

pub const SCENE_MAGIC: [u8; 4] = [0x50, 0x53, 0x4E, 0x31];

pub const SCENE_VERSION: u16 = 1;

pub const SCENE_HEADER_LEN: usize = 28;

pub const FLAG_KEYFRAME: u16 = 0x0001;

pub const SFLAG_HAS_PIXELS: u8 = 0x0001;

pub const FORMAT_SHM_BGRA: u8 = 0;

static FRAME_SEQ: AtomicU32 = AtomicU32::new(0);

fn next_frame_seq() -> u32 {
    FRAME_SEQ.fetch_add(1, Ordering::Relaxed)
}

static LAST_KEYFRAME_MS: AtomicU64 = AtomicU64::new(0);

static HAS_EMITTED: AtomicBool = AtomicBool::new(false);

pub const KEYFRAME_INTERVAL_MS_DEFAULT: u64 = 3000;

fn keyframe_interval_ms() -> u64 {
    std::env::var("PHANTOM_SCENE_KEYFRAME_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(KEYFRAME_INTERVAL_MS_DEFAULT)
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

#[inline]
fn put_u16(buf: &mut Vec<u8>, v: u16) {
    buf.extend_from_slice(&v.to_le_bytes());
}
#[inline]
fn put_i16(buf: &mut Vec<u8>, v: i16) {
    buf.extend_from_slice(&v.to_le_bytes());
}
#[inline]
fn put_u32(buf: &mut Vec<u8>, v: u32) {
    buf.extend_from_slice(&v.to_le_bytes());
}

fn pack_surface_pixels(data: &[u8], info: &BufferInfo) -> Vec<u8> {
    let w = info.width.max(0) as usize;
    let h = info.height.max(0) as usize;
    let stride = info.stride.max(0) as usize;
    let start = info.offset.max(0) as usize;
    let mut out = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        let row = start + y * stride;
        let end = row + w * 4;
        if end <= data.len() {
            out.extend_from_slice(&data[row..end]);
        } else {

            out.resize(out.len() + w * 4, 0);
        }
    }
    out
}

fn coalesce_damage(rects: &[(i32, i32, i32, i32)], surf_w: i32, surf_h: i32) -> Option<(u16, u16, u16, u16)> {
    if surf_w <= 0 || surf_h <= 0 {
        return None;
    }
    let mut min_x = i32::MAX;
    let mut min_y = i32::MAX;
    let mut max_x = i32::MIN;
    let mut max_y = i32::MIN;
    for &(x, y, w, h) in rects {
        if w <= 0 || h <= 0 {
            continue;
        }
        min_x = min_x.min(x);
        min_y = min_y.min(y);
        max_x = max_x.max(x.saturating_add(w));
        max_y = max_y.max(y.saturating_add(h));
    }
    if min_x == i32::MAX {
        return None;
    }

    let x0 = min_x.clamp(0, surf_w);
    let y0 = min_y.clamp(0, surf_h);
    let x1 = max_x.clamp(0, surf_w);
    let y1 = max_y.clamp(0, surf_h);
    let w = x1 - x0;
    let h = y1 - y0;
    if w <= 0 || h <= 0 {
        return None;
    }
    Some((x0 as u16, y0 as u16, w as u16, h as u16))
}

fn pack_surface_rect(data: &[u8], info: &BufferInfo, rx: u16, ry: u16, rw: u16, rh: u16) -> Vec<u8> {
    let stride = info.stride.max(0) as usize;
    let start = info.offset.max(0) as usize;
    let (rx, ry, rw, rh) = (rx as usize, ry as usize, rw as usize, rh as usize);
    let mut out = Vec::with_capacity(rw * rh * 4);
    for row in 0..rh {
        let y = ry + row;
        let line = start + y * stride + rx * 4;
        let end = line + rw * 4;
        if end <= data.len() {
            out.extend_from_slice(&data[line..end]);
        } else {
            out.resize(out.len() + rw * 4, 0);
        }
    }
    out
}

struct Packed {
    surface_id: u32,
    x: i16,
    y: i16,
    w: u16,
    h: u16,
    stride_px: u16,
    format: u8,

    rect: (u16, u16, u16, u16),
    pixels: Vec<u8>,
}

fn write_surface(buf: &mut Vec<u8>, p: &Packed) {
    put_u32(buf, p.surface_id);
    put_i16(buf, p.x);
    put_i16(buf, p.y);
    put_u16(buf, p.w);
    put_u16(buf, p.h);
    put_u16(buf, p.stride_px);
    buf.push(p.format);
    buf.push(SFLAG_HAS_PIXELS);
    put_u16(buf, 1);
    put_u16(buf, p.rect.0);
    put_u16(buf, p.rect.1);
    put_u16(buf, p.rect.2);
    put_u16(buf, p.rect.3);
    put_u32(buf, p.pixels.len() as u32);
    buf.extend_from_slice(&p.pixels);
}

fn assemble(packed: &[Packed], keyframe: bool, screen_w: u16, screen_h: u16, seat_generation: u32) -> Vec<u8> {
    let total_pixel_bytes: u32 = packed.iter().fold(0u32, |a, p| a.saturating_add(p.pixels.len() as u32));
    let surface_count = packed.len().min(u16::MAX as usize) as u16;
    let flags = if keyframe { FLAG_KEYFRAME } else { 0 };
    let mut buf: Vec<u8> = Vec::with_capacity(SCENE_HEADER_LEN + total_pixel_bytes as usize + packed.len() * 24);

    buf.extend_from_slice(&SCENE_MAGIC);
    put_u16(&mut buf, SCENE_VERSION);
    put_u16(&mut buf, flags);
    put_u32(&mut buf, seat_generation);
    put_u32(&mut buf, next_frame_seq());
    put_u16(&mut buf, screen_w);
    put_u16(&mut buf, screen_h);
    put_u16(&mut buf, surface_count);
    put_u16(&mut buf, 0);
    put_u32(&mut buf, total_pixel_bytes);
    for p in packed {
        write_surface(&mut buf, p);
    }
    buf
}

#[allow(dead_code)]
pub fn build_scene_frame(shared: &Shared, screen_w: u16, screen_h: u16, seat_generation: u32) -> Option<Vec<u8>> {
    build_scene_frame_ex(shared, screen_w, screen_h, seat_generation, false, "_legacy")
}

pub fn build_scene_frame_ex(
    shared: &Shared,
    screen_w: u16,
    screen_h: u16,
    seat_generation: u32,
    force_keyframe: bool,
    leser: &str,
) -> Option<Vec<u8>> {
    let layers = foreground_scene_layers(shared);
    if layers.is_empty() {
        return None;
    }

    
    
    let damage = crate::compositor::damage_seit(shared, leser);

    let now = now_ms();
    let interval = keyframe_interval_ms();
    let last_kf = LAST_KEYFRAME_MS.load(Ordering::Relaxed);
    let first = !HAS_EMITTED.load(Ordering::Relaxed);
    let due = last_kf == 0 || now.saturating_sub(last_kf) >= interval;
    let keyframe = force_keyframe || first || due;

    let packed: Vec<Packed> = if keyframe {

        layers
            .iter()
            .map(|l| {
                let w = l.info.width.clamp(0, u16::MAX as i32) as u16;
                let h = l.info.height.clamp(0, u16::MAX as i32) as u16;
                Packed {
                    surface_id: l.surface_id,
                    x: l.x.clamp(i16::MIN as i32, i16::MAX as i32) as i16,
                    y: l.y.clamp(i16::MIN as i32, i16::MAX as i32) as i16,
                    w,
                    h,

                    stride_px: (l.info.stride.max(0) / 4).clamp(0, u16::MAX as i32) as u16,
                    format: FORMAT_SHM_BGRA,
                    rect: (0, 0, w, h),
                    pixels: pack_surface_pixels(l.mmap.as_slice(), &l.info),
                }
            })
            .collect()
    } else {

        let mut out: Vec<Packed> = Vec::new();
        for l in &layers {
            let Some(rects) = damage.get(&l.surface_id) else { continue };
            let Some((rx, ry, rw, rh)) = coalesce_damage(rects, l.info.width, l.info.height) else {
                continue;
            };
            let w = l.info.width.clamp(0, u16::MAX as i32) as u16;
            let h = l.info.height.clamp(0, u16::MAX as i32) as u16;
            out.push(Packed {
                surface_id: l.surface_id,
                x: l.x.clamp(i16::MIN as i32, i16::MAX as i32) as i16,
                y: l.y.clamp(i16::MIN as i32, i16::MAX as i32) as i16,
                w,
                h,
                stride_px: (l.info.stride.max(0) / 4).clamp(0, u16::MAX as i32) as u16,
                format: FORMAT_SHM_BGRA,
                rect: (rx, ry, rw, rh),
                pixels: pack_surface_rect(l.mmap.as_slice(), &l.info, rx, ry, rw, rh),
            });
        }

        if out.is_empty() {
            return None;
        }
        out
    };

    if keyframe {
        LAST_KEYFRAME_MS.store(now, Ordering::Relaxed);
    }
    HAS_EMITTED.store(true, Ordering::Relaxed);

    Some(assemble(&packed, keyframe, screen_w, screen_h, seat_generation))
}

pub struct BoxSceneSource {
    shared: Shared,
    tap: FrameTap,
    screen_w: u16,
    screen_h: u16,
}

impl BoxSceneSource {

    pub fn new(shared: Shared, tap: FrameTap, screen_w: u16, screen_h: u16) -> BoxSceneSource {
        BoxSceneSource { shared, tap, screen_w, screen_h }
    }
}

fn mem_available_mb() -> u32 {
    if let Ok(s) = std::fs::read_to_string("/proc/meminfo") {
        for line in s.lines() {
            if let Some(rest) = line.strip_prefix("MemAvailable:") {
                if let Some(kb) = rest.split_whitespace().next().and_then(|v| v.parse::<u64>().ok()) {
                    return (kb / 1024) as u32;
                }
            }
        }
    }
    512
}

fn load_avg1() -> f32 {
    std::fs::read_to_string("/proc/loadavg")
        .ok()
        .and_then(|s| s.split_whitespace().next().and_then(|v| v.parse::<f32>().ok()))
        .unwrap_or(0.0)
}

fn nproc() -> u32 {
    std::thread::available_parallelism().map(|n| n.get() as u32).unwrap_or(1)
}

fn client_caps_from_query(query: &str) -> ClientCaps {
    let mut webgl2 = false;
    let mut webgpu = false;
    let mut cores: u32 = 4;
    let mut mem_mb: u32 = 4096;
    let mut hw_decode = false;
    let mut native = false;
    let mut throttled = false;
    for kv in query.split('&') {
        let (k, v) = match kv.split_once('=') {
            Some(p) => p,
            None => continue,
        };
        let boolish = v == "1" || v.eq_ignore_ascii_case("true");
        match k {
            "webgl2" => webgl2 = boolish,
            "webgpu" => webgpu = boolish,
            "cores" => cores = v.parse().unwrap_or(cores),
            "mem_mb" => mem_mb = v.parse().unwrap_or(mem_mb),
            "hw_decode" => hw_decode = boolish,
            "native" => native = boolish,
            "throttled" => throttled = boolish,
            _ => {}
        }
    }
    ClientCaps { webgl2, webgpu, cores, mem_mb, hw_decode, native, throttled }
}

impl SceneSource for BoxSceneSource {
    fn scene_frame(&self, force_keyframe: bool, leser: &str) -> Option<Vec<u8>> {
        build_scene_frame_ex(&self.shared, self.screen_w, self.screen_h, self.seat_generation(), force_keyframe, leser)
    }

    fn seat_generation(&self) -> u32 {

        self.tap.generation() as u32
    }

    fn screen_size(&self) -> (u16, u16) {
        (self.screen_w, self.screen_h)
    }

    fn placement_json(&self, query: &str) -> String {

        let server = ServerCaps::gpuless(nproc(), mem_available_mb(), load_avg1());
        let client = client_caps_from_query(query);
        let mode = decide_placement(&server, &client, PlacementPolicy::Auto);
        format!(
            "{{\"mode\":\"{}\",\"screen\":{{\"w\":{},\"h\":{}}}}}",
            mode.as_str(),
            self.screen_w,
            self.screen_h
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode_one_surface(
        seat_generation: u32,
        frame_seq: u32,
        screen_w: u16,
        screen_h: u16,
        surface_id: u32,
        x: i16,
        y: i16,
        w: u16,
        h: u16,
        pixels: &[u8],
    ) -> Vec<u8> {
        let mut buf = Vec::new();
        buf.extend_from_slice(&SCENE_MAGIC);
        put_u16(&mut buf, SCENE_VERSION);
        put_u16(&mut buf, FLAG_KEYFRAME);
        put_u32(&mut buf, seat_generation);
        put_u32(&mut buf, frame_seq);
        put_u16(&mut buf, screen_w);
        put_u16(&mut buf, screen_h);
        put_u16(&mut buf, 1);
        put_u16(&mut buf, 0);
        put_u32(&mut buf, pixels.len() as u32);
        put_u32(&mut buf, surface_id);
        put_i16(&mut buf, x);
        put_i16(&mut buf, y);
        put_u16(&mut buf, w);
        put_u16(&mut buf, h);
        put_u16(&mut buf, w);
        buf.push(FORMAT_SHM_BGRA);
        buf.push(SFLAG_HAS_PIXELS);
        put_u16(&mut buf, 1);
        put_u16(&mut buf, 0);
        put_u16(&mut buf, 0);
        put_u16(&mut buf, w);
        put_u16(&mut buf, h);
        put_u32(&mut buf, pixels.len() as u32);
        buf.extend_from_slice(pixels);
        buf
    }

    #[test]
    fn scene_frame_header_and_one_surface() {

        let w: u16 = 2;
        let h: u16 = 1;
        let pixels: [u8; 8] = [
            0xFF, 0x00, 0x00, 0xFF,
            0x00, 0x00, 0xFF, 0xFF,
        ];
        let frame = encode_one_surface(7, 42, 960, 600, 3, -5, 10, w, h, &pixels);

        assert_eq!(&frame[0..4], &SCENE_MAGIC, "magic 'PSN1' in stream order");
        assert_eq!(u16::from_le_bytes([frame[4], frame[5]]), SCENE_VERSION);
        let flags = u16::from_le_bytes([frame[6], frame[7]]);
        assert_eq!(flags & FLAG_KEYFRAME, FLAG_KEYFRAME, "slice build is always a keyframe");
        assert_eq!(u32::from_le_bytes([frame[8], frame[9], frame[10], frame[11]]), 7, "seat_generation");
        assert_eq!(u32::from_le_bytes([frame[12], frame[13], frame[14], frame[15]]), 42, "frame_seq");
        assert_eq!(u16::from_le_bytes([frame[16], frame[17]]), 960, "screen_w");
        assert_eq!(u16::from_le_bytes([frame[18], frame[19]]), 600, "screen_h");
        assert_eq!(u16::from_le_bytes([frame[20], frame[21]]), 1, "surface_count");
        assert_eq!(u16::from_le_bytes([frame[22], frame[23]]), 0, "reserved");
        assert_eq!(
            u32::from_le_bytes([frame[24], frame[25], frame[26], frame[27]]),
            pixels.len() as u32,
            "total_pixel_bytes"
        );

        let mut o = SCENE_HEADER_LEN;
        assert_eq!(u32::from_le_bytes([frame[o], frame[o + 1], frame[o + 2], frame[o + 3]]), 3, "surface_id");
        o += 4;
        assert_eq!(i16::from_le_bytes([frame[o], frame[o + 1]]), -5, "x (signed)");
        o += 2;
        assert_eq!(i16::from_le_bytes([frame[o], frame[o + 1]]), 10, "y");
        o += 2;
        assert_eq!(u16::from_le_bytes([frame[o], frame[o + 1]]), w, "w");
        o += 2;
        assert_eq!(u16::from_le_bytes([frame[o], frame[o + 1]]), h, "h");
        o += 2;
        assert_eq!(u16::from_le_bytes([frame[o], frame[o + 1]]), w, "stride_px");
        o += 2;
        assert_eq!(frame[o], FORMAT_SHM_BGRA, "format = shm BGRA");
        o += 1;
        assert_eq!(frame[o] & SFLAG_HAS_PIXELS, SFLAG_HAS_PIXELS, "has_pixels");
        o += 1;
        assert_eq!(u16::from_le_bytes([frame[o], frame[o + 1]]), 1, "damage_count");
        o += 2;

        assert_eq!(u16::from_le_bytes([frame[o], frame[o + 1]]), 0);
        assert_eq!(u16::from_le_bytes([frame[o + 2], frame[o + 3]]), 0);
        assert_eq!(u16::from_le_bytes([frame[o + 4], frame[o + 5]]), w);
        assert_eq!(u16::from_le_bytes([frame[o + 6], frame[o + 7]]), h);
        o += 8;
        let pixel_bytes = u32::from_le_bytes([frame[o], frame[o + 1], frame[o + 2], frame[o + 3]]) as usize;
        o += 4;
        assert_eq!(pixel_bytes, pixels.len(), "pixel_bytes == w*h*4");
        assert_eq!(&frame[o..o + pixel_bytes], &pixels, "pixels travel in shm BGRA order, unconverted");
        assert_eq!(o + pixel_bytes, frame.len(), "no trailing bytes");
    }

    #[test]
    fn scene_frame_seq_is_monotonic() {

        let a = next_frame_seq();
        let b = next_frame_seq();
        assert_eq!(b, a.wrapping_add(1), "frame_seq is monotonic");
    }

    #[test]
    fn scene_coalesce_damage_bounding_union() {

        let rects = [(10, 20, 5, 5), (30, 40, 8, 8)];
        let r = coalesce_damage(&rects, 100, 100).expect("union");

        assert_eq!(r, (10, 20, 28, 28));
    }

    #[test]
    fn scene_coalesce_damage_clamps_to_bounds() {

        let rects = [(-5, -5, 200, 200)];
        let r = coalesce_damage(&rects, 64, 48).expect("clamped");
        assert_eq!(r, (0, 0, 64, 48), "clamped to surface bounds");

        assert_eq!(coalesce_damage(&[], 64, 48), None);
        assert_eq!(coalesce_damage(&[(0, 0, 0, 0)], 64, 48), None);
        assert_eq!(coalesce_damage(&[(10, 10, 4, 4)], 0, 0), None, "zero-size surface");
    }

    #[test]
    fn scene_pack_surface_rect_extracts_window() {

        let mut data = vec![0u8; 32];
        for (i, b) in data.iter_mut().enumerate() {
            *b = i as u8;
        }
        let info = BufferInfo { pool: 0, offset: 0, width: 4, height: 2, stride: 16, format: 0 };

        let out = pack_surface_rect(&data, &info, 2, 0, 2, 2);
        assert_eq!(out.len(), 2 * 2 * 4);

        assert_eq!(&out[0..8], &data[8..16]);
        assert_eq!(&out[8..16], &data[24..32]);
    }

    #[test]
    fn scene_assemble_keyframe_vs_delta_flag() {
        let px = vec![1u8, 2, 3, 4];
        let p = Packed {
            surface_id: 5,
            x: 0,
            y: 0,
            w: 1,
            h: 1,
            stride_px: 1,
            format: FORMAT_SHM_BGRA,
            rect: (0, 0, 1, 1),
            pixels: px.clone(),
        };

        let kf = assemble(std::slice::from_ref(&p), true, 960, 600, 3);
        assert_eq!(u16::from_le_bytes([kf[6], kf[7]]) & FLAG_KEYFRAME, FLAG_KEYFRAME);

        let df = assemble(std::slice::from_ref(&p), false, 960, 600, 3);
        assert_eq!(u16::from_le_bytes([df[6], df[7]]) & FLAG_KEYFRAME, 0, "delta clears keyframe bit");
        assert_eq!(u16::from_le_bytes([df[20], df[21]]), 1, "one surface");

        let o = SCENE_HEADER_LEN;
        assert_eq!(u32::from_le_bytes([df[o], df[o + 1], df[o + 2], df[o + 3]]), 5, "surface_id");
    }

    #[test]
    fn scene_delta_serializes_partial_rect() {

        let dirty = vec![9u8, 9, 9, 9, 8, 8, 8, 8];
        let p = Packed {
            surface_id: 7,
            x: 4,
            y: 6,
            w: 100,
            h: 80,
            stride_px: 100,
            format: FORMAT_SHM_BGRA,
            rect: (10, 12, 2, 1),
            pixels: dirty.clone(),
        };
        let df = assemble(std::slice::from_ref(&p), false, 960, 600, 1);

        assert_eq!(u32::from_le_bytes([df[24], df[25], df[26], df[27]]) as usize, dirty.len());
        let mut o = SCENE_HEADER_LEN;
        o += 4;
        assert_eq!(i16::from_le_bytes([df[o], df[o + 1]]), 4, "x");
        o += 2;
        assert_eq!(i16::from_le_bytes([df[o], df[o + 1]]), 6, "y");
        o += 2;
        assert_eq!(u16::from_le_bytes([df[o], df[o + 1]]), 100, "full surface w travels");
        o += 2 + 2 + 2 + 1 + 1;
        assert_eq!(u16::from_le_bytes([df[o], df[o + 1]]), 1, "damage_count");
        o += 2;
        assert_eq!(u16::from_le_bytes([df[o], df[o + 1]]), 10, "rect.x");
        assert_eq!(u16::from_le_bytes([df[o + 2], df[o + 3]]), 12, "rect.y");
        assert_eq!(u16::from_le_bytes([df[o + 4], df[o + 5]]), 2, "rect.w");
        assert_eq!(u16::from_le_bytes([df[o + 6], df[o + 7]]), 1, "rect.h");
        o += 8;
        let pb = u32::from_le_bytes([df[o], df[o + 1], df[o + 2], df[o + 3]]) as usize;
        o += 4;
        assert_eq!(&df[o..o + pb], &dirty[..], "only the dirty pixels ship");
    }

    #[test]
    fn scene_keyframe_interval_env() {

        std::env::remove_var("PHANTOM_SCENE_KEYFRAME_MS");
        assert_eq!(keyframe_interval_ms(), KEYFRAME_INTERVAL_MS_DEFAULT);
        std::env::set_var("PHANTOM_SCENE_KEYFRAME_MS", "500");
        assert_eq!(keyframe_interval_ms(), 500);
        std::env::remove_var("PHANTOM_SCENE_KEYFRAME_MS");
    }
}
