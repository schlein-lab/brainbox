use std::io::{BufRead, BufReader};
use std::os::unix::net::UnixListener;

fn main() {
    let mut args = std::env::args().skip(1);
    let path = args.next().unwrap_or_else(|| "/run/phantom/preload.sock".to_string());
    let once = std::env::args().any(|a| a == "--once");

    let _ = std::fs::remove_file(&path);
    let listener = match UnixListener::bind(&path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("stub_bus: cannot bind {path}: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("stub_bus: listening on {path}");

    for conn in listener.incoming() {
        let stream = match conn {
            Ok(s) => s,
            Err(_) => continue,
        };
        let reader = BufReader::new(stream);
        for line in reader.lines().map_while(Result::ok) {
            println!("{line}");
        }
        if once {
            break;
        }
    }
}
