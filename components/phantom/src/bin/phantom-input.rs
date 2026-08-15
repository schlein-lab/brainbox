use phantom::input_tap::{spawn_all, Key, KeyEvent};

use std::io::Write;

fn main() {
    let grab = std::env::args().any(|a| a == "--grab");

    let handles = spawn_all(grab, |ev: &KeyEvent| {

        let out = std::io::stdout();
        let mut lock = out.lock();
        match ev.key {
            Key::Char(c) => {
                let _ = write!(lock, "{c}");
            }
            Key::Backspace => {
                let _ = write!(lock, "⌫");
            }
            Key::Enter => {
                let _ = writeln!(lock, "  ⏎ [{}]", ev.device);
            }
        }
        let _ = lock.flush();
    });

    if handles.is_empty() {
        eprintln!("phantom-input: no readable /dev/input/event* — run as root or join the `input` group");
        std::process::exit(1);
    }
    eprintln!(
        "phantom-input: tapping {} input device(s){}  (Ctrl-C to stop)",
        handles.len(),
        if grab { "  [GRAB / man-in-the-middle]" } else { "" }
    );
    for h in handles {
        let _ = h.join();
    }
}
