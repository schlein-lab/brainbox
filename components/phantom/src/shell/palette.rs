use super::raster::Rgb;

pub(crate) const PAPER: Rgb = Rgb(0xf4, 0xed, 0xda);
pub(crate) const PAPER_HI: Rgb = Rgb(0xfa, 0xf6, 0xea);
pub(crate) const PAPER_ALT: Rgb = Rgb(0xef, 0xe6, 0xcf);
pub(crate) const LINE: Rgb = Rgb(0xcc, 0xc0, 0xa3);

pub(crate) const INK: Rgb = Rgb(0x1c, 0x17, 0x12);
pub(crate) const INK_SOFT: Rgb = Rgb(0x6f, 0x65, 0x52);
pub(crate) const INK_FAINT: Rgb = Rgb(0x9b, 0x91, 0x7a);

pub(crate) const ACCENT: Rgb = Rgb(0x51, 0x59, 0xd6);
#[allow(dead_code)]
pub(crate) const ACCENT_SOFT: Rgb = Rgb(0x9a, 0xa7, 0xff);
#[allow(dead_code)]
pub(crate) const ACCENT_TINT: Rgb = Rgb(0xe7, 0xe9, 0xfb);
pub(crate) const OK_GREEN: Rgb = Rgb(0x3f, 0x9d, 0x6d);
#[allow(dead_code)]
pub(crate) const WARN_AMBER: Rgb = Rgb(0xc9, 0x8a, 0x2e);
#[allow(dead_code)]
pub(crate) const ALERT_RED: Rgb = Rgb(0xd6, 0x45, 0x2b);
