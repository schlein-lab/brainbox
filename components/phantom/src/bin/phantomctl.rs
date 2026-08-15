use std::io::{Read, Write};
use std::os::unix::net::UnixStream;

fn main() {
    let runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/1000".into());
    let path = std::env::var("PHANTOM_CTL").unwrap_or_else(|_| format!("{runtime}/phantom.ctl"));

    let cmd = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
    if cmd.is_empty() {
        eprintln!("usage: phantomctl <clients | inject <cid> text <s> | inject <cid> enter | inject <cid> key <code>>");
        std::process::exit(2);
    }

    let mut s = match UnixStream::connect(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("phantomctl: cannot reach phantom hub at {path}: {e}");
            std::process::exit(1);
        }
    };
    let _ = s.write_all(cmd.as_bytes());
    let _ = s.write_all(b"\n");
    let _ = s.shutdown(std::net::Shutdown::Write);

    let mut reply = String::new();
    let _ = s.read_to_string(&mut reply);
    print!("{reply}");
}
