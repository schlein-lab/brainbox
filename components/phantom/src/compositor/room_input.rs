use super::state::FOCUS_DIRTY;

static ROOM_INPUT: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());
static ROOM_SUBMIT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static ROOM_DIRTY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub(crate) fn room_mark_dirty() {
    ROOM_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
    FOCUS_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub fn room_input_push(s: &str) {
    if let Ok(mut b) = ROOM_INPUT.lock() {

        if b.len() + s.len() <= 4096 {
            b.push_str(s);
        }
    }
    room_mark_dirty();
}

pub fn room_input_backspace() {
    if let Ok(mut b) = ROOM_INPUT.lock() {
        b.pop();
    }
    room_mark_dirty();
}

pub fn room_input_clear() {
    if let Ok(mut b) = ROOM_INPUT.lock() {
        b.clear();
    }
    room_mark_dirty();
}

pub fn room_input_submit() {
    ROOM_SUBMIT.store(true, std::sync::atomic::Ordering::Relaxed);
    room_mark_dirty();
}

pub fn room_input_snapshot() -> String {
    ROOM_INPUT.lock().map(|b| b.clone()).unwrap_or_default()
}

pub(crate) fn room_input_take_submit() -> Option<String> {
    if ROOM_SUBMIT.swap(false, std::sync::atomic::Ordering::Relaxed) {
        if let Ok(mut b) = ROOM_INPUT.lock() {
            let line = b.trim().to_string();
            b.clear();
            if !line.is_empty() {
                return Some(line);
            }
        }
    }
    None
}

pub(crate) fn take_room_dirty() -> bool {
    ROOM_DIRTY.swap(false, std::sync::atomic::Ordering::Relaxed)
}
