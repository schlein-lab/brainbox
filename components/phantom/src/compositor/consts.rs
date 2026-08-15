pub(crate) const KEY_F9: u16 = 67;

pub(crate) const WHEEL_STEP: f32 = std::f32::consts::TAU / 14.0;

pub(crate) const BTN_LEFT: u16 = 0x110;
#[allow(dead_code)]
pub(crate) const BTN_RIGHT: u16 = 0x111;
#[allow(dead_code)]
pub(crate) const BTN_MIDDLE: u16 = 0x112;

pub(crate) const AXIS_NOTCH: f64 = 15.0;

pub(crate) const DRAG_SPIN_PER_PX: f32 = 0.006;

pub(crate) const KDSETMODE: u64 = 0x4B3A;
pub(crate) const KD_TEXT: u64 = 0x00;
pub(crate) const KD_GRAPHICS: u64 = 0x01;

pub(crate) const FOCUS_AUTO: u64 = u64::MAX;

pub(crate) const MIN_SUBSTANTIAL: i32 = 64;

pub(crate) const IDLE_POLL_MS: i32 = 200;
