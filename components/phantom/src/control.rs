use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixListener;
use std::thread;

use phantom::input::{active_layout, char_to_key_layout, Modifier};
use phantom::sys;

use crate::act::{do_inject, do_snapshot, sense_text, with_kbd};
use crate::compositor;
use crate::state::{Shared, GATE};
use crate::xwl::{cid_ready, xwl_set_title};

pub(crate) fn control_server(path: &str, shared: Shared) {
    let _ = std::fs::remove_file(path);
    let listener = match UnixListener::bind(path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom: cannot bind control socket {path}: {e}");
            return;
        }
    };
    for conn in listener.incoming() {
        let Ok(mut s) = conn else { continue };
        let shared = shared.clone();
        thread::spawn(move || {
            let mut line = String::new();
            let mut buf = [0u8; 4096];
            let mut r = match s.try_clone() {
                Ok(r) => r,
                Err(_) => return,
            };
            loop {
                match r.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        line.push_str(&String::from_utf8_lossy(&buf[..n]));
                        if line.contains('\n') {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }

            let peer = sys::peer_uid(s.as_raw_fd());
            let reply = match GATE.get() {
                Some(g) => match g.authorize(line.trim(), peer) {
                    Ok(eff) => handle_control(&eff, &shared),
                    Err(msg) => format!("error: {msg}\n"),
                },
                None => handle_control(line.trim(), &shared),
            };
            let _ = s.write_all(reply.as_bytes());
        });
    }
}

fn parse_keys(sub: &str, arg: Option<&str>) -> Result<Vec<(u16, Modifier)>, String> {
    match sub {

        "text" | "type" => {
            let layout = active_layout();
            Ok(arg.unwrap_or("").chars().filter_map(|c| char_to_key_layout(c, layout)).collect())
        }
        "enter" => Ok(vec![(28, Modifier::None)]),
        "key" => arg
            .and_then(|s| s.trim().parse::<u16>().ok())
            .map(|c| vec![(c, Modifier::None)])
            .ok_or_else(|| "key <code>".to_string()),
        _ => Err("text <s> | enter | key <code>".to_string()),
    }
}

pub(crate) fn cid_by_title(shared: &Shared, substr: &str) -> Option<u64> {
    let g = shared.lock().unwrap();
    let q = substr.to_lowercase();
    g.iter()
        .filter(|(cid, _)| cid_ready(&g, **cid))
        .find(|(_, st)| st.title.as_deref().map(|t| t.to_lowercase().contains(&q)).unwrap_or(false))
        .map(|(cid, _)| *cid)
}

pub(crate) fn resolve_target(shared: &Shared, tok: &str) -> Option<u64> {
    if let Ok(cid) = tok.parse::<u64>() {
        return shared.lock().unwrap().contains_key(&cid).then_some(cid);
    }
    cid_by_title(shared, tok.strip_prefix('@').unwrap_or(tok))
}

fn parse_source(shared: &Shared, tok: &str) -> Result<compositor::SourceKind, String> {
    if let Some(hex) = tok.strip_prefix('#').or_else(|| tok.strip_prefix("solid:")) {
        if hex.len() == 6 {
            let r = u8::from_str_radix(&hex[0..2], 16).map_err(|_| "colour must be #RRGGBB")?;
            let g = u8::from_str_radix(&hex[2..4], 16).map_err(|_| "colour must be #RRGGBB")?;
            let b = u8::from_str_radix(&hex[4..6], 16).map_err(|_| "colour must be #RRGGBB")?;
            return Ok(compositor::SourceKind::Solid([r, g, b]));
        }
        return Err("colour must be #RRGGBB".into());
    }
    match resolve_target(shared, tok) {
        Some(cid) => Ok(compositor::SourceKind::Client(cid)),
        None => Err(format!("no such target {tok:?} (cid, @title, or #RRGGBB)")),
    }
}

