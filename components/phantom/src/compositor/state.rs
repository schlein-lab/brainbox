use super::consts::FOCUS_AUTO;

pub(crate) static FOCUSED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(FOCUS_AUTO);

pub(crate) static FOCUS_DIRTY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn set_focused(cid: u64) {
    FOCUSED.store(cid, std::sync::atomic::Ordering::Relaxed);
    FOCUS_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub fn set_focused_auto() {
    FOCUSED.store(FOCUS_AUTO, std::sync::atomic::Ordering::Relaxed);
    FOCUS_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub(crate) fn take_focus_dirty() -> bool {
    FOCUS_DIRTY.swap(false, std::sync::atomic::Ordering::Relaxed)
}

pub(crate) static DOCK_MODE: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

pub fn set_dock_mode(mode: u8) {
    DOCK_MODE.store(mode, std::sync::atomic::Ordering::Relaxed);
    FOCUS_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub(crate) fn dock_mode() -> u8 {
    DOCK_MODE.load(std::sync::atomic::Ordering::Relaxed)
}

pub(crate) static SHOW_CLIENTS: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn set_show_clients(on: bool) {
    SHOW_CLIENTS.store(on, std::sync::atomic::Ordering::Relaxed);
    FOCUS_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub fn show_clients() -> bool {
    SHOW_CLIENTS.load(std::sync::atomic::Ordering::Relaxed)
}

pub fn focused_get() -> Option<u64> {
    match FOCUSED.load(std::sync::atomic::Ordering::Relaxed) {
        FOCUS_AUTO => None,
        v => Some(v),
    }
}

pub(crate) fn focused_override() -> Option<u64> {
    focused_get()
}

pub(crate) static SEAT_W: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
pub(crate) static SEAT_H: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

pub fn set_seat_size(w: u32, h: u32) {
    SEAT_W.store(w, std::sync::atomic::Ordering::Relaxed);
    SEAT_H.store(h, std::sync::atomic::Ordering::Relaxed);
}

pub fn seat_size() -> (u32, u32) {
    (
        SEAT_W.load(std::sync::atomic::Ordering::Relaxed),
        SEAT_H.load(std::sync::atomic::Ordering::Relaxed),
    )
}

pub(crate) static SEAT_SOCK: std::sync::OnceLock<String> = std::sync::OnceLock::new();

pub fn set_seat_socket(path: &str) {
    let _ = SEAT_SOCK.set(path.to_string());
}

pub fn seat_socket() -> String {
    SEAT_SOCK.get().cloned().unwrap_or_else(|| "/run/phantom/phantom-0".to_string())
}

pub(crate) static SEAT_WAKE: std::sync::OnceLock<super::signal::SharedDamage> = std::sync::OnceLock::new();

pub fn set_seat_wake(wake: super::signal::SharedDamage) {
    let _ = SEAT_WAKE.set(wake);
}

pub fn wake_present() {
    if let Some(w) = SEAT_WAKE.get() {
        w.mark();
    }
}
