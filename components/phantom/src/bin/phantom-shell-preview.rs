use phantom::png;
use phantom::shell::{self, render_room, RoomItem, RoomKind, RoomSpeaker, Shell, ShellMode};

const W: u32 = 1280;
const H: u32 = 800;

fn write_png(path: &str, shell: &Shell) {
    write_png_cursor(path, shell, None);
}

fn write_png_cursor(path: &str, shell: &Shell, cursor: Option<(f64, f64)>) {
    let mut buf = vec![0u8; (W * H * 4) as usize];
    shell.render(&mut buf, W, H);
    if let Some((cx, cy)) = cursor {
        shell::draw_cursor(&mut buf, W, H, W * 4, cx, cy);
    }

    let mut rgba = vec![0u8; (W * H * 4) as usize];
    for i in 0..(W * H) as usize {
        rgba[i * 4] = buf[i * 4 + 2];
        rgba[i * 4 + 1] = buf[i * 4 + 1];
        rgba[i * 4 + 2] = buf[i * 4];
        rgba[i * 4 + 3] = 0xff;
    }
    let bytes = png::encode_rgba(W, H, &rgba);
    match std::fs::write(path, &bytes) {
        Ok(()) => println!("wrote {path}  ({} bytes)", bytes.len()),
        Err(e) => eprintln!("FAILED {path}: {e}"),
    }
}

const RW: u32 = 1920;
const RH: u32 = 1080;

fn load_feed(path: &str) -> Vec<RoomItem> {
    let data = std::fs::read_to_string(path).unwrap_or_default();
    let mut items = Vec::new();
    for line in data.lines() {
        let p: Vec<&str> = line.splitn(3, '\t').collect();
        if p.len() < 3 {
            continue;
        }
        let speaker = match p[0] {
            "user" => RoomSpeaker::User,
            "agent1" => RoomSpeaker::Agent(1),
            "agent2" => RoomSpeaker::Agent(2),
            _ => RoomSpeaker::Agent(0),
        };
        let kind = match p[1] {
            "thinking" => RoomKind::Thinking,
            "tool" => RoomKind::Tool,
            "result" => RoomKind::Result,
            _ => RoomKind::Text,
        };
        let text = p[2].replace("\\n", "\n").replace("\\t", "\t");
        items.push(RoomItem { speaker, kind, text });
    }
    items
}

fn blit(dst: &mut [u8], dw: u32, src: &[u8], sw: u32, sh: u32, ox: u32, oy: u32) {
    for ry in 0..sh {
        let dy = oy + ry;
        if dy >= RH {
            break;
        }
        for rx in 0..sw {
            let dx = ox + rx;
            if dx >= dw {
                break;
            }
            let s = ((ry * sw + rx) * 4) as usize;
            let d = ((dy * dw + dx) * 4) as usize;
            if s + 3 < src.len() && d + 3 < dst.len() {
                dst[d..d + 4].copy_from_slice(&src[s..s + 4]);
            }
        }
    }
}

fn render_room_preview(feed: &str) {
    let mut buf = vec![0u8; (RW * RH * 4) as usize];

    let mut bg = onsite_shell();
    bg.mode = ShellMode::Onsite;
    bg.render(&mut buf, RW, RH);

    let rw = (RW as f64 * 0.45) as u32;
    let rh = (RH as f64 * 0.52) as u32;
    let mut room = vec![0u8; (rw * rh * 4) as usize];
    let items = load_feed(feed);
    let room_title = shell::default_room_title();
    render_room(&mut room, rw, rh, &items, &room_title, "");
    let margin = 22u32;
    blit(&mut buf, RW, &room, rw, rh, margin, RH - rh - margin);
    shell::draw_cursor(&mut buf, RW, RH, RW * 4, (margin + rw / 2) as f64, (RH - rh / 2) as f64);

    let mut rgba = vec![0u8; (RW * RH * 4) as usize];
    for i in 0..(RW * RH) as usize {
        rgba[i * 4] = buf[i * 4 + 2];
        rgba[i * 4 + 1] = buf[i * 4 + 1];
        rgba[i * 4 + 2] = buf[i * 4];
        rgba[i * 4 + 3] = 0xff;
    }
    let bytes = png::encode_rgba(RW, RH, &rgba);
    let out = "/tmp/phantom-room.png";
    match std::fs::write(out, &bytes) {
        Ok(()) => println!("wrote {out} ({} items, {} bytes)", items.len(), bytes.len()),
        Err(e) => eprintln!("FAILED {out}: {e}"),
    }
}

fn main() {

    let args: Vec<String> = std::env::args().collect();
    if let Some(i) = args.iter().position(|a| a == "--room") {
        render_room_preview(args.get(i + 1).map(|s| s.as_str()).unwrap_or("/tmp/room.feed"));
        return;
    }
    let mode = args
        .iter()
        .position(|a| a == "--mode")
        .and_then(|i| args.get(i + 1))
        .map(|s| match s.as_str() {
            "auto" => ShellMode::Auto,
            "remote" => ShellMode::Remote,
            "onsite" => ShellMode::Onsite,
            other => {
                eprintln!("unknown --mode '{other}', falling back to onsite");
                ShellMode::Onsite
            }
        });

    match mode {
        Some(ShellMode::Onsite) => write_png("/tmp/phantom-shell-onsite.png", &onsite_shell()),
        Some(ShellMode::Auto) => write_png("/tmp/phantom-shell-auto.png", &idle_shell(ShellMode::Auto)),
        Some(ShellMode::Remote) => write_png("/tmp/phantom-shell-remote.png", &idle_shell(ShellMode::Remote)),
        None => {

            write_png("/tmp/phantom-shell-onsite.png", &onsite_shell());
            write_png("/tmp/phantom-shell-auto.png", &idle_shell(ShellMode::Auto));
            write_png("/tmp/phantom-shell-remote.png", &idle_shell(ShellMode::Remote));

            write_png_cursor("/tmp/phantom-shell-usable-onsite.png", &onsite_shell(), Some((1010.0, 300.0)));
            write_png_cursor("/tmp/phantom-shell-usable-auto.png", &idle_shell(ShellMode::Auto), Some((660.0, 360.0)));
            write_png_cursor("/tmp/phantom-shell-usable-remote.png", &idle_shell(ShellMode::Remote), Some((660.0, 360.0)));

            let step = std::f32::consts::TAU / 7.0;
            for i in 0..5 {
                let mut s = Shell::new();
                s.mode = ShellMode::Onsite;
                s.spin(i as f32 * step);
                write_png(&format!("/tmp/phantom-shell-0{i}.png"), &s);
            }
            println!("phantom-shell-preview: onsite + auto + remote (+ usable cursor frames + 5-frame spin strip).");
        }
    }
}

fn onsite_shell() -> Shell {
    let mut s = Shell::new();
    s.mode = ShellMode::Onsite;
    s.spin(2.0 * std::f32::consts::TAU / 7.0);
    s
}

fn idle_shell(mode: ShellMode) -> Shell {
    let mut s = Shell::new();
    s.set_mode(mode);
    s
}
