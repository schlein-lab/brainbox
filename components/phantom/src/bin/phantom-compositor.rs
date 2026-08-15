use phantom::drm::Display;
use phantom::evdev::{InputEv, InputHub};
use phantom::input::{char_from_key, VirtualInput};
use phantom::png;
use phantom::sys::poll_readable_timeout;
use std::fs::OpenOptions;
use std::io::Write;
use std::os::fd::AsRawFd;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const KDSETMODE: u64 = 0x4B3A;
const KD_TEXT: u64 = 0x00;
const KD_GRAPHICS: u64 = 0x01;

fn set_vt_mode(graphics: bool) -> bool {

    match OpenOptions::new().read(true).write(true).open("/dev/tty0") {
        Ok(f) => {
            let mode = if graphics { KD_GRAPHICS } else { KD_TEXT };
            phantom::sys::ioctl_val(f.as_raw_fd(), KDSETMODE, mode).is_ok()
        }
        Err(_) => false,
    }
}

fn arm_screen_guard() -> Option<std::fs::File> {
    let tty = OpenOptions::new().read(true).write(true).open("/dev/tty0").ok()?;
    phantom::sys::install_screen_guard(tty.as_raw_fd(), KDSETMODE, KD_TEXT, -1, phantom::drm::drop_master_request());
    Some(tty)
}

fn arg(flag: &str, default: &str) -> String {
    let a: Vec<String> = std::env::args().collect();
    for i in 0..a.len() {
        if a[i] == flag {
            if let Some(v) = a.get(i + 1) {
                return v.clone();
            }
        }
    }
    default.to_string()
}

fn paint(d: &mut Display) {
    let (w, h) = (d.width, d.height);
    for y in 0..h {
        for x in 0..w {
            let r = (x * 255 / w.max(1)) as u8;
            let g = (y * 255 / h.max(1)) as u8;
            let b = 0x40u8;
            d.put(x, y, r, g, b);
        }
    }

    for x in 0..w {
        d.put(x, h / 2, 0xff, 0xff, 0xff);
    }
    for y in 0..h {
        d.put(w / 2, y, 0xff, 0xff, 0xff);
    }

    for y in 0..32.min(h) {
        for x in 0..32.min(w) {
            d.put(x, y, 0xff, 0xff, 0xff);
            d.put(w - 1 - x, y, 0xff, 0xff, 0xff);
            d.put(x, h - 1 - y, 0xff, 0xff, 0xff);
            d.put(w - 1 - x, h - 1 - y, 0xff, 0xff, 0xff);
        }
    }
}

fn rect(d: &mut Display, x0: u32, y0: u32, w: u32, h: u32, r: u8, g: u8, b: u8) {
    for y in y0..y0 + h {
        for x in x0..x0 + w {
            d.put(x, y, r, g, b);
        }
    }
}

