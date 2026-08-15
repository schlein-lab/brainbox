use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use phantom::scene::{self, Crop, Fit, Placement, Rect, Src, SurfaceView};
use phantom::sys;

use super::pick::buffer_for_cid;
use crate::{BufferInfo, Shared};

#[derive(Clone, Copy, Debug)]
pub enum SourceKind {
    Client(u64),
    Solid([u8; 3]),
}

#[derive(Clone, Copy, Debug)]
pub struct PlacementSpec {
    pub src: SourceKind,
    pub crop: Option<Crop>,
    pub dest: Rect,
    pub fit: Fit,
    pub z: i32,
    pub opacity: u8,
}

static SCENE: Mutex<Vec<PlacementSpec>> = Mutex::new(Vec::new());

pub fn scene_active() -> bool {
    SCENE.lock().map(|s| !s.is_empty()).unwrap_or(false)
}

pub fn scene_len() -> usize {
    SCENE.lock().map(|s| s.len()).unwrap_or(0)
}

pub fn scene_snapshot() -> Option<Vec<PlacementSpec>> {
    let s = SCENE.lock().ok()?;
    if s.is_empty() {
        None
    } else {
        Some(s.clone())
    }
}

pub fn scene_add(spec: PlacementSpec) {
    if let Ok(mut s) = SCENE.lock() {
        s.push(spec);
    }
    super::state::FOCUS_DIRTY.store(true, Ordering::Relaxed);
}

pub fn scene_clear() {
    if let Ok(mut s) = SCENE.lock() {
        s.clear();
    }
    super::state::FOCUS_DIRTY.store(true, Ordering::Relaxed);
}

pub fn scene_describe() -> String {
    let s = match SCENE.lock() {
        Ok(s) => s,
        Err(_) => return "scene: <poisoned>\n".into(),
    };
    if s.is_empty() {
        return "scene: off (default single-foreground compositing)\n".into();
    }
    let mut out = format!("scene: {} placement(s)\n", s.len());
    for (i, p) in s.iter().enumerate() {
        let src = match p.src {
            SourceKind::Client(c) => format!("cid={c}"),
            SourceKind::Solid([r, g, b]) => format!("solid #{r:02x}{g:02x}{b:02x}"),
        };
        let crop = match p.crop {
            Some(c) => format!("crop {},{} {}x{}", c.x, c.y, c.w, c.h),
            None => "crop full".into(),
        };
        out.push_str(&format!(
            "  [{i}] {src}  {crop}  -> {},{} {}x{}  fit={:?} z={} op={}\n",
            p.dest.x, p.dest.y, p.dest.w, p.dest.h, p.fit, p.z, p.opacity
        ));
    }
    out
}

pub fn compose_scene(map: &mut [u8], pitch: u32, sw: u32, sh: u32, shared: &Shared, specs: &[PlacementSpec]) {

    let holders: Vec<Option<(Arc<sys::Mmap>, BufferInfo)>> = specs
        .iter()
        .map(|ps| match ps.src {
            SourceKind::Client(cid) => buffer_for_cid(shared, cid),
            SourceKind::Solid(_) => None,
        })
        .collect();

    let mut placements: Vec<Placement> = Vec::with_capacity(specs.len());
    for (ps, hold) in specs.iter().zip(holders.iter()) {
        let src = match (&ps.src, hold) {
            (SourceKind::Solid(rgb), _) => Src::Solid(*rgb),
            (SourceKind::Client(_), Some((m, info))) => Src::Surface(SurfaceView {
                bytes: m.as_slice(),
                off: info.offset.max(0) as usize,
                w: info.width.max(0) as u32,
                h: info.height.max(0) as u32,
                stride: info.stride.max(0) as u32,

                has_alpha: info.format == 0,
            }),
            (SourceKind::Client(_), None) => continue,
        };
        placements.push(Placement {
            src,
            crop: ps.crop,
            dest: ps.dest,
            fit: ps.fit,
            z: ps.z,
            opacity: ps.opacity,
        });
    }

    scene::compose_vec(map, sw, sh, pitch, &mut placements);
}
