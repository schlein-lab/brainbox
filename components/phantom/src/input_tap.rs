use crate::input::char_from_key;
use crate::sys::ioctl_val;

use std::fs::File;
use std::io::Read;
use std::os::fd::AsRawFd;
use std::thread::{self, JoinHandle};

const EV_KEY: u16 = 1;
const EVIOCGRAB: u64 = 0x4004_4590;

pub struct KeyEvent<'a> {

    pub device: &'a str,

    pub key: Key,
}

pub enum Key {
    Char(char),
    Backspace,
    Enter,
}

pub fn spawn_all<F>(grab: bool, sink: F) -> Vec<JoinHandle<()>>
where
    F: Fn(&KeyEvent) + Send + Clone + 'static,
{
    let mut handles = Vec::new();
    let Ok(dir) = std::fs::read_dir("/dev/input") else {
        return handles;
    };
    for entry in dir.flatten() {
        let path = entry.path();
        let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
        if !name.starts_with("event") {
            continue;
        }
        if let Ok(f) = File::open(&path) {
            let sink = sink.clone();
            handles.push(thread::spawn(move || tap_device(f, &name, grab, sink)));
        }
    }
    handles
}

fn tap_device(mut f: File, name: &str, grab: bool, sink: impl Fn(&KeyEvent)) {
    if grab {

        let _ = ioctl_val(f.as_raw_fd(), EVIOCGRAB, 1);
    }
    let mut shift = false;
    let mut buf = [0u8; 24];
    while f.read_exact(&mut buf).is_ok() {
        let etype = u16::from_ne_bytes([buf[16], buf[17]]);
        let code = u16::from_ne_bytes([buf[18], buf[19]]);
        let value = i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]);
        if etype != EV_KEY {
            continue;
        }
        match value {
            1 => match code {

                42 | 54 => shift = true,
                14 => sink(&KeyEvent { device: name, key: Key::Backspace }),
                28 => sink(&KeyEvent { device: name, key: Key::Enter }),
                _ => {
                    if let Some(c) = char_from_key(code, shift) {
                        sink(&KeyEvent { device: name, key: Key::Char(c) });
                    }
                }
            },
            0 if code == 42 || code == 54 => shift = false,
            _ => {}
        }
    }
}
