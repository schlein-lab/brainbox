use crate::sys;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::os::fd::AsRawFd;
use std::sync::OnceLock;

const EV_SYN: u16 = 0x00;
const EV_KEY: u16 = 0x01;
const EV_REL: u16 = 0x02;
const SYN_REPORT: u16 = 0;
const REL_X: u16 = 0x00;
const REL_Y: u16 = 0x01;
const REL_WHEEL: u16 = 0x08;

pub const BTN_LEFT: u16 = 0x110;
pub const BTN_RIGHT: u16 = 0x111;
pub const BTN_MIDDLE: u16 = 0x112;

const KEY_ENTER: u16 = 28;
const KEY_LEFTSHIFT: u16 = 42;
const KEY_RIGHTALT: u16 = 100;

const UI_SET_EVBIT: u64 = 0x4004_5564;
const UI_SET_KEYBIT: u64 = 0x4004_5565;
const UI_SET_RELBIT: u64 = 0x4004_5566;
const UI_DEV_SETUP: u64 = 0x405C_5503;
const UI_DEV_CREATE: u64 = 0x5501;
const UI_DEV_DESTROY: u64 = 0x5502;

#[repr(C)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

#[repr(C)]
struct UinputSetup {
    id: InputId,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[repr(C)]
struct InputEvent {
    sec: i64,
    usec: i64,
    etype: u16,
    code: u16,
    value: i32,
}

pub struct VirtualInput {
    dev: File,
}

impl VirtualInput {

    pub fn new() -> io::Result<Self> {
        let dev = OpenOptions::new().read(true).write(true).open("/dev/uinput")?;
        let fd = dev.as_raw_fd();

        sys::ioctl_val(fd, UI_SET_EVBIT, EV_KEY as u64)?;
        sys::ioctl_val(fd, UI_SET_EVBIT, EV_SYN as u64)?;
        sys::ioctl_val(fd, UI_SET_EVBIT, EV_REL as u64)?;

        for k in 1u64..=255 {
            sys::ioctl_val(fd, UI_SET_KEYBIT, k)?;
        }

        for b in [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE] {
            sys::ioctl_val(fd, UI_SET_KEYBIT, b as u64)?;
        }
        for r in [REL_X, REL_Y, REL_WHEEL] {
            sys::ioctl_val(fd, UI_SET_RELBIT, r as u64)?;
        }

        let mut name = [0u8; 80];
        let label = b"phantom-virtual-input";
        name[..label.len()].copy_from_slice(label);
        let setup = UinputSetup {
            id: InputId { bustype: 0x03, vendor: 0x1209, product: 0x5050, version: 1 },
            name,
            ff_effects_max: 0,
        };
        sys::ioctl_val(fd, UI_DEV_SETUP, &setup as *const _ as u64)?;
        sys::ioctl_val(fd, UI_DEV_CREATE, 0)?;
        Ok(Self { dev })
    }

    fn emit(&mut self, etype: u16, code: u16, value: i32) -> io::Result<()> {
        let ev = InputEvent { sec: 0, usec: 0, etype, code, value };
        let bytes = unsafe {
            std::slice::from_raw_parts(&ev as *const _ as *const u8, std::mem::size_of::<InputEvent>())
        };
        self.dev.write_all(bytes)
    }

    #[inline]
    fn syn(&mut self) -> io::Result<()> {
        self.emit(EV_SYN, SYN_REPORT, 0)
    }

    pub fn key(&mut self, code: u16) -> io::Result<()> {
        self.emit(EV_KEY, code, 1)?;
        self.syn()?;
        self.emit(EV_KEY, code, 0)?;
        self.syn()
    }

    pub fn enter(&mut self) -> io::Result<()> {
        self.key(KEY_ENTER)
    }

    pub fn type_str(&mut self, s: &str) -> io::Result<()> {
        let layout = active_layout();
        for ch in s.chars() {
            let Some((code, modi)) = char_to_key_layout(ch, layout) else { continue };
            if modi == Modifier::Shift {
                self.emit(EV_KEY, KEY_LEFTSHIFT, 1)?;
                self.syn()?;
            } else if modi == Modifier::AltGr {
                self.emit(EV_KEY, KEY_RIGHTALT, 1)?;
                self.syn()?;
            }
            self.emit(EV_KEY, code, 1)?;
            self.syn()?;
            self.emit(EV_KEY, code, 0)?;
            self.syn()?;
            if modi == Modifier::Shift {
                self.emit(EV_KEY, KEY_LEFTSHIFT, 0)?;
                self.syn()?;
            } else if modi == Modifier::AltGr {
                self.emit(EV_KEY, KEY_RIGHTALT, 0)?;
                self.syn()?;
            }
        }
        Ok(())
    }

