use super::consts::{KDSETMODE, KD_TEXT, KD_GRAPHICS};
use super::signal::SharedShell;
use phantom::sys;

pub(crate) fn render_shell_bg(shell: &SharedShell, bg: &mut [u8], w: u32, h: u32) {
    match shell.lock() {
        Ok(s) => s.render(bg, w, h),
        Err(_) => clear_bg(bg, w, h),
    }
}

pub(crate) fn clear_bg(bg: &mut [u8], w: u32, h: u32) {
    let n = (w as usize).saturating_mul(h as usize).saturating_mul(4);
    let n = n.min(bg.len());
    let mut i = 0;
    while i + 3 < n {
        bg[i] = 0x1c;
        bg[i + 1] = 0x14;
        bg[i + 2] = 0x12;
        bg[i + 3] = 0xff;
        i += 4;
    }
}

#[inline]
pub(crate) fn set_tty(tty_fd: i32, graphics: bool) {
    if tty_fd >= 0 {
        let _ = sys::ioctl_val(tty_fd, KDSETMODE, if graphics { KD_GRAPHICS } else { KD_TEXT });
    }
}

pub(crate) fn copy_scanout_region(
    map: &[u8],
    pitch: usize,
    sw: u32,
    sh: u32,
    x: u32,
    y: u32,
    w: u32,
    h: u32,
    dst: &mut [u8],
) {
    if x + w > sw {
        return;
    }
    let rb = (w as usize) * 4;
    for row in 0..h {
        let sy = y + row;
        if sy >= sh {
            break;
        }
        let so = sy as usize * pitch + (x as usize) * 4;
        let dofs = (row as usize) * rb;
        if so + rb <= map.len() && dofs + rb <= dst.len() {
            dst[dofs..dofs + rb].copy_from_slice(&map[so..so + rb]);
        }
    }
}

pub(crate) fn load_room_feed(path: &str) -> Vec<phantom::shell::RoomItem> {
    use phantom::shell::{RoomItem, RoomKind, RoomSpeaker};
    let data = std::fs::read_to_string(path).unwrap_or_default();
    let mut items = Vec::new();
    for line in data.lines() {
        let mut it = line.splitn(3, '\t');
        let sp = it.next().unwrap_or("");
        let kd = it.next().unwrap_or("");
        let tx = it.next().unwrap_or("");
        if kd.is_empty() {
            continue;
        }
        let speaker = match sp {
            "user" => RoomSpeaker::User,
            "agent1" => RoomSpeaker::Agent(1),
            "agent2" => RoomSpeaker::Agent(2),
            "sys" | "system" => RoomSpeaker::System,
            _ => RoomSpeaker::Agent(0),
        };
        let kind = match kd {
            "thinking" => RoomKind::Thinking,
            "tool" => RoomKind::Tool,
            "result" => RoomKind::Result,
            _ => RoomKind::Text,
        };
        let text = tx.replace("\\n", "\n").replace("\\t", "\t");
        items.push(RoomItem { speaker, kind, text });
    }
    items
}
