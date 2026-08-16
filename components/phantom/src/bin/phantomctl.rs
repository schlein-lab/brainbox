use std::io::{Read, Write};
use std::os::unix::net::UnixStream;

fn main() {
    let runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/1000".into());
    let path = std::env::var("PHANTOM_CTL").unwrap_or_else(|_| format!("{runtime}/phantom.ctl"));

    let mut cmd = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
    
    
    
    if let Ok(sitz) = std::env::var("PHANTOM_SITZ") {
        let sitz = sitz.trim().to_string();
        if !sitz.is_empty() && !cmd.contains("sitz:") {
            cmd = match cmd.strip_prefix("tok:") {
                Some(rest) => match rest.split_once(' ') {
                    Some((t, rest2)) => format!("tok:{t} sitz:{sitz} {rest2}"),
                    None => cmd,
                },
                None => format!("sitz:{sitz} {cmd}"),
            };
        }
    }
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
