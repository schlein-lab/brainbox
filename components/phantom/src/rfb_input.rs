use std::sync::Mutex;
use crate::Shared;
use phantom::input::{active_layout, char_to_key_layout, Modifier};

const M_SHIFT: u32 = 1;
const M_CTRL: u32 = 4;
const M_ALT: u32 = 8;
const M_ALTGR: u32 = 128;

const BTN_LEFT: u8 = 1;
const BTN_MIDDLE: u8 = 1 << 1;
const BTN_RIGHT: u8 = 1 << 2;
const BTN_WHEEL_UP: u8 = 1 << 3;
const BTN_WHEEL_DOWN: u8 = 1 << 4;

struct InState {

    mods: u32,

    prev_buttons: u8,

    last_xy: (i64, i64),
}

pub struct BoxInputSink {
    shared: Shared,
    st: Mutex<InState>,
}

impl BoxInputSink {
    pub fn new(shared: Shared) -> BoxInputSink {
        BoxInputSink {
            shared,
            st: Mutex::new(InState { mods: 0, prev_buttons: 0, last_xy: (-1, -1) }),
        }
    }

    fn si(&self, action: &str, args: &[&str]) {

        let _ = crate::compositor::screen_input(&self.shared, action, args);
    }

    fn inject_keycode(&self, code: u16, mask: u32) {
        let cs = code.to_string();
        let ms = mask.to_string();
        self.si("keycode", &[&cs, &ms]);
    }
}

impl phantom::rfbd::InputSink for BoxInputSink {
    fn key(&self, keysym: u32, down: bool) {

        if let Some(bit) = modifier_bit(keysym) {
            if bit != 0 {
                let mut s = self.st.lock().unwrap();
                if down {
                    s.mods |= bit;
                } else {
                    s.mods &= !bit;
                }
            }
            return;
        }

        if !down {
            return;
        }

        if let Some(code) = named_key(keysym) {
            let mask = self.st.lock().unwrap().mods;
            self.inject_keycode(code, mask);
            return;
        }

        if let Some(ch) = keysym_to_char(keysym) {
            if let Some((code, modi)) = char_to_key_layout(ch, active_layout()) {

                let mask = if matches!(modi, Modifier::AltGr) {
                    modifier_to_bit(modi)
                } else {
                    (self.st.lock().unwrap().mods & (M_CTRL | M_ALT)) | modifier_to_bit(modi)
                };
                self.inject_keycode(code, mask);
            }
        }
    }

    fn pointer(&self, x: u16, y: u16, button_mask: u8) {
        let (xi, yi) = (x as i64, y as i64);
        let xs = xi.to_string();
        let ys = yi.to_string();
        let (prev, moved) = {
            let mut s = self.st.lock().unwrap();
            let prev = s.prev_buttons;
            let moved = s.last_xy != (xi, yi);
            s.prev_buttons = button_mask;
            s.last_xy = (xi, yi);
            (prev, moved)
        };
        let pressed = |bit: u8| (button_mask & bit) != 0 && (prev & bit) == 0;

        if moved {
            self.si("move", &[&xs, &ys]);
        }
        if pressed(BTN_LEFT) {
            self.si("click", &[&xs, &ys]);
        }
        if pressed(BTN_RIGHT) {
            self.si("click", &[&xs, &ys, "3"]);
        }
        if pressed(BTN_MIDDLE) {
            self.si("click", &[&xs, &ys, "2"]);
        }

        if pressed(BTN_WHEEL_UP) {
            self.si("scroll", &[&xs, &ys, "-1"]);
        }
        if pressed(BTN_WHEEL_DOWN) {
            self.si("scroll", &[&xs, &ys, "1"]);
        }
    }
}

fn modifier_bit(keysym: u32) -> Option<u32> {
    match keysym {
        0xffe1 | 0xffe2 => Some(M_SHIFT),
        0xffe3 | 0xffe4 => Some(M_CTRL),
        0xffe9 => Some(M_ALT),
        0xffea | 0xfe03 => Some(M_ALTGR),
        0xffe5 | 0xffe6 => Some(0),
        0xffe7 | 0xffe8 => Some(0),
        0xffeb | 0xffec => Some(0),
        _ => None,
    }
}

fn modifier_to_bit(m: Modifier) -> u32 {
    match m {
        Modifier::None => 0,
        Modifier::Shift => M_SHIFT,
        Modifier::AltGr => M_ALTGR,
        Modifier::Ctrl => M_CTRL,
    }
}

fn named_key(keysym: u32) -> Option<u16> {
    let v = match keysym {
        0xff0d => 28,
        0xff8d => 28,
        0xff08 => 14,
        0xff09 => 15,
        0xff1b => 1,
        0xffff => 111,
        0xff50 => 102,
        0xff57 => 107,
        0xff51 => 105,
        0xff52 => 103,
        0xff53 => 106,
        0xff54 => 108,
        0xff55 => 104,
        0xff56 => 109,
        0xff63 => 110,
        0xffc8 => 87,
        0xffc9 => 88,
        0xffbe..=0xffc7 => 59 + (keysym - 0xffbe) as u16,
        _ => return None,
    };
    Some(v)
}

fn keysym_to_char(keysym: u32) -> Option<char> {
    let cp = if (0x20..=0x7e).contains(&keysym) || (0xa0..=0xff).contains(&keysym) {
        keysym
    } else if keysym & 0xff00_0000 == 0x0100_0000 {
        keysym & 0x00ff_ffff
    } else {
        return None;
    };
    char::from_u32(cp)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn named_keys_map() {
        assert_eq!(named_key(0xff0d), Some(28));
        assert_eq!(named_key(0xff08), Some(14));
        assert_eq!(named_key(0xffc9), Some(88));
        assert_eq!(named_key(0xffbe), Some(59));
        assert_eq!(named_key(0x61), None);
    }

    #[test]
    fn keysym_char_decoding() {
        assert_eq!(keysym_to_char(0x40), Some('@'));
        assert_eq!(keysym_to_char(0x61), Some('a'));
        assert_eq!(keysym_to_char(0xe4), Some('ä'));
        assert_eq!(keysym_to_char(0xdf), Some('ß'));
        assert_eq!(keysym_to_char(0x0100_20ac), Some('€'));
        assert_eq!(keysym_to_char(0xff0d), None);
    }

    #[test]
    fn modifiers() {
        assert_eq!(modifier_bit(0xffe1), Some(M_SHIFT));
        assert_eq!(modifier_bit(0xffea), Some(M_ALTGR));
        assert_eq!(modifier_bit(0x63), None);
        assert_eq!(modifier_to_bit(Modifier::AltGr), M_ALTGR);
        assert_eq!(modifier_to_bit(Modifier::Shift), M_SHIFT);
        assert_eq!(modifier_to_bit(Modifier::None), 0);
    }
}