fn parse_scene_add(shared: &Shared, a: &[&str]) -> Result<compositor::PlacementSpec, String> {
    use phantom::scene::{Crop, Fit, Rect};
    if a.len() < 5 {
        return Err("need <target> <dx> <dy> <dw> <dh>".into());
    }
    let src = parse_source(shared, a[0])?;
    let dx = a[1].parse::<i32>().map_err(|_| "dx not an int")?;
    let dy = a[2].parse::<i32>().map_err(|_| "dy not an int")?;
    let dw = a[3].parse::<u32>().map_err(|_| "dw not a uint")?;
    let dh = a[4].parse::<u32>().map_err(|_| "dh not a uint")?;
    let mut crop = None;
    let mut fit = Fit::Stretch;
    let mut z = 0i32;
    let mut opacity = 255u8;
    let mut i = 5;
    while i < a.len() {
        match a[i] {
            "crop" => {
                if i + 4 >= a.len() {
                    return Err("crop <sx> <sy> <sw> <sh>".into());
                }
                let x = a[i + 1].parse().map_err(|_| "crop sx")?;
                let y = a[i + 2].parse().map_err(|_| "crop sy")?;
                let w = a[i + 3].parse().map_err(|_| "crop sw")?;
                let h = a[i + 4].parse().map_err(|_| "crop sh")?;
                crop = Some(Crop { x, y, w, h });
                i += 5;
            }
            "fit" => {
                let f = a.get(i + 1).copied().ok_or("fit <mode>")?;
                fit = match f {
                    "stretch" => Fit::Stretch,
                    "contain" => Fit::Contain,
                    "cover" => Fit::Cover,
                    "none" => Fit::None,
                    _ => return Err("fit stretch|contain|cover|none".into()),
                };
                i += 2;
            }
            "z" => {
                z = a.get(i + 1).copied().ok_or("z <n>")?.parse().map_err(|_| "z not an int")?;
                i += 2;
            }
            "opacity" | "op" => {
                opacity = a.get(i + 1).copied().ok_or("opacity <n>")?.parse().map_err(|_| "opacity 0-255")?;
                i += 2;
            }
            other => return Err(format!("unknown option {other:?}")),
        }
    }
    Ok(compositor::PlacementSpec { src, crop, dest: Rect { x: dx, y: dy, w: dw, h: dh }, fit, z, opacity })
}

