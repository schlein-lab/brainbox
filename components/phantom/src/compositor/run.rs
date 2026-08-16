use super::consts::*;
use super::forge::*;
use super::launch::*;
use super::paint::*;
use super::pick::*;
use super::room_input::*;
use super::scene_live::*;
use super::signal::*;
use super::state::*;

use crate::server;
use crate::{BufferInfo, Shared};
use phantom::backend::{self, Backend};
use phantom::evdev::{InputEv, InputHub};
use phantom::input::VirtualInput;
use phantom::shell::{dock_reveal_top, Shell, ShellMode};
use phantom::sys::{self, poll_readable_timeout};
use phantom::png;

use std::fs::OpenOptions;
use std::io::Write;
use std::os::fd::AsRawFd;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub fn run(listen_path: &str, shared: Shared) {
    let card = std::env::var("PHANTOM_DRM_CARD").unwrap_or_else(|_| "/dev/dri/card1".into());

    let hold_secs: Option<u64> = std::env::var("PHANTOM_HOLD").ok().and_then(|s| s.parse().ok());
    let shot_path = std::env::var("PHANTOM_SHOT").ok();

    let hang_after: Option<u64> = std::env::var("PHANTOM_HANG").ok().and_then(|s| s.parse().ok());

    let tty = OpenOptions::new().read(true).write(true).open("/dev/tty0").ok();
    let tty_fd = tty.as_ref().map(|f| f.as_raw_fd()).unwrap_or(-1);

    sys::install_screen_guard(tty_fd, KDSETMODE, KD_TEXT, -1, phantom::drm::drop_master_request());
    set_tty(tty_fd, true);

    let mut d: Box<dyn Backend> = match backend::select_backend() {
        Ok(b) => b,
        Err(e) => {
            eprintln!("compositor: cannot acquire a display backend: {e}");
            set_tty(tty_fd, false);
            return;
        }
    };

    if let Some(card_fd) = d.card_fd() {
        sys::set_guard_card(card_fd);
    }
    eprintln!("compositor: {} on {card} — phantom owns the display (tty_fd={tty_fd})", d.describe());

    let _stream_watchdog = match (std::env::var("PHANTOM_STREAM").ok(), d.frame_tap()) {
        (Some(addr), Some(tap)) => {

            let scene: std::sync::Arc<dyn phantom::streamd::SceneSource> = std::sync::Arc::new(
                crate::scene_frame::BoxSceneSource::new(shared.clone(), tap.clone(), d.width() as u16, d.height() as u16),
            );
            match phantom::streamd::spawn(tap, addr, phantom::streamd::Config::default(), Some(scene)) {
                Ok(vd) => Some(vd),
                Err(e) => {
                    eprintln!("compositor: streamd failed to bind PHANTOM_STREAM: {e}");
                    None
                }
            }
        }
        (Some(_), None) => {
            eprintln!("compositor: PHANTOM_STREAM set but backend has no frame tap (DRM) — stream disabled");
            None
        }
        _ => None,
    };

    if let (Some(vaddr), Some(vtap)) = (std::env::var("PHANTOM_VNC").ok(), d.frame_tap()) {

        let sink: std::sync::Arc<dyn phantom::rfbd::InputSink> =
            std::sync::Arc::new(crate::rfb_input::BoxInputSink::new(shared.clone()));
        match phantom::rfbd::spawn(vtap, vaddr, Some(sink)) {
            Ok(()) => {}
            Err(e) => eprintln!("compositor: rfbd failed to bind PHANTOM_VNC: {e}"),
        }
    }

    let (sw, sh_h) = (d.width(), d.height());

    set_seat_size(sw, sh_h);

    set_seat_socket(listen_path);
    let shell: SharedShell = Arc::new(Mutex::new(Shell::new()));
    let mut shell_bg: Vec<u8> = vec![0u8; (sw as usize) * (sh_h as usize) * 4];

    let mut dock_buf: Vec<u8> = vec![0u8; (sw as usize) * (phantom::shell::dock_height() as usize) * 4];
    render_shell_bg(&shell, &mut shell_bg, sw, sh_h);
    let mut shell_dirty = false;
    let mut last_tick = Instant::now();
    let mut last_minute: u64 = 0;

    let room_dir = std::env::var("PHANTOM_ROOM_DIR").unwrap_or_else(|_| "/tmp/phantom-room".into());
    let _ = std::fs::create_dir_all(&room_dir);
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&room_dir, std::fs::Permissions::from_mode(0o777));
    }
    let room_feed = std::env::var("PHANTOM_ROOM_FEED").unwrap_or_else(|_| format!("{room_dir}/room.feed"));
    let room_submit = std::env::var("PHANTOM_ROOM_SUBMIT").unwrap_or_else(|_| format!("{room_dir}/room.submit"));

    let room_title_path = format!("{room_dir}/room.title");
    let default_title = phantom::shell::default_room_title();
    let mut room_title = std::fs::read_to_string(&room_title_path)
        .map(|s| s.trim().to_string())
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| default_title.clone());
    let mut room_title_mtime = std::fs::metadata(&room_title_path).and_then(|m| m.modified()).ok();
    let room_w = (sw as f64 * 0.45) as u32;
    let room_h = (sh_h as f64 * 0.52) as u32;
    let room_margin: u32 = 22;
    let mut room_buf: Vec<u8> = vec![0u8; (room_w as usize) * (room_h as usize) * 4];
    let mut room_items = load_room_feed(&room_feed);
    let mut room_mtime = std::fs::metadata(&room_feed).and_then(|m| m.modified()).ok();
    let mut room_dirty = true;

    let damage: SharedDamage = Arc::new(Damage::new());
    let present: server::Presenter = Arc::new(server::Present::new());
    let wake_fd = damage.wake.read_fd();

    super::state::set_seat_wake(damage.clone());

    server::set_screen_size(sw, sh_h);

    {
        let sh = shared.clone();
        let lp = listen_path.to_string();
        let dmg = damage.clone();
        let prs = present.clone();
        thread::spawn(move || server::run_with(&lp, sh, Some(dmg), Some(prs)));
    }
    eprintln!("compositor: serving {listen_path} — run a client with WAYLAND_DISPLAY=<name>");

    {
        let sh = shared.clone();
        thread::spawn(move || warm_session(&sh));
    }

    let _injector = std::env::var("PHANTOM_TYPE").ok().and_then(|text| match VirtualInput::new() {
        Ok(vi) => {
            thread::sleep(Duration::from_millis(300));
            Some(thread::spawn(move || {
                let mut vi = vi;
                thread::sleep(Duration::from_secs(4));
                for c in text.chars() {
                    let _ = vi.type_str(&c.to_string());
                    thread::sleep(Duration::from_millis(25));
                }
                let _ = vi.enter();
            }))
        }
        Err(e) => {
            eprintln!("compositor: PHANTOM_TYPE set but uinput unavailable: {e}");
            None
        }
    });

    let no_input = matches!(std::env::var("PHANTOM_NO_INPUT").ok().as_deref(), Some("1") | Some("true"));
    let mut hub = if no_input { None } else { InputHub::open_all(true).ok() };
    let fds = hub.as_ref().map(|h| h.fds()).unwrap_or_default();
    if no_input {
        eprintln!("compositor: PHANTOM_NO_INPUT — not grabbing /dev/input (forge-only act)");
    } else if let Some(h) = &hub {
        eprintln!("compositor: grabbed {} input devices", h.device_count());
    }

    sys::sd_notify("READY=1");
    let wd_interval = sys::watchdog_interval();
    let start = Instant::now();
    let deadline = hold_secs.map(|s| start + Duration::from_secs(s));
    let mut last_shot = Instant::now();
    let mut last_wd = Instant::now();
    let mut announced = false;
    let mut entered: Option<u64> = None;
    let mut mods: u32 = 0;

    let mut cursor_x: f64 = sw as f64 * 0.5;
    let mut cursor_y: f64 = sh_h as f64 * 0.5;
    let mut cursor_moved = false;
    let mut ptr_entered: Option<(u64, u32)> = None;
    let mut btn_left_down = false;
    let mut dragging_orb = false;
    let mut drag_last_x: f64 = cursor_x;
    let mut dock_visible = false;

    let mut last_client: Option<(Arc<sys::Mmap>, BufferInfo)> = None;

    let mut last_cover: Option<(Arc<sys::Mmap>, BufferInfo)> = None;

    let mut poll_fds: Vec<std::os::fd::RawFd> = fds.clone();
    let wake_idx: Option<usize> = if wake_fd >= 0 {
        poll_fds.push(wake_fd);
        Some(poll_fds.len() - 1)
    } else {
        None
    };

    let mut force_present = true;

    let profile = std::env::var("PHANTOM_PROFILE").is_ok();
    let mut prof_iters: u64 = 0;
    let mut prof_frames: u64 = 0;
    let mut prof_render = Duration::ZERO;
    let mut prof_last = Instant::now();
    loop {
        prof_iters += 1;
        if profile && prof_last.elapsed() >= Duration::from_secs(5) {
            eprintln!(
                "PROFILE: {} iters, {} frames, {}ms rendering over {:?}",
                prof_iters, prof_frames, prof_render.as_millis(), prof_last.elapsed()
            );
            prof_iters = 0;
            prof_frames = 0;
            prof_render = Duration::ZERO;
            prof_last = Instant::now();
        }

        if hang_after.map(|h| start.elapsed() > Duration::from_secs(h)).unwrap_or(false) {
            eprintln!("compositor: PHANTOM_HANG — wedging the loop (watchdog should restart us)");
            loop {
                thread::sleep(Duration::from_secs(3600));
            }
        }
        if let Some(iv) = wd_interval {
            if last_wd.elapsed() >= iv {
                sys::sd_notify("WATCHDOG=1");
                last_wd = Instant::now();
            }
        }

        if !poll_fds.is_empty() {
            let ready = poll_readable_timeout(&poll_fds, IDLE_POLL_MS);

            if wake_fd >= 0 {
                damage.wake.drain();
            }

            let input_idx = match ready {
                Ok(Some(idx)) if Some(idx) != wake_idx && idx < fds.len() => Some(idx),
                _ => None,
            };
            if input_idx.is_some() {
                if let Some(h) = hub.as_mut() {

                    let mut batch = Vec::new();
                    for didx in 0..fds.len() {
                        batch.extend(h.read(didx));
                    }

                    let fc = focused_cid(&shared);

                    let fp = focused_pointer(&shared);
                    let geom = focused_geom(&shared, sw, sh_h);

                    let onsite_now = shell.lock().map(|s| s.mode == ShellMode::Onsite).unwrap_or(true);
                    for ev in batch {
                        match ev {

                            InputEv::Key { code: 1, down: true } => {
                                eprintln!("compositor: ESC — releasing screen");
                                set_tty(tty_fd, false);
                                return;
                            }

                            InputEv::Key { code: KEY_F9, down: true } => {
                                if let Ok(mut s) = shell.lock() {
                                    s.cycle_mode();
                                    eprintln!("compositor: shell mode -> {:?}", s.mode);
                                }
                                shell_dirty = true;
                            }
                            InputEv::Key { code, down } => {
                                let mb = modbit(code);
                                let old = mods;
                                if mb != 0 {
                                    if down {
                                        mods |= mb;
                                    } else {
                                        mods &= !mb;
                                    }
                                }
                                if let Some(cid) = fc {
                                    if entered != Some(cid) {

                                        if let Some(prev) = entered {
                                            forge_leave(&shared, prev);
                                        }
                                        forge_enter(&shared, cid);
                                        entered = Some(cid);
                                    }
                                    route_key(&shared, cid, code, down, mods, old);
                                } else if onsite_now && down {

                                    match code {
                                        28 | 96 => room_input_submit(),
                                        14 => room_input_backspace(),
                                        _ => {

                                            let shift = mods & 1 != 0;
                                            let altgr = mods & 128 != 0;
                                            let layout = phantom::input::active_layout();
                                            if let Some(ch) = phantom::input::char_from_key_layout(code, shift, altgr, layout) {
                                                let mut tmp = [0u8; 4];
                                                room_input_push(ch.encode_utf8(&mut tmp));
                                            }
                                        }
                                    }
                                    room_dirty = true;
                                    force_present = true;
                                }
                            }

                            InputEv::Motion { dx, dy } => {
                                if dx != 0 || dy != 0 {
                                    cursor_x = (cursor_x + dx as f64).clamp(0.0, (sw.saturating_sub(1)) as f64);
                                    cursor_y = (cursor_y + dy as f64).clamp(0.0, (sh_h.saturating_sub(1)) as f64);
                                    cursor_moved = true;
                                }

                                let was_dock = dock_visible;
                                dock_visible = onsite_now && cursor_y >= dock_reveal_top(sh_h) as f64;
                                if dock_visible != was_dock {
                                    cursor_moved = true;
                                }

                                let on_dock = dock_visible
                                    && phantom::shell::dock_overlay_contains(cursor_x, cursor_y, sw, sh_h);
                                let over = !on_dock && onsite_now && over_client(geom, cursor_x, cursor_y);
                                if dragging_orb {

                                    let ddx = cursor_x - drag_last_x;
                                    if ddx != 0.0 {
                                        if let Ok(mut s) = shell.lock() {
                                            s.spin(ddx as f32 * DRAG_SPIN_PER_PX);
                                        }
                                        drag_last_x = cursor_x;
                                        shell_dirty = true;
                                    }
                                } else if let (Some((cid, surface)), Some((ox, oy, _, _))) = (fp, geom) {
                                    if over {
                                        let (sx, sy) = (cursor_x - ox as f64, cursor_y - oy as f64);
                                        if ptr_entered != Some((cid, surface)) {
                                            if let Some((pc, ps)) = ptr_entered {
                                                pointer_leave(&shared, pc, ps);
                                            }
                                            pointer_enter(&shared, cid, surface, sx, sy);
                                            ptr_entered = Some((cid, surface));
                                        }
                                        pointer_motion(&shared, cid, sx, sy);
                                    } else if let Some((pc, ps)) = ptr_entered.take() {

                                        pointer_leave(&shared, pc, ps);
                                    }
                                } else if let Some((pc, ps)) = ptr_entered.take() {
                                    pointer_leave(&shared, pc, ps);
                                }
                            }

                            InputEv::AbsMotion { x, y } => {
                                if x >= 0 {
                                    cursor_x = (x as f64 / 65535.0 * (sw.saturating_sub(1)) as f64)
                                        .clamp(0.0, (sw.saturating_sub(1)) as f64);
                                    cursor_moved = true;
                                }
                                if y >= 0 {
                                    cursor_y = (y as f64 / 65535.0 * (sh_h.saturating_sub(1)) as f64)
                                        .clamp(0.0, (sh_h.saturating_sub(1)) as f64);
                                    cursor_moved = true;
                                }
                                let was_dock = dock_visible;
                                dock_visible = onsite_now && cursor_y >= dock_reveal_top(sh_h) as f64;
                                if dock_visible != was_dock {
                                    cursor_moved = true;
                                }

                                let on_dock = dock_visible
                                    && phantom::shell::dock_overlay_contains(cursor_x, cursor_y, sw, sh_h);
                                let over = !on_dock && onsite_now && over_client(geom, cursor_x, cursor_y);
                                if dragging_orb {
                                    let ddx = cursor_x - drag_last_x;
                                    if ddx != 0.0 {
                                        if let Ok(mut s) = shell.lock() {
                                            s.spin(ddx as f32 * DRAG_SPIN_PER_PX);
                                        }
                                        drag_last_x = cursor_x;
                                        shell_dirty = true;
                                    }
                                } else if let (Some((cid, surface)), Some((ox, oy, _, _))) = (fp, geom) {
                                    if over {
                                        let (sx, sy) = (cursor_x - ox as f64, cursor_y - oy as f64);
                                        if ptr_entered != Some((cid, surface)) {
                                            if let Some((pc, ps)) = ptr_entered {
                                                pointer_leave(&shared, pc, ps);
                                            }
                                            pointer_enter(&shared, cid, surface, sx, sy);
                                            ptr_entered = Some((cid, surface));
                                        }
                                        pointer_motion(&shared, cid, sx, sy);
                                    } else if let Some((pc, ps)) = ptr_entered.take() {
                                        pointer_leave(&shared, pc, ps);
                                    }
                                } else if let Some((pc, ps)) = ptr_entered.take() {
                                    pointer_leave(&shared, pc, ps);
                                }
                            }

                            InputEv::Button { code, down } => {
                                if code == BTN_LEFT {
                                    btn_left_down = down;
                                }

                                if code == BTN_LEFT
                                    && down
                                    && onsite_now
                                    && cursor_y >= dock_reveal_top(sh_h) as f64
                                {
                                    let hit = shell
                                        .lock()
                                        .ok()
                                        .and_then(|s| s.dock_icon_at(cursor_x, cursor_y, sw, sh_h));
                                    if let Some(i) = hit {
                                        let label = {
                                            let mut s = shell.lock().unwrap();
                                            s.select_index(i);
                                            s.app_label(i).to_string()
                                        };
                                        launch_program(&shared, &label);
                                        dragging_orb = false;
                                        continue;
                                    }

                                }
                                let over = onsite_now && over_client(geom, cursor_x, cursor_y);
                                if over {
                                    if let Some((cid, surface)) = fp {
                                        if let Some((ox, oy, _, _)) = geom {
                                            let (sx, sy) = (cursor_x - ox as f64, cursor_y - oy as f64);
                                            if ptr_entered != Some((cid, surface)) {
                                                if let Some((pc, ps)) = ptr_entered {
                                                    pointer_leave(&shared, pc, ps);
                                                }
                                                pointer_enter(&shared, cid, surface, sx, sy);
                                                ptr_entered = Some((cid, surface));
                                            }
                                            pointer_button(&shared, cid, code, down);
                                        }
                                    }
                                    dragging_orb = false;
                                } else if code == BTN_LEFT {

                                    dragging_orb = down;
                                    drag_last_x = cursor_x;
                                }
                            }

                            InputEv::Wheel(v) => {
                                if v != 0 {
                                    let over = onsite_now && over_client(geom, cursor_x, cursor_y);
                                    if over {
                                        if let Some((cid, surface)) = fp {
                                            if let Some((ox, oy, _, _)) = geom {
                                                let (sx, sy) = (cursor_x - ox as f64, cursor_y - oy as f64);
                                                if ptr_entered != Some((cid, surface)) {
                                                    if let Some((pc, ps)) = ptr_entered {
                                                        pointer_leave(&shared, pc, ps);
                                                    }
                                                    pointer_enter(&shared, cid, surface, sx, sy);
                                                    ptr_entered = Some((cid, surface));
                                                }
                                            }
                                            pointer_axis(&shared, cid, v);
                                        }
                                    } else {
                                        if let Ok(mut s) = shell.lock() {
                                            s.spin(v as f32 * WHEEL_STEP);
                                        }
                                        shell_dirty = true;
                                    }
                                }
                            }
                        }
                    }

                    if !btn_left_down {
                        dragging_orb = false;
                    }
                }
            }
        } else {

            thread::sleep(Duration::from_millis(IDLE_POLL_MS as u64));
        }

        if let Ok(mt) = std::fs::metadata(&room_feed).and_then(|m| m.modified()) {
            if room_mtime != Some(mt) {
                room_mtime = Some(mt);
                room_items = load_room_feed(&room_feed);
                room_dirty = true;
                force_present = true;
            }
        }

        if let Ok(mt) = std::fs::metadata(&room_title_path).and_then(|m| m.modified()) {
            if room_title_mtime != Some(mt) {
                room_title_mtime = Some(mt);
                if let Ok(s) = std::fs::read_to_string(&room_title_path) {
                    let s = s.trim().to_string();
                    if !s.is_empty() {
                        room_title = s;
                        room_dirty = true;
                        force_present = true;
                    }
                }
            }
        }

        if take_room_dirty() {
            room_dirty = true;
            force_present = true;
        }

        if let Some(line) = room_input_take_submit() {
            match OpenOptions::new().create(true).append(true).open(&room_submit) {
                Ok(mut f) => {
                    let _ = writeln!(f, "{line}");
                }
                Err(e) => eprintln!("compositor: room submit write failed: {e}"),
            }
            room_dirty = true;
            force_present = true;
        }

        let show = show_clients() || d.always_composite() || scene_active();

        if last_tick.elapsed() >= Duration::from_secs(1) {
            last_tick = Instant::now();

            if profile || std::env::var_os("PHANTOM_DEBUG").is_some() {
                eprintln!(
                    "CLICKDBG show={} onsite={} fc={:?} fp={} geom={:?} cur=({:.0},{:.0})",
                    show_clients(), shell.lock().map(|s| s.mode == ShellMode::Onsite).unwrap_or(true),
                    focused_cid(&shared), focused_pointer(&shared).is_some(),
                    focused_geom(&shared, sw, sh_h), cursor_x, cursor_y
                );
            }
            let minute = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs() / 60)
                .unwrap_or(0);
            if minute != last_minute {
                last_minute = minute;

                if !(show && client_covers_screen(&shared, d.width(), d.height())) {
                    shell_dirty = true;
                }
            }
        }

        let client_damaged = damage.take() && show;

        let focus_changed = take_focus_dirty();
        let dirty =
            force_present || shell_dirty || cursor_moved || client_damaged || focus_changed;
        force_present = false;
        if !dirty {

            if sys::screen_guard_stop() {
                eprintln!("compositor: stop signal — releasing screen");
                break;
            }
            if let Some(dl) = deadline {
                if Instant::now() > dl {
                    break;
                }
            }
            continue;
        }

        if shell_dirty {
            render_shell_bg(&shell, &mut shell_bg, sw, sh_h);
            shell_dirty = false;
        }

        let onsite = shell.lock().map(|s| s.mode == ShellMode::Onsite).unwrap_or(true);

        if scene_active() {
            d.blit_bgra(0, 0, &shell_bg, 0, sw, sh_h, sw * 4);
            if let Some(specs) = scene_snapshot() {
                let (pitch, cw, ch) = (d.pitch(), d.width(), d.height());
                compose_scene(d.map_mut(), pitch, cw, ch, &shared, &specs);
            }
        } else {
        let fg = if onsite && show {
            match focused_buffer(&shared) {
                Some(cur) => {
                    last_client = Some(cur.clone());
                    Some(cur)
                }
                None if any_client_surface(&shared) => {
                    last_client.clone().or_else(|| focused_buffer_min(&shared, 1))
                }
                None => {
                    last_client = None;
                    None
                }
            }
        } else {
            None
        };

        let cover = if onsite && show {
            match covering_buffer(&shared, sw, sh_h) {
                Some(c) => {
                    last_cover = Some(c.clone());
                    Some(c)
                }
                None if any_client_surface(&shared) => last_cover.clone(),
                None => {
                    last_cover = None;
                    None
                }
            }
        } else {
            None
        };

        let fg_covers = fg
            .as_ref()
            .map(|(_, i)| {
                let (w, h) = (i.width.max(0) as u32, i.height.max(0) as u32);
                d.width().saturating_sub(w) / 2 == 0
                    && d.height().saturating_sub(h) / 2 == 0
                    && w >= d.width()
                    && h >= d.height()
            })
            .unwrap_or(false);

        let blit_full = |d: &mut Box<dyn Backend>, m: &Arc<sys::Mmap>, i: &BufferInfo| {
            let ox = d.width().saturating_sub(i.width as u32) / 2;
            let oy = d.height().saturating_sub(i.height as u32) / 2;
            d.blit_bgra(ox, oy, m.as_slice(), i.offset as usize, i.width as u32, i.height as u32, i.stride as u32);
        };

        if fg_covers {

            if let Some((m, i)) = &fg {
                if !announced {
                    eprintln!("compositor: compositing client buffer {}x{} fmt={}", i.width, i.height, i.format);
                    announced = true;
                }
                blit_full(&mut d, m, i);
            }
        } else {

            match &cover {
                Some((m, i)) => blit_full(&mut d, m, i),
                None => d.blit_bgra(0, 0, &shell_bg, 0, sw, sh_h, sw * 4),
            }
            if let Some((m, i)) = &fg {
                blit_full(&mut d, m, i);
            }
        }

        if onsite && show {
            let (sox, soy) = match &fg {
                Some((_, pinfo)) => (
                    d.width().saturating_sub((pinfo.width.max(0)) as u32) / 2,
                    d.height().saturating_sub((pinfo.height.max(0)) as u32) / 2,
                ),
                None => (0, 0),
            };
            for (m, i, ax, ay) in foreground_subsurfaces(&shared) {
                let cx = (sox as i32 + ax).max(0) as u32;
                let cy = (soy as i32 + ay).max(0) as u32;
                d.blit_bgra(cx, cy, m.as_slice(), i.offset as usize, i.width as u32, i.height as u32, i.stride as u32);
            }
        }
        }

        let dock_show = match dock_mode() {
            1 => onsite,
            2 => false,
            _ => dock_visible,
        };
        if dock_show {
            let (tw, th) = (phantom::shell::dock_overlay_w(), phantom::shell::dock_overlay_h());
            let (ox, oy) = phantom::shell::dock_overlay_origin(sw, sh_h);
            let pitch = d.pitch() as usize;
            copy_scanout_region(d.map_mut(), pitch, sw, sh_h, ox, oy, tw, th, &mut dock_buf);
            if let Ok(s) = shell.lock() {
                let hover = s.dock_icon_at(cursor_x, cursor_y, sw, sh_h);
                s.render_dock_tray(&mut dock_buf, tw, th, hover);
            }
            d.blit_bgra(ox, oy, &dock_buf, 0, tw, th, tw * 4);
        }

        if onsite && !show {
            if room_dirty {

                let line = room_input_snapshot();
                let shown = if line.is_empty() { String::new() } else { format!("{line}\u{2588}") };
                phantom::shell::render_room(&mut room_buf, room_w, room_h, &room_items, &room_title, &shown);
                room_dirty = false;
            }
            let ry = sh_h.saturating_sub(room_h + room_margin);
            d.blit_bgra(room_margin, ry, &room_buf, 0, room_w, room_h, room_w * 4);
        }

        let _ = cursor_moved;
        cursor_moved = false;

        if !no_input {
            let (cw, ch, pitch) = (d.width(), d.height(), d.pitch());
            phantom::shell::draw_cursor(d.map_mut(), cw, ch, pitch, cursor_x, cursor_y);
        }

        d.dirty();

        present.present();

        d.present();
        
        
        if let Some(z) = phantom::sehwerk::wechsel_abholen() {
            crate::winbus::melde(&format!("\"ereignis\":\"state\",\"zustand\":\"{}\"", z.wort()));
        }

        if let Some(p) = &shot_path {
            if last_shot.elapsed() > Duration::from_millis(1000) {
                let rgba = d.snapshot_rgba();
                let bytes = png::encode_rgba(d.width(), d.height(), &rgba);
                if let Ok(mut f) = OpenOptions::new().create(true).write(true).truncate(true).open(p) {
                    let _ = f.write_all(&bytes);
                }
                last_shot = Instant::now();
            }
        }

        if sys::screen_guard_stop() {
            eprintln!("compositor: stop signal — releasing screen");
            break;
        }
        if let Some(dl) = deadline {
            if Instant::now() > dl {
                break;
            }
        }
    }
    sys::sd_notify("STOPPING=1");
    set_tty(tty_fd, false);
}
