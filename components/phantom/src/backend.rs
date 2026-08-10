use std::io;

pub trait Backend {
    fn width(&self) -> u32;
    fn height(&self) -> u32;
    fn pitch(&self) -> u32;

    fn put(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8);
    fn clear(&mut self, r: u8, g: u8, b: u8);
    fn blit_bgra(&mut self, ox: u32, oy: u32, src: &[u8], src_off: usize, src_w: u32, src_h: u32, src_stride: u32);
    fn snapshot_rgba(&self) -> Vec<u8>;

    fn map_mut(&mut self) -> &mut [u8];

    fn dirty(&self) {}

    fn present(&mut self) {}

    fn card_fd(&self) -> Option<i32> {
        None
    }

    fn always_composite(&self) -> bool {
        false
    }

    fn frame_tap(&self) -> Option<crate::headless_fb::FrameTap> {
        None
    }

    fn describe(&self) -> String {
        format!("{}x{}", self.width(), self.height())
    }
}

pub struct DrmDisplay(pub crate::drm::Display);

impl Backend for DrmDisplay {
    fn width(&self) -> u32 {
        self.0.width
    }
    fn height(&self) -> u32 {
        self.0.height
    }
    fn pitch(&self) -> u32 {
        self.0.pitch
    }
    fn put(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8) {
        self.0.put(x, y, r, g, b)
    }
    fn clear(&mut self, r: u8, g: u8, b: u8) {
        self.0.clear(r, g, b)
    }
    fn blit_bgra(&mut self, ox: u32, oy: u32, src: &[u8], src_off: usize, src_w: u32, src_h: u32, src_stride: u32) {
        self.0.blit_bgra(ox, oy, src, src_off, src_w, src_h, src_stride)
    }
    fn snapshot_rgba(&self) -> Vec<u8> {
        self.0.snapshot_rgba()
    }
    fn map_mut(&mut self) -> &mut [u8] {
        self.0.map.as_mut_slice()
    }
    fn dirty(&self) {
        self.0.dirty()
    }

    fn card_fd(&self) -> Option<i32> {
        Some(self.0.card_fd())
    }
    fn describe(&self) -> String {
        format!("{}x{} '{}' (drm)", self.0.width, self.0.height, self.0.mode.name_str())
    }
}

impl Backend for crate::headless_fb::HeadlessFb {
    fn width(&self) -> u32 {
        self.width
    }
    fn height(&self) -> u32 {
        self.height
    }
    fn pitch(&self) -> u32 {
        self.pitch
    }
    fn put(&mut self, x: u32, y: u32, r: u8, g: u8, b: u8) {
        crate::headless_fb::HeadlessFb::put(self, x, y, r, g, b)
    }
    fn clear(&mut self, r: u8, g: u8, b: u8) {
        crate::headless_fb::HeadlessFb::clear(self, r, g, b)
    }
    fn blit_bgra(&mut self, ox: u32, oy: u32, src: &[u8], src_off: usize, src_w: u32, src_h: u32, src_stride: u32) {
        crate::headless_fb::HeadlessFb::blit_bgra(self, ox, oy, src, src_off, src_w, src_h, src_stride)
    }
    fn snapshot_rgba(&self) -> Vec<u8> {
        crate::headless_fb::HeadlessFb::snapshot_rgba(self)
    }
    fn map_mut(&mut self) -> &mut [u8] {
        crate::headless_fb::HeadlessFb::map_mut(self)
    }

    fn present(&mut self) {
        crate::headless_fb::HeadlessFb::present(self)
    }

    fn always_composite(&self) -> bool {
        true
    }
    fn frame_tap(&self) -> Option<crate::headless_fb::FrameTap> {
        Some(crate::headless_fb::HeadlessFb::frame_tap(self))
    }
    fn describe(&self) -> String {
        format!("{}x{} (headless-composite)", self.width, self.height)
    }
}

fn want_headless_env() -> Option<bool> {
    match std::env::var("PHANTOM_HEADLESS").ok().as_deref() {
        Some("1") | Some("true") => Some(true),
        Some("0") | Some("false") => Some(false),
        _ => None,
    }
}

fn no_drm_card_in(dri_dir: &std::path::Path) -> bool {
    if !dri_dir.exists() {
        return true;
    }
    match std::fs::read_dir(dri_dir) {
        Ok(rd) => !rd
            .filter_map(|e| e.ok())
            .any(|e| e.file_name().to_string_lossy().starts_with("card")),
        Err(_) => true,
    }
}

