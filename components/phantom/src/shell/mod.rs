mod palette;
mod raster;
mod primitives;
mod font;
mod glyphs;
mod clock;
mod cursor;
mod geom;
mod dock;
mod room;
mod frame;
mod state;

pub use clock::{default_room_title, hostname};
pub use cursor::draw_cursor;
pub use dock::{
    dock_height, dock_overlay_contains, dock_overlay_h, dock_overlay_origin, dock_overlay_w,
    dock_reveal_top,
};
pub use raster::Canvas;
pub use room::{render_room, RoomItem, RoomKind, RoomSpeaker};
pub use state::{Shell, ShellMode};
