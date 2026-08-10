use phantom::input::{VirtualInput, BTN_LEFT, BTN_MIDDLE, BTN_RIGHT};
use std::time::Duration;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("");
    if cmd.is_empty() {
        eprintln!("usage: inject <type|enter|key|click|move|scroll> ...");
        std::process::exit(2);
    }

    let mut vi = match VirtualInput::new() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("inject: cannot open /dev/uinput: {e}");
            eprintln!("        run as root, or install an /dev/uinput udev rule for the 'input' group.");
            std::process::exit(1);
        }
    };

    std::thread::sleep(Duration::from_millis(500));

    let r = match cmd {
        "type" => vi.type_str(&args[2..].join(" ")),
        "enter" => vi.enter(),
        "key" => match args.get(2).and_then(|s| s.parse::<u16>().ok()) {
            Some(code) => vi.key(code),
            None => {
                eprintln!("inject key <code>");
                std::process::exit(2);
            }
        },
        "click" => {
            let b = match args.get(2).map(|s| s.as_str()) {
                Some("r") => BTN_RIGHT,
                Some("m") => BTN_MIDDLE,
                _ => BTN_LEFT,
            };
            vi.click(b)
        }
        "move" => {
            let dx = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(0);
            let dy = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(0);
            vi.move_rel(dx, dy)
        }
        "scroll" => vi.scroll(args.get(2).and_then(|s| s.parse().ok()).unwrap_or(0)),
        other => {
            eprintln!("inject: unknown command {other:?}");
            std::process::exit(2);
        }
    };

    if let Err(e) = r {
        eprintln!("inject: {e}");
        std::process::exit(1);
    }
    eprintln!("inject: ok ({cmd})");
}
