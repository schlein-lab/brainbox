pub static ICONS_RGBA: &[u8] = include_bytes!("shell_icons.bin");

pub const ICON_SIZE: usize = 128;

pub const ICON_COUNT: usize = 7;

pub static ICON_NAMES: [&str; 7] = ["terminal", "files", "browser", "code", "claude", "settings", "calendar"];