    pub fn move_rel(&mut self, dx: i32, dy: i32) -> io::Result<()> {
        self.emit(EV_REL, REL_X, dx)?;
        self.emit(EV_REL, REL_Y, dy)?;
        self.syn()
    }

    pub fn scroll(&mut self, amount: i32) -> io::Result<()> {
        self.emit(EV_REL, REL_WHEEL, amount)?;
        self.syn()
    }

    pub fn click(&mut self, button: u16) -> io::Result<()> {
        self.emit(EV_KEY, button, 1)?;
        self.syn()?;
        self.emit(EV_KEY, button, 0)?;
        self.syn()
    }
}

impl Drop for VirtualInput {
    fn drop(&mut self) {
        let _ = sys::ioctl_val(self.dev.as_raw_fd(), UI_DEV_DESTROY, 0);
    }
}

const AZ: [u16; 26] = [
    30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45,
    21, 44,
];

pub fn char_from_key(code: u16, shift: bool) -> Option<char> {
    let (lo, hi): (char, char) = match code {
        2 => ('1', '!'), 3 => ('2', '@'), 4 => ('3', '#'), 5 => ('4', '$'),
        6 => ('5', '%'), 7 => ('6', '^'), 8 => ('7', '&'), 9 => ('8', '*'),
        10 => ('9', '('), 11 => ('0', ')'), 12 => ('-', '_'), 13 => ('=', '+'),
        16 => ('q', 'Q'), 17 => ('w', 'W'), 18 => ('e', 'E'), 19 => ('r', 'R'),
        20 => ('t', 'T'), 21 => ('y', 'Y'), 22 => ('u', 'U'), 23 => ('i', 'I'),
        24 => ('o', 'O'), 25 => ('p', 'P'), 26 => ('[', '{'), 27 => (']', '}'),
        30 => ('a', 'A'), 31 => ('s', 'S'), 32 => ('d', 'D'), 33 => ('f', 'F'),
        34 => ('g', 'G'), 35 => ('h', 'H'), 36 => ('j', 'J'), 37 => ('k', 'K'),
        38 => ('l', 'L'), 39 => (';', ':'), 40 => ('\'', '"'), 41 => ('`', '~'),
        43 => ('\\', '|'), 44 => ('z', 'Z'), 45 => ('x', 'X'), 46 => ('c', 'C'),
        47 => ('v', 'V'), 48 => ('b', 'B'), 49 => ('n', 'N'), 50 => ('m', 'M'),
        51 => (',', '<'), 52 => ('.', '>'), 53 => ('/', '?'), 57 => (' ', ' '),
        _ => return None,
    };
    Some(if shift { hi } else { lo })
}

pub fn char_from_key_de(code: u16, shift: bool, altgr: bool) -> Option<char> {
    if altgr {
        return Some(match code {
            16 => '@', 18 => '€', 8 => '{', 9 => '[', 10 => ']', 11 => '}',
            12 => '\\', 86 => '|', 27 => '~',
            _ => return None,
        });
    }
    let (lo, hi): (char, char) = match code {
        2 => ('1', '!'), 3 => ('2', '"'), 4 => ('3', '§'), 5 => ('4', '$'),
        6 => ('5', '%'), 7 => ('6', '&'), 8 => ('7', '/'), 9 => ('8', '('),
        10 => ('9', ')'), 11 => ('0', '='), 12 => ('ß', '?'),
        16 => ('q', 'Q'), 17 => ('w', 'W'), 18 => ('e', 'E'), 19 => ('r', 'R'), 20 => ('t', 'T'),
        21 => ('z', 'Z'), 22 => ('u', 'U'), 23 => ('i', 'I'), 24 => ('o', 'O'), 25 => ('p', 'P'),
        26 => ('ü', 'Ü'), 27 => ('+', '*'),
        30 => ('a', 'A'), 31 => ('s', 'S'), 32 => ('d', 'D'), 33 => ('f', 'F'), 34 => ('g', 'G'),
        35 => ('h', 'H'), 36 => ('j', 'J'), 37 => ('k', 'K'), 38 => ('l', 'L'), 39 => ('ö', 'Ö'), 40 => ('ä', 'Ä'),
        43 => ('#', '\''),
        44 => ('y', 'Y'), 45 => ('x', 'X'), 46 => ('c', 'C'), 47 => ('v', 'V'), 48 => ('b', 'B'), 49 => ('n', 'N'), 50 => ('m', 'M'),
        51 => (',', ';'), 52 => ('.', ':'), 53 => ('-', '_'),
        57 => (' ', ' '), 86 => ('<', '>'),
        _ => return None,
    };
    Some(if shift { hi } else { lo })
}

pub fn char_from_key_layout(code: u16, shift: bool, altgr: bool, layout: Layout) -> Option<char> {
    match layout {
        Layout::De => char_from_key_de(code, shift, altgr),
        Layout::Us => char_from_key(code, shift),
    }
}

pub fn char_to_key(ch: char) -> Option<(u16, bool)> {
    Some(match ch {
        'a'..='z' => (AZ[ch as usize - 'a' as usize], false),
        'A'..='Z' => (AZ[ch as usize - 'A' as usize], true),
        '1'..='9' => (2 + (ch as u16 - '1' as u16), false),
        '0' => (11, false),
        ' ' => (57, false),
        '\n' => (28, false),
        '\t' => (15, false),
        '!' => (2, true),
        '@' => (3, true),
        '#' => (4, true),
        '$' => (5, true),
        '%' => (6, true),
        '^' => (7, true),
        '&' => (8, true),
        '*' => (9, true),
        '(' => (10, true),
        ')' => (11, true),
        '-' => (12, false),
        '_' => (12, true),
        '=' => (13, false),
        '+' => (13, true),
        '[' => (26, false),
        '{' => (26, true),
        ']' => (27, false),
        '}' => (27, true),
        '\\' => (43, false),
        '|' => (43, true),
        ';' => (39, false),
        ':' => (39, true),
        '\'' => (40, false),
        '"' => (40, true),
        ',' => (51, false),
        '<' => (51, true),
        '.' => (52, false),
        '>' => (52, true),
        '/' => (53, false),
        '?' => (53, true),
        '`' => (41, false),
        '~' => (41, true),
        _ => return None,
    })
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Modifier {
    None,
    Shift,
    AltGr,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Layout {
    Us,
    De,
}

pub fn active_layout() -> Layout {
    static LAYOUT: OnceLock<Layout> = OnceLock::new();
    *LAYOUT.get_or_init(detect_layout)
}

fn layout_from_value(v: &str) -> Layout {

    let first = v.trim().trim_matches('"').split(',').next().unwrap_or("").trim();
    if first.to_ascii_lowercase().starts_with("de") {
        Layout::De
    } else {
        Layout::Us
    }
}

fn detect_layout() -> Layout {
    if let Ok(v) = std::env::var("PHANTOM_KBD_LAYOUT") {
        if !v.trim().is_empty() {
            return layout_from_value(&v);
        }
    }
    if let Ok(contents) = std::fs::read_to_string("/etc/default/keyboard") {
        for line in contents.lines() {
            let line = line.trim();
            if line.starts_with('#') {
                continue;
            }
            if let Some(rest) = line.strip_prefix("XKBLAYOUT=") {
                return layout_from_value(rest);
            }
        }
    }
    Layout::Us
}

pub fn char_to_key_layout(ch: char, layout: Layout) -> Option<(u16, Modifier)> {
    match layout {
        Layout::De => char_to_key_de(ch),
        Layout::Us => char_to_key(ch).map(|(code, shift)| {
            (code, if shift { Modifier::Shift } else { Modifier::None })
        }),
    }
}

pub fn char_to_key_de(ch: char) -> Option<(u16, Modifier)> {
    use Modifier::{AltGr, None as N, Shift};
    Some(match ch {

        'z' => (21, N),
        'Z' => (21, Shift),
        'y' => (44, N),
        'Y' => (44, Shift),
        'a'..='z' => (AZ[ch as usize - 'a' as usize], N),
        'A'..='Z' => (AZ[ch as usize - 'A' as usize], Shift),

        'ü' => (26, N),
        'Ü' => (26, Shift),
        'ö' => (39, N),
        'Ö' => (39, Shift),
        'ä' => (40, N),
        'Ä' => (40, Shift),
        'ß' => (12, N),

        '1'..='9' => (2 + (ch as u16 - '1' as u16), N),
        '0' => (11, N),

        '!' => (2, Shift),
        '"' => (3, Shift),
        '§' => (4, Shift),
        '$' => (5, Shift),
        '%' => (6, Shift),
        '&' => (7, Shift),
        '/' => (8, Shift),
        '(' => (9, Shift),
        ')' => (10, Shift),
        '=' => (11, Shift),
        '?' => (12, Shift),

        '+' => (27, N),
        '*' => (27, Shift),
        '#' => (43, N),
        '\'' => (43, Shift),
        ',' => (51, N),
        ';' => (51, Shift),
        '.' => (52, N),
        ':' => (52, Shift),
        '-' => (53, N),
        '_' => (53, Shift),
        '<' => (86, N),
        '>' => (86, Shift),
        ' ' => (57, N),
        '\n' => (28, N),
        '\t' => (15, N),

        '@' => (16, AltGr),
        '€' => (18, AltGr),
        '{' => (8, AltGr),
        '[' => (9, AltGr),
        ']' => (10, AltGr),
        '}' => (11, AltGr),
        '\\' => (12, AltGr), // AltGr + ß
        '|' => (86, AltGr),
        '~' => (27, AltGr),
        _ => return None,
    })
}
