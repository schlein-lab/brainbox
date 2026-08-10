#![allow(dead_code)]

use crate::sys::{ioctl_val, MmapMut};
use std::fs::{File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;

const DIR_NONE: u32 = 0;
const DIR_WRITE: u32 = 1;
const DIR_READ: u32 = 2;
const DRM_BASE: u32 = 0x64;

#[inline]
const fn ioc(dir: u32, typ: u32, nr: u32, size: u32) -> u64 {
    ((dir << 30) | (size << 16) | (typ << 8) | nr) as u64
}
#[inline]
fn drm_io(nr: u32) -> u64 {
    ioc(DIR_NONE, DRM_BASE, nr, 0)
}
#[inline]
fn drm_iowr<T>(nr: u32) -> u64 {
    ioc(DIR_READ | DIR_WRITE, DRM_BASE, nr, std::mem::size_of::<T>() as u32)
}

const NR_SET_MASTER: u32 = 0x1e;
const NR_DROP_MASTER: u32 = 0x1f;
const NR_GETRESOURCES: u32 = 0xA0;
const NR_GETCRTC: u32 = 0xA1;
const NR_SETCRTC: u32 = 0xA2;
const NR_GETENCODER: u32 = 0xA6;
const NR_GETCONNECTOR: u32 = 0xA7;
const NR_ADDFB: u32 = 0xAE;
const NR_RMFB: u32 = 0xAF;
const NR_PAGE_FLIP: u32 = 0xB0;
const NR_DIRTYFB: u32 = 0xB1;
const NR_CREATE_DUMB: u32 = 0xB2;
const NR_MAP_DUMB: u32 = 0xB3;
const NR_DESTROY_DUMB: u32 = 0xB4;

const DRM_MODE_CONNECTED: u32 = 1;

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeCardRes {
    fb_id_ptr: u64,
    crtc_id_ptr: u64,
    connector_id_ptr: u64,
    encoder_id_ptr: u64,
    count_fbs: u32,
    count_crtcs: u32,
    count_connectors: u32,
    count_encoders: u32,
    min_width: u32,
    max_width: u32,
    min_height: u32,
    max_height: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct ModeInfo {
    pub clock: u32,
    pub hdisplay: u16,
    pub hsync_start: u16,
    pub hsync_end: u16,
    pub htotal: u16,
    pub hskew: u16,
    pub vdisplay: u16,
    pub vsync_start: u16,
    pub vsync_end: u16,
    pub vtotal: u16,
    pub vscan: u16,
    pub vrefresh: u32,
    pub flags: u32,
    pub typ: u32,
    pub name: [u8; 32],
}
impl Default for ModeInfo {
    fn default() -> Self {

        unsafe { std::mem::zeroed() }
    }
}
impl ModeInfo {
    pub fn name_str(&self) -> String {
        let end = self.name.iter().position(|&b| b == 0).unwrap_or(self.name.len());
        String::from_utf8_lossy(&self.name[..end]).to_string()
    }
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeGetConnector {
    encoders_ptr: u64,
    modes_ptr: u64,
    props_ptr: u64,
    prop_values_ptr: u64,
    count_modes: u32,
    count_props: u32,
    count_encoders: u32,
    encoder_id: u32,
    connector_id: u32,
    connector_type: u32,
    connector_type_id: u32,
    connection: u32,
    mm_width: u32,
    mm_height: u32,
    subpixel: u32,
    pad: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeGetEncoder {
    encoder_id: u32,
    encoder_type: u32,
    crtc_id: u32,
    possible_crtcs: u32,
    possible_clones: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeCreateDumb {
    height: u32,
    width: u32,
    bpp: u32,
    flags: u32,
    handle: u32,
    pitch: u32,
    size: u64,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeMapDumb {
    handle: u32,
    pad: u32,
    offset: u64,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeFbCmd {
    fb_id: u32,
    width: u32,
    height: u32,
    pitch: u32,
    bpp: u32,
    depth: u32,
    handle: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct ModeFbDirty {
    fb_id: u32,
    flags: u32,
    color: u32,
    num_clips: u32,
    clips_ptr: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct ModeCrtc {
    set_connectors_ptr: u64,
    count_connectors: u32,
    crtc_id: u32,
    fb_id: u32,
    x: u32,
    y: u32,
    gamma_size: u32,
    mode_valid: u32,
    mode: ModeInfo,
}
impl Default for ModeCrtc {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

pub fn drop_master_request() -> u64 {
    drm_io(NR_DROP_MASTER)
}

#[inline]
fn ptr_of<T>(v: &T) -> u64 {
    v as *const T as u64
}
#[inline]
fn mut_ptr_of<T>(v: &mut T) -> u64 {
    v as *mut T as u64
}

pub struct Output {
    pub connector_id: u32,
    pub crtc_id: u32,
    pub mode: ModeInfo,
}

pub struct Display {
    file: File,
    pub width: u32,
    pub height: u32,
    pub pitch: u32,
    pub crtc_id: u32,
    pub connector_id: u32,
    pub mode: ModeInfo,
    fb_id: u32,
    handle: u32,
    pub map: MmapMut,
    saved_crtc: ModeCrtc,
    had_master: bool,
}

impl Display {
    fn fd(&self) -> i32 {
        self.file.as_raw_fd()
    }

    pub fn open(path: &str) -> io::Result<Display> {
        let file = OpenOptions::new().read(true).write(true).open(path)?;
        let fd = file.as_raw_fd();

        let had_master = ioctl_val(fd, drm_io(NR_SET_MASTER), 0).is_ok();
        if !had_master {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("SET_MASTER failed on {path} (another compositor holds the card?)"),
            ));
        }

        let out = pick_output(fd)?;

        let w = out.mode.hdisplay as u32;
        let h = out.mode.vdisplay as u32;
        let mut cd = ModeCreateDumb { width: w, height: h, bpp: 32, ..Default::default() };
        ioctl_val(fd, drm_iowr::<ModeCreateDumb>(NR_CREATE_DUMB), mut_ptr_of(&mut cd))?;

        let mut fb = ModeFbCmd {
            width: w,
            height: h,
            pitch: cd.pitch,
            bpp: 32,
            depth: 24,
            handle: cd.handle,
            fb_id: 0,
        };
        ioctl_val(fd, drm_iowr::<ModeFbCmd>(NR_ADDFB), mut_ptr_of(&mut fb))?;

        let mut md = ModeMapDumb { handle: cd.handle, ..Default::default() };
        ioctl_val(fd, drm_iowr::<ModeMapDumb>(NR_MAP_DUMB), mut_ptr_of(&mut md))?;
        let map = MmapMut::map_rw(fd, cd.size as usize, md.offset as i64)?;

        let mut saved = ModeCrtc { crtc_id: out.crtc_id, ..Default::default() };
        let _ = ioctl_val(fd, drm_iowr::<ModeCrtc>(NR_GETCRTC), mut_ptr_of(&mut saved));

        let connectors = [out.connector_id];
        let mut set = ModeCrtc {
            set_connectors_ptr: connectors.as_ptr() as u64,
            count_connectors: 1,
            crtc_id: out.crtc_id,
            fb_id: fb.fb_id,
            x: 0,
            y: 0,
            gamma_size: 0,
            mode_valid: 1,
            mode: out.mode,
        };
        ioctl_val(fd, drm_iowr::<ModeCrtc>(NR_SETCRTC), mut_ptr_of(&mut set))?;

        Ok(Display {
            file,
            width: w,
            height: h,
            pitch: cd.pitch,
            crtc_id: out.crtc_id,
            connector_id: out.connector_id,
            mode: out.mode,
            fb_id: fb.fb_id,
            handle: cd.handle,
            map,
            saved_crtc: saved,
            had_master,
        })
    }

    pub fn verify_crtc(&self) -> io::Result<(u32, u32, u16, u16)> {
        let mut c = ModeCrtc { crtc_id: self.crtc_id, ..Default::default() };
        ioctl_val(self.fd(), drm_iowr::<ModeCrtc>(NR_GETCRTC), mut_ptr_of(&mut c))?;
        Ok((c.fb_id, c.mode_valid, c.mode.hdisplay, c.mode.vdisplay))
    }

    pub fn fb_id(&self) -> u32 {
        self.fb_id
    }

    pub fn dirty(&self) {
        let mut d = ModeFbDirty { fb_id: self.fb_id, ..Default::default() };
        let _ = ioctl_val(self.fd(), drm_iowr::<ModeFbDirty>(NR_DIRTYFB), mut_ptr_of(&mut d));
    }

    pub fn card_fd(&self) -> i32 {
        self.file.as_raw_fd()
    }

    #[inline]
    pub fn put(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8) {
        if x >= self.width || y >= self.height {
            return;
        }

        let off = y as usize * self.pitch as usize + x as usize * 4;
        let buf = self.map.as_mut_slice();
        if off + 4 <= buf.len() {

            buf[off] = b;
            buf[off + 1] = g;
            buf[off + 2] = r;
            buf[off + 3] = 0xff;
        }
    }

    pub fn clear(&mut self, r: u8, g: u8, b: u8) {
        let (w, h, pitch) = (self.width as usize, self.height as usize, self.pitch as usize);
        let buf = self.map.as_mut_slice();
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
        let buf = self.map.as_mut_slice();
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
        let buf = self.map.as_slice();
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
}

impl Drop for Display {
    fn drop(&mut self) {
        let fd = self.fd();

        let mut fb_id = self.fb_id;
        let _ = ioctl_val(fd, drm_iowr::<u32>(NR_RMFB), mut_ptr_of(&mut fb_id));
        let mut dd = ModeMapDumb { handle: self.handle, ..Default::default() };
        let _ = ioctl_val(fd, drm_iowr::<ModeMapDumb>(NR_DESTROY_DUMB), mut_ptr_of(&mut dd));
        if self.had_master {
            let _ = ioctl_val(fd, drm_io(NR_DROP_MASTER), 0);
        }
    }
}

fn pick_output(fd: i32) -> io::Result<Output> {

    let mut res = ModeCardRes::default();
    ioctl_val(fd, drm_iowr::<ModeCardRes>(NR_GETRESOURCES), mut_ptr_of(&mut res))?;

    let mut crtcs = vec![0u32; res.count_crtcs as usize];
    let mut connectors = vec![0u32; res.count_connectors as usize];
    let mut encoders = vec![0u32; res.count_encoders as usize];
    let mut fbs = vec![0u32; res.count_fbs as usize];
    res.crtc_id_ptr = crtcs.as_mut_ptr() as u64;
    res.connector_id_ptr = connectors.as_mut_ptr() as u64;
    res.encoder_id_ptr = encoders.as_mut_ptr() as u64;
    res.fb_id_ptr = fbs.as_mut_ptr() as u64;
    ioctl_val(fd, drm_iowr::<ModeCardRes>(NR_GETRESOURCES), mut_ptr_of(&mut res))?;

    for &cid in &connectors {
        if cid == 0 {
            continue;
        }

        let mut c = ModeGetConnector { connector_id: cid, ..Default::default() };
        if ioctl_val(fd, drm_iowr::<ModeGetConnector>(NR_GETCONNECTOR), mut_ptr_of(&mut c)).is_err() {
            continue;
        }
        if c.count_modes == 0 {
            continue;
        }
        let mut modes = vec![ModeInfo::default(); c.count_modes as usize];
        let mut conn_encoders = vec![0u32; c.count_encoders as usize];
        c.modes_ptr = modes.as_mut_ptr() as u64;
        c.encoders_ptr = conn_encoders.as_mut_ptr() as u64;
        c.props_ptr = 0;
        c.prop_values_ptr = 0;
        c.count_props = 0;
        if ioctl_val(fd, drm_iowr::<ModeGetConnector>(NR_GETCONNECTOR), mut_ptr_of(&mut c)).is_err() {
            continue;
        }
        if c.connection != DRM_MODE_CONNECTED || c.count_modes == 0 {
            continue;
        }
        let mode = pick_mode(&modes);

        let crtc_id = resolve_crtc(fd, c.encoder_id, &conn_encoders, &crtcs);
        if crtc_id == 0 {
            continue;
        }
        return Ok(Output { connector_id: cid, crtc_id, mode });
    }
    Err(io::Error::new(io::ErrorKind::NotFound, "no connected output with a mode"))
}

fn resolve_crtc(fd: i32, cur_encoder: u32, conn_encoders: &[u32], crtcs: &[u32]) -> u32 {

    if cur_encoder != 0 {
        let mut e = ModeGetEncoder { encoder_id: cur_encoder, ..Default::default() };
        if ioctl_val(fd, drm_iowr::<ModeGetEncoder>(NR_GETENCODER), mut_ptr_of(&mut e)).is_ok()
            && e.crtc_id != 0
        {
            return e.crtc_id;
        }
    }

    for &eid in conn_encoders {
        if eid == 0 {
            continue;
        }
        let mut e = ModeGetEncoder { encoder_id: eid, ..Default::default() };
        if ioctl_val(fd, drm_iowr::<ModeGetEncoder>(NR_GETENCODER), mut_ptr_of(&mut e)).is_err() {
            continue;
        }
        for (i, &crtc) in crtcs.iter().enumerate() {
            if crtc != 0 && (e.possible_crtcs & (1 << i)) != 0 {
                return crtc;
            }
        }
    }
    0
}

fn pick_mode(modes: &[ModeInfo]) -> ModeInfo {
    if let Ok(want) = std::env::var("PHANTOM_MODE") {
        if let Some((w, h)) = want.split_once('x') {
            if let (Ok(w), Ok(h)) = (w.trim().parse::<u16>(), h.trim().parse::<u16>()) {
                if let Some(m) = modes.iter().find(|m| m.hdisplay == w && m.vdisplay == h) {
                    eprintln!("compositor: PHANTOM_MODE={want} matched a connector mode");
                    return *m;
                }
                eprintln!("compositor: PHANTOM_MODE={want} not offered by the connector; using preferred mode");
            }
        }
    }
    modes[0]
}