fn handle_control(line: &str, shared: &Shared) -> String {
    let mut it = line.splitn(4, ' ');
    match it.next().unwrap_or("") {
        "clients" | "list" => {
            let fg = compositor::foreground_cid(shared);
            let g = shared.lock().unwrap();
            if g.is_empty() {
                return "(no clients connected)\n".into();
            }
            let mut ids: Vec<&u64> = g.keys().collect();
            ids.sort();
            let mut out = String::new();
            for cid in ids {
                let st = &g[cid];

                let mark = if Some(*cid) == fg { " *" } else { "" };
                out.push_str(&format!(
                    "cid={cid} pid={} ready={} title={:?} app_id={:?}{mark}\n",
                    st.pid.map(|p| p.to_string()).unwrap_or_else(|| "?".into()),
                    cid_ready(&g, *cid),
                    st.title.as_deref().unwrap_or(""),
                    st.app_id.as_deref().unwrap_or(""),
                ));
            }
            out
        }
        "cap" | "cap-status" => match GATE.get() {
            Some(g) => format!("{}\n", g.status()),
            None => "policy=off (gate not initialised)\n".into(),
        },
        "xwin" => {

            let rest = line.strip_prefix("xwin ").unwrap_or("");
            let (ser, title) = rest.split_once(' ').unwrap_or((rest, ""));
            match ser.parse::<u64>() {
                Ok(serial) if xwl_set_title(shared, serial, title) => "ok\n".into(),
                Ok(_) => "ok: no window for that serial (yet)\n".into(),
                Err(_) => "usage: xwin <serial> <title>\n".into(),
            }
        }
        "inject" => {
            let Some(cid) = it.next().and_then(|s| s.parse::<u64>().ok()) else {
                return "usage: inject <cid> text <s> | enter | key <code>\n".into();
            };
            let keys = match parse_keys(it.next().unwrap_or(""), it.next()) {
                Ok(k) => k,
                Err(e) => return format!("usage: inject <cid> {e}\n"),
            };
            match do_inject(shared, cid, &keys) {
                Ok(n) => format!("ok: injected {n} into cid={cid}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "inject-title" => {
            let substr = it.next().unwrap_or("");
            if substr.is_empty() {
                return "usage: inject-title <substr> text <s> | enter | key <code>\n".into();
            }
            let keys = match parse_keys(it.next().unwrap_or(""), it.next()) {
                Ok(k) => k,
                Err(e) => return format!("usage: inject-title <substr> {e}\n"),
            };
            let Some(cid) = cid_by_title(shared, substr) else {
                return format!("error: no ready client with title containing {substr:?}\n");
            };
            match do_inject(shared, cid, &keys) {
                Ok(n) => format!("ok: injected {n} into cid={cid} (title~{substr:?})\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "snapshot" => {
            let Some(cid) = it.next().and_then(|s| s.parse::<u64>().ok()) else {
                return "usage: snapshot <cid> [path.png]\n".into();
            };
            let path = it.next().map(|s| s.to_string());
            match do_snapshot(shared, cid, path) {
                Ok(p) => format!("ok: {p}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }

        "act" => {
            let Some(cid) = it.next().and_then(|t| resolve_target(shared, t)) else {
                return "usage: act <cid|@title> type <s>|enter|key <code>|move <x> <y>|click <x> <y> [btn]|drag <x1> <y1> <x2> <y2> [btn]|scroll <x> <y> <n>\n".into();
            };
            let sub = it.next().unwrap_or("");
            match sub {
                "move" | "click" | "drag" | "scroll" => {
                    let args: Vec<&str> = it.next().unwrap_or("").split_whitespace().collect();
                    match compositor::act_pointer(shared, cid, sub, &args) {
                        Ok(s) => s,
                        Err(e) => format!("error: {e}\n"),
                    }
                }
                _ => {
                    let keys = match parse_keys(sub, it.next()) {
                        Ok(k) => k,
                        Err(e) => return format!("usage: act <target> {e}\n"),
                    };
                    match do_inject(shared, cid, &keys) {
                        Ok(n) => format!("ok: acted {n} on cid={cid}\n"),
                        Err(e) => format!("error: {e}\n"),
                    }
                }
            }
        }

        "focus" => match it.next() {
            None => match compositor::focused_get() {
                None => "focus: auto (highest renderable cid)\n".into(),
                Some(f) => format!("focus: cid={f}\n"),
            },
            Some("auto") => {
                compositor::set_focused_auto();
                "ok: focus -> auto\n".into()
            }
            Some(tok) => match resolve_target(shared, tok) {
                Some(cid) => {
                    compositor::set_focused(cid);
                    format!("ok: focus -> cid={cid}\n")
                }
                None => format!("error: no such target {tok:?} (cid, @title, or 'auto')\n"),
            },
        },

        "dock" => match it.next() {
            Some("show") => {
                compositor::set_dock_mode(1);
                "ok: dock -> show\n".into()
            }
            Some("hide") => {
                compositor::set_dock_mode(2);
                "ok: dock -> hide\n".into()
            }
            Some("auto") | None => {
                compositor::set_dock_mode(0);
                "ok: dock -> auto (cursor-revealed)\n".into()
            }
            Some(other) => format!("usage: dock show|hide|auto (got {other:?})\n"),
        },

        "spielwiese" | "foreground" | "fg" => match it.next() {
            None => format!(
                "spielwiese: {}\n",
                if compositor::show_clients() { "on" } else { "off" }
            ),
            Some("off") | Some("hide") => {
                compositor::set_show_clients(false);
                "ok: spielwiese off (shell + rooms only)\n".into()
            }
            Some("on") | Some("show") => {
                compositor::set_show_clients(true);
                "ok: spielwiese on (clients composited)\n".into()
            }
            Some("auto") => {
                compositor::set_show_clients(true);
                compositor::set_focused_auto();
                "ok: spielwiese on -> focus auto\n".into()
            }
            Some(tok) => match resolve_target(shared, tok) {
                Some(cid) => {
                    compositor::set_show_clients(true);
                    compositor::set_focused(cid);
                    format!("ok: spielwiese on -> focus cid={cid}\n")
                }
                None => format!("error: no such target {tok:?} (cid, @title, on, off, auto)\n"),
            },
        },

        "room" => match it.next() {
            Some("type") => {
                let text: Vec<&str> = it.collect();
                let text = text.join(" ");
                compositor::room_input_push(&text);
                format!("ok: room type (+{} chars)\n", text.len())
            }
            Some("key") => match it.next() {
                Some("back") | Some("backspace") => {
                    compositor::room_input_backspace();
                    "ok: room backspace\n".into()
                }
                Some("enter") => {
                    compositor::room_input_submit();
                    "ok: room submit\n".into()
                }
                other => format!("usage: room key back|enter (got {other:?})\n"),
            },
            Some("submit") => {
                compositor::room_input_submit();
                "ok: room submit\n".into()
            }
            Some("clear") => {
                compositor::room_input_clear();
                "ok: room clear\n".into()
            }
            Some("get") | None => format!("room input: {:?}\n", compositor::room_input_snapshot()),
            Some(other) => {
                format!("usage: room type <text> | key back|enter | submit | clear | get (got {other:?})\n")
            }
        },

        "launch" => {
            let prog = it.next().unwrap_or("").to_ascii_uppercase();
            if prog.is_empty() {
                return "usage: launch <TERMINAL|FILES|BROWSER|CODE|CLAUDE|SETTINGS|CALENDAR>\n"
                    .into();
            }
            compositor::launch_program(shared, &prog);
            format!("ok: launching {prog}\n")
        }

        "spawn" => {
            let cmd = line.strip_prefix("spawn ").unwrap_or("").trim();
            if cmd.is_empty() {
                return "usage: spawn <command line>  (e.g. spawn gnome-calculator | spawn snap-run firefox)\n".into();
            }
            compositor::spawn_command(shared, cmd);
            format!("ok: spawning {cmd}\n")
        }
        "sense" => {
            let Some(cid) = it.next().and_then(|t| resolve_target(shared, t)) else {
                return "usage: sense <cid|@title> shot [path] | text | intent\n".into();
            };
            match it.next().unwrap_or("shot") {
                "shot" => match do_snapshot(shared, cid, it.next().map(|s| s.to_string())) {
                    Ok(p) => format!("ok: {p}\n"),
                    Err(e) => format!("error: {e}\n"),
                },

                "text" | "intent" => sense_text(shared, cid),
                other => format!("unknown sense: {other} (shot|text|intent)\n"),
            }
        }

        "keys" => {
            let Some(cid) = it.next().and_then(|t| resolve_target(shared, t)) else {
                return "usage: keys <cid|@title> [clear]\n".into();
            };
            let clear = it.next() == Some("clear");
            let mut g = shared.lock().unwrap();
            let Some(st) = g.get_mut(&cid) else { return "error: no such client\n".into() };
            let out = st.keylog.clone();
            if clear {
                st.keylog.clear();
            }
            format!("{out}\n")
        }

        "kbd" => {

            match line.split_whitespace().nth(1).unwrap_or("") {
                "type" => {
                    let text = line.strip_prefix("kbd type ").unwrap_or("");
                    with_kbd(|vi| vi.type_str(text))
                }
                "enter" => with_kbd(|vi| vi.enter()),
                "key" => match line.split_whitespace().nth(2).and_then(|s| s.parse::<u16>().ok()) {
                    Some(code) => with_kbd(|vi| vi.key(code)),
                    None => "err: key <code> (u16 evdev keycode)\n".into(),
                },

                "map" => {
                    let chars: String = line.strip_prefix("kbd map ").unwrap_or("").into();
                    let layout = active_layout();
                    let mut out = format!("layout={layout:?}\n");
                    for ch in chars.chars() {
                        match char_to_key_layout(ch, layout) {
                            Some((code, modi)) => {
                                out.push_str(&format!("{ch:?} -> ({code},{modi:?})\n"))
                            }
                            None => out.push_str(&format!("{ch:?} -> (skip)\n")),
                        }
                    }
                    out
                }
                _ => "err: kbd type <text> | kbd enter | kbd key <code> | kbd map <chars>\n".into(),
            }
        }

        "scene" => {
            let toks: Vec<&str> = line.split_whitespace().collect();
            match toks.get(1).copied() {
                None | Some("get") | Some("status") => compositor::scene_describe(),
                Some("clear") | Some("off") => {
                    compositor::scene_clear();
                    "ok: scene cleared (default single-foreground compositing)\n".into()
                }
                Some("set") | Some("add") => {
                    if toks[1] == "set" {
                        compositor::scene_clear();
                    }
                    match parse_scene_add(shared, &toks[2..]) {
                        Ok(spec) => {
                            compositor::scene_add(spec);
                            format!("ok: scene now {} placement(s)\n", compositor::scene_len())
                        }
                        Err(e) => format!(
                            "usage: scene add <cid|@title|#RRGGBB> <dx> <dy> <dw> <dh> [crop <sx> <sy> <sw> <sh>] [fit stretch|contain|cover|none] [z <n>] [opacity <0-255>]\n  ({e})\n"
                        ),
                    }
                }
                Some(other) => format!(
                    "usage: scene [get] | clear | set|add <target> <dx> <dy> <dw> <dh> [crop ...] [fit ...] [z ...] [opacity ...] (got {other:?})\n"
                ),
            }
        }

        "screen" => {
            let toks: Vec<&str> = line.split_whitespace().collect();
            let action = toks.get(1).copied().unwrap_or("");
            if action.is_empty() {
                return "usage: screen move|click|drag|scroll <x> <y> [..] | text <s> | key <code> | keycode <code> [modmask] | enter\n".into();
            }
            match compositor::screen_input(shared, action, &toks[2..]) {
                Ok(s) => s,
                Err(e) => format!("error: {e}\n"),
            }
        }
        "" => "phantom hub. targets = <cid>|@<title>. commands: list/clients | act <t> type|enter|key | sense <t> shot|text | scene set <t> <x> <y> <w> <h> | screen click <x> <y> | kbd type|enter|key (system-wide uinput) | (inject, inject-title, snapshot aliases)\n".into(),
        other => format!("unknown command: {other}\n"),
    }
}