pub fn decide_headless(want_headless: Option<bool>, no_drm_card: bool) -> bool {
    want_headless.unwrap_or(no_drm_card)
}

fn dri_dir() -> std::path::PathBuf {
    std::env::var_os("PHANTOM_DRM_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("/dev/dri"))
}

pub fn select_backend() -> io::Result<Box<dyn Backend>> {
    let card = std::env::var("PHANTOM_DRM_CARD").unwrap_or_else(|_| "/dev/dri/card1".into());
    let want_headless = want_headless_env();
    let no_card = no_drm_card_in(&dri_dir());

    if decide_headless(want_headless, no_card) {
        let fb = crate::headless_fb::HeadlessFb::open_from_env()?;
        let why = if want_headless == Some(true) { "forced" } else { "auto: no DRM card" };
        eprintln!(
            "compositor: headless-composite backend {}x{} ({why} — apps composited off-screen, no scanout)",
            fb.width, fb.height
        );
        return Ok(Box::new(fb));
    }
    match crate::drm::Display::open(&card) {
        Ok(d) => Ok(Box::new(DrmDisplay(d))),
        Err(e) if want_headless.is_none() => {
            eprintln!("compositor: DRM open failed ({e}) — falling back to headless-composite");
            Ok(Box::new(crate::headless_fb::HeadlessFb::open_from_env()?))
        }
        Err(e) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_picks_headless_when_no_card() {

        assert!(decide_headless(None, true), "auto with no DRM card must go headless");
    }

    #[test]
    fn auto_picks_drm_when_card_present() {

        assert!(!decide_headless(None, false), "auto with a DRM card must try DRM");
    }

    #[test]
    fn force_overrides_card_presence() {

        assert!(decide_headless(Some(true), false), "force-headless wins over a present card");
        assert!(!decide_headless(Some(false), true), "force-DRM wins over a missing card");
    }

    #[test]
    fn empty_dir_reads_as_no_card() {

        let base = std::env::temp_dir().join(format!("phantom-dritest-{}", std::process::id()));
        let empty = base.join("empty");
        let carded = base.join("carded");
        let renderonly = base.join("renderonly");
        std::fs::create_dir_all(&empty).unwrap();
        std::fs::create_dir_all(&carded).unwrap();
        std::fs::create_dir_all(&renderonly).unwrap();
        std::fs::write(carded.join("card0"), b"").unwrap();
        std::fs::write(renderonly.join("renderD128"), b"").unwrap();

        assert!(no_drm_card_in(&empty), "empty dir ⇒ no card");
        assert!(!no_drm_card_in(&carded), "dir with card0 ⇒ has card");
        assert!(no_drm_card_in(&renderonly), "render node only ⇒ no card");
        assert!(no_drm_card_in(&base.join("does-not-exist")), "missing dir ⇒ no card");

        assert!(
            decide_headless(want_headless_env_for(None), no_drm_card_in(&empty)),
            "auto + empty DRM dir ⇒ headless-composite"
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    fn want_headless_env_for(v: Option<bool>) -> Option<bool> {
        v
    }

    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn select_backend_auto_falls_back_to_headless_when_dir_empty() {
        let _g = ENV_LOCK.lock().unwrap();
        let empty = std::env::temp_dir().join(format!("phantom-empty-dri-{}", std::process::id()));
        std::fs::create_dir_all(&empty).unwrap();

        std::env::remove_var("PHANTOM_HEADLESS");
        std::env::set_var("PHANTOM_DRM_DIR", &empty);
        std::env::set_var("PHANTOM_HEADLESS_SIZE", "320x200");

        let b = select_backend().expect("select_backend should succeed (headless can't fail)");
        assert!(
            b.frame_tap().is_some(),
            "auto + empty /dev/dri ⇒ headless-composite backend (has a frame tap)"
        );
        assert_eq!((b.width(), b.height()), (320, 200), "honoured PHANTOM_HEADLESS_SIZE");

        std::env::remove_var("PHANTOM_DRM_DIR");
        std::env::remove_var("PHANTOM_HEADLESS_SIZE");
        let _ = std::fs::remove_dir_all(&empty);
    }
}
