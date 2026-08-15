use crate::sys::ioctl_val;
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::fs::OpenOptionsExt;

const EV_KEY: u16 = 1;
const EV_REL: u16 = 2;
const EV_ABS: u16 = 3;
const REL_X: u16 = 0;
const REL_Y: u16 = 1;
const REL_WHEEL: u16 = 8;
const ABS_X: u16 = 0;
const ABS_Y: u16 = 1;
const BTN_MIN: u16 = 0x110;
const BTN_MAX: u16 = 0x117;
const EVIOCGRAB: u64 = 0x4004_4590;
const EVENT_SIZE: usize = 24;

fn abs_max(fd: RawFd, axis: u16) -> Option<i32> {

    let mut info = [0u8; 24];
    let req = 0x8000_0000u64 | (24u64 << 16) | (0x45u64 << 8) | (0x40 + axis as u64);
    if ioctl_val(fd, req, info.as_mut_ptr() as u64).is_ok() {
        let max = i32::from_ne_bytes([info[8], info[9], info[10], info[11]]);
        if max > 0 {
            return Some(max);
        }
    }
    None
}

#[derive(Debug, Clone, Copy)]
pub enum InputEv {

    Motion { dx: i32, dy: i32 },

    AbsMotion { x: i32, y: i32 },
    Wheel(i32),

    Button { code: u16, down: bool },

    Key { code: u16, down: bool },
}

pub struct InputHub {

    devs: Vec<(String, File, Option<(i32, i32)>)>,
    grabbed: bool,
}

impl InputHub {
    pub fn open_all(grab: bool) -> std::io::Result<InputHub> {
        let mut devs = Vec::new();
        let dir = std::fs::read_dir("/dev/input")?;
        for e in dir.flatten() {
            let p = e.path();
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
            if !name.starts_with("event") {
                continue;
            }
            if let Ok(f) = OpenOptions::new().read(true).custom_flags(0o4000  ).open(&p) {
                let fd = f.as_raw_fd();
                if grab {

                    let _ = ioctl_val(fd, EVIOCGRAB, 1);
                }

                let abs = match (abs_max(fd, ABS_X), abs_max(fd, ABS_Y)) {
                    (Some(mx), Some(my)) => Some((mx, my)),
                    _ => None,
                };
                devs.push((name, f, abs));
            }
        }
        Ok(InputHub { devs, grabbed: grab })
    }

    pub fn fds(&self) -> Vec<RawFd> {
        self.devs.iter().map(|(_, f, _)| f.as_raw_fd()).collect()
    }
    pub fn device_count(&self) -> usize {
        self.devs.len()
    }
    pub fn name(&self, idx: usize) -> &str {
        self.devs.get(idx).map(|(n, _, _)| n.as_str()).unwrap_or("")
    }

    pub fn read(&mut self, idx: usize) -> Vec<InputEv> {
        let mut out = Vec::new();
        let Some((_, f, absrange)) = self.devs.get_mut(idx) else {
            return out;
        };
        let absrange = *absrange;
        let mut buf = [0u8; EVENT_SIZE * 64];
        let n = match f.read(&mut buf) {
            Ok(n) => n,
            Err(_) => return out,
        };
        let mut off = 0;
        while off + EVENT_SIZE <= n {
            let etype = u16::from_ne_bytes([buf[off + 16], buf[off + 17]]);
            let code = u16::from_ne_bytes([buf[off + 18], buf[off + 19]]);
            let value = i32::from_ne_bytes([buf[off + 20], buf[off + 21], buf[off + 22], buf[off + 23]]);
            match etype {
                EV_REL => match code {
                    REL_X => out.push(InputEv::Motion { dx: value, dy: 0 }),
                    REL_Y => out.push(InputEv::Motion { dx: 0, dy: value }),
                    REL_WHEEL => out.push(InputEv::Wheel(value)),
                    _ => {}
                },

                EV_ABS => {
                    if let Some((mx, my)) = absrange {
                        match code {
                            ABS_X if mx > 0 => {
                                let nx = (value.clamp(0, mx) as i64 * 65535 / mx as i64) as i32;
                                out.push(InputEv::AbsMotion { x: nx, y: -1 });
                            }
                            ABS_Y if my > 0 => {
                                let ny = (value.clamp(0, my) as i64 * 65535 / my as i64) as i32;
                                out.push(InputEv::AbsMotion { x: -1, y: ny });
                            }
                            _ => {}
                        }
                    }
                }
                EV_KEY => {
                    let down = value != 0;
                    if (BTN_MIN..=BTN_MAX).contains(&code) {
                        out.push(InputEv::Button { code, down });
                    } else {
                        out.push(InputEv::Key { code, down });
                    }
                }
                _ => {}
            }
            off += EVENT_SIZE;
        }
        out
    }
}

impl Drop for InputHub {
    fn drop(&mut self) {
        if self.grabbed {
            for (_, f, _) in &self.devs {
                let _ = ioctl_val(f.as_raw_fd(), EVIOCGRAB, 0);
            }
        }
    }
}
