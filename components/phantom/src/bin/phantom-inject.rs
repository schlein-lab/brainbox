use phantom::input::{VirtualInput, BTN_LEFT, BTN_MIDDLE, BTN_RIGHT};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::time::Duration;

fn default_socket_path() -> String {
    if let Ok(p) = std::env::var("PHANTOM_INJECT_SOCK") {
        return p;
    }
    let run = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".to_string());
    format!("{run}/phantom-inject.sock")
}

fn main() {

    let path = std::env::args().nth(1).unwrap_or_else(default_socket_path);

    let mut vi = match VirtualInput::new() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("phantom-inject: cannot open /dev/uinput: {e}");
            eprintln!("        run as root, or join the `input` group + install the");
            eprintln!("        /dev/uinput udev rule (see setup/99-phantom-uinput.rules).");
            std::process::exit(1);
        }
    };

    std::thread::sleep(Duration::from_millis(500));

    let _ = std::fs::remove_file(&path);
    let listener = match UnixListener::bind(&path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom-inject: cannot bind {path}: {e}");
            std::process::exit(1);
        }
    };

    let _ = std::fs::set_permissions(&path, std::os::unix::fs::PermissionsExt::from_mode(0o600));

    eprintln!("phantom-inject: virtual device live, listening on {path}");

    for conn in listener.incoming() {
        match conn {
            Ok(stream) => {
                if let Err(e) = handle(&mut vi, stream) {
                    eprintln!("phantom-inject: connection error: {e}");
                }
            }
            Err(e) => eprintln!("phantom-inject: accept error: {e}"),
        }
    }
}

fn handle(vi: &mut VirtualInput, stream: UnixStream) -> std::io::Result<()> {
    let mut out = stream.try_clone()?;
    let reader = BufReader::new(stream);
    for line in reader.lines() {
        let line = line?;
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            continue;
        }
        let (verb, rest) = line.split_once(' ').unwrap_or((line, ""));
        let reply: String = match verb {
            "ping" => "pong".to_string(),
            "type" => act(vi.type_str(rest)),
            "enter" => act(vi.enter()),
            "key" => match rest.trim().parse::<u16>() {
                Ok(code) => act(vi.key(code)),
                Err(_) => "err key <code:u16>".to_string(),
            },
            "click" => {
                let b = match rest.trim() {
                    "r" | "right" => BTN_RIGHT,
                    "m" | "middle" => BTN_MIDDLE,
                    _ => BTN_LEFT,
                };
                act(vi.click(b))
            }
            "move" => {
                let mut it = rest.split_whitespace();
                let dx = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
                let dy = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
                act(vi.move_rel(dx, dy))
            }
            "scroll" => {
                let n = rest.trim().parse().unwrap_or(0);
                act(vi.scroll(n))
            }
            other => format!("err unknown verb {other:?}"),
        };
        writeln!(out, "{reply}")?;
        out.flush()?;
    }
    Ok(())
}

fn act(r: std::io::Result<()>) -> String {
    match r {
        Ok(()) => "ok".to_string(),
        Err(e) => format!("err {e}"),
    }
}