fn run_m1(card: &str, out: &str) {
    let _tty_guard = arm_screen_guard();
    let _vt = set_vt_mode(true);
    let mut d = match Display::open(card) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("[M1] FAIL opening {card}: {e}");
            set_vt_mode(false);
            std::process::exit(1);
        }
    };
    phantom::sys::set_guard_card(d.card_fd());
    let (w, h) = (d.width, d.height);
    println!("[M1] modeset OK: {w}x{h}, phantom owns the screen; now grabbing input.");

    for y in 0..h {
        for x in 0..w {
            let r = (x * 90 / w.max(1)) as u8;
            let g = (y * 90 / h.max(1)) as u8;
            d.put(x, y, r, g, 0x28);
        }
    }

    let vi = match VirtualInput::new() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[M1] FAIL creating uinput device: {e} (need /dev/uinput)");
            return;
        }
    };
    std::thread::sleep(Duration::from_millis(300));
    let mut hub = match InputHub::open_all(true) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("[M1] FAIL opening input: {e}");
            return;
        }
    };
    println!("[M1] grabbed {} input devices (EVIOCGRAB).", hub.device_count());

    let done = Arc::new(AtomicBool::new(false));
    let done_t = done.clone();
    let injector = std::thread::spawn(move || {
        let mut vi = vi;
        std::thread::sleep(Duration::from_millis(400));
        let step = |vi: &mut VirtualInput, n, dx, dy| {
            for _ in 0..n {
                let _ = vi.move_rel(dx, dy);
                std::thread::sleep(Duration::from_millis(4));
            }
        };
        step(&mut vi, 150, 2, 0);
        step(&mut vi, 120, 0, 2);
        step(&mut vi, 150, -2, 0);
        step(&mut vi, 120, 0, -2);
        std::thread::sleep(Duration::from_millis(150));

        for c in "PHANTOM OWNS INPUT".chars() {
            let _ = vi.type_str(&c.to_string());
            std::thread::sleep(Duration::from_millis(15));
        }
        std::thread::sleep(Duration::from_millis(100));
        let _ = vi.click(phantom::input::BTN_LEFT);
        std::thread::sleep(Duration::from_millis(200));
        done_t.store(true, Ordering::SeqCst);
    });

    let mut cx = (w / 2) as i32;
    let mut cy = (h / 2) as i32;
    let mut shift = false;
    let mut typed = String::new();
    let (mut n_motion, mut n_key, mut n_btn) = (0u64, 0u64, 0u64);
    let mut key_ticks = 0u32;
    let fds = hub.fds();
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut done_at: Option<Instant> = None;

    loop {
        if let Ok(Some(idx)) = poll_readable_timeout(&fds, 40) {
            for ev in hub.read(idx) {
                match ev {
                    InputEv::Motion { dx, dy } => {
                        n_motion += 1;
                        cx = (cx + dx).clamp(0, w as i32 - 1);
                        cy = (cy + dy).clamp(0, h as i32 - 1);

                        rect(&mut d, cx.max(1) as u32 - 1, cy.max(1) as u32 - 1, 3, 3, 0x20, 0xff, 0xff);
                    }
                    InputEv::Key { code, down } => {
                        if code == 42 || code == 54 {
                            shift = down;
                            continue;
                        }
                        if down {
                            n_key += 1;
                            if let Some(c) = char_from_key(code, shift) {
                                typed.push(c);
                            }

                            let tx = 10 + key_ticks * 8;
                            rect(&mut d, tx, h - 30, 6, 20, 0x30, 0xff, 0x40);
                            key_ticks += 1;
                        }
                    }
                    InputEv::Button { down, .. } => {
                        if down {
                            n_btn += 1;

                            rect(&mut d, cx.max(8) as u32 - 8, cy.max(8) as u32 - 8, 16, 16, 0xff, 0x20, 0xff);
                        }
                    }
                    InputEv::Wheel(_) => {}

                    InputEv::AbsMotion { .. } => {}
                }
            }
        }
        if done.load(Ordering::SeqCst) && done_at.is_none() {
            done_at = Some(Instant::now());
        }
        if let Some(t) = done_at {
            if t.elapsed() > Duration::from_millis(300) {
                break;
            }
        }
        if Instant::now() > deadline {
            break;
        }
    }
    let _ = injector.join();

    rect(&mut d, cx.max(6) as u32 - 6, cy.max(6) as u32 - 6, 12, 12, 0xff, 0xff, 0xff);

    let rgba = d.snapshot_rgba();
    let png = png::encode_rgba(w, h, &rgba);
    if let Ok(mut f) = OpenOptions::new().create(true).write(true).truncate(true).open(out) {
        let _ = f.write_all(&png);
    }
    println!("[M1] events received below the compositor: motion={n_motion} key={n_key} button={n_btn}");
    println!("[M1] decoded keystrokes: {typed:?}");
    println!("[M1] cursor rest position: ({cx},{cy}); screenshot: {out}");
    let ok = n_motion > 100 && typed.contains("PHANTOM OWNS INPUT") && n_btn >= 1;
    println!("[M1] {}", if ok { "VERIFIED: phantom owns input (path + typed string + click all received)." } else { "INCOMPLETE: expected sequence not fully observed." });

    drop(hub);
    drop(d);
    set_vt_mode(false);
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let card = arg("--card", "/dev/dri/card1");
    let out = arg("--out", "/tmp/phantom-scanout.png");

    if args.iter().any(|a| a == "--m1") {
        let out_m1 = arg("--out", "/tmp/phantom-m1.png");
        run_m1(&card, &out_m1);
        return;
    }

    let hold: u64 = arg("--hold", "4").parse().unwrap_or(4);

    let _tty_guard = arm_screen_guard();
    let vt = set_vt_mode(true);
    eprintln!("[M0] VT graphics mode: {}", if vt { "set" } else { "unavailable (continuing)" });

    let mut d = match Display::open(&card) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("[M0] FAIL opening {card}: {e}");
            set_vt_mode(false);
            std::process::exit(1);
        }
    };
    phantom::sys::set_guard_card(d.card_fd());
    println!(
        "[M0] modeset OK: {}x{} '{}' pitch={} crtc={} connector={} fb={}",
        d.width,
        d.height,
        d.mode.name_str(),
        d.pitch,
        d.crtc_id,
        d.connector_id,
        d.fb_id()
    );

    paint(&mut d);

    match d.verify_crtc() {
        Ok((fb, valid, mw, mh)) => {
            let ours = fb == d.fb_id();
            println!(
                "[M0] GETCRTC: fb={} (ours={}) mode_valid={} {}x{}",
                fb, ours, valid, mw, mh
            );
            if ours && valid == 1 {
                println!("[M0] VERIFIED: phantom owns the scanout. The screen is ours.");
            } else {
                println!("[M0] WARNING: CRTC does not report our fb — scanout not confirmed.");
            }
        }
        Err(e) => println!("[M0] GETCRTC failed: {e}"),
    }

    let rgba = d.snapshot_rgba();
    let png = png::encode_rgba(d.width, d.height, &rgba);
    match OpenOptions::new().create(true).write(true).truncate(true).open(&out) {
        Ok(mut f) => {
            if f.write_all(&png).is_ok() {
                println!("[M0] scanout screenshot written: {out} ({} bytes)", png.len());
            }
        }
        Err(e) => println!("[M0] could not write {out}: {e}"),
    }

    println!("[M0] holding {hold}s, then releasing the screen…");
    std::thread::sleep(std::time::Duration::from_secs(hold));

    drop(d);
    set_vt_mode(false);
    println!("[M0] released. screen handed back.");
}
