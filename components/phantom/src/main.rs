use phantom::i18n::l;
use phantom::input_tap::{self, Key, KeyEvent};
use phantom::kernel::{self, KernelEvent};
use phantom::sys;

use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};
use std::thread;

mod compositor;
mod server;
mod xwayland;

mod act;
mod control;
mod proxy;
mod scene_frame;
mod rfb_input;
mod state;
mod winbus;
mod winreg;
mod xwl;

use control::control_server;
use proxy::proxy_client;
use state::GATE;

pub(crate) use act::uid_to_name;
pub(crate) use state::{BufferInfo, ClientState, Shared, CLIENT_SEQ};
pub(crate) use xwl::{xwl_associate, xwl_parent_gone, xwl_surface_gone};


const CONTROL_VERBS: &[&str] = &[
    "list", "clients", "act", "sense", "kbd", "focus", "popup-zu", "snapshot", "inject", "inject-title",
    "keys", "dock", "room", "launch", "spawn", "spielwiese", "foreground", "fg", "scene",
    "screen", "cap", "cap-status", "xwin", "record", "await", "verbs", "delta", "xmeta",
    "zoom", "som", "notiz",
];

fn print_help() {
    let intro = l(
        "phantom — steuere jedes Linux-Programm über seine eigene I/O (Wayland / Syscalls / uinput).",
        "phantom — drive any Linux program through its own I/O (Wayland / syscalls / uinput).",
    );
    let run = l("So startest du phantom:", "Run phantom:");
    let c_proxy = l("Proxy: zwischen App und echtem Compositor", "proxy: sit between apps and the real compositor");
    let c_headless = l("phantom IST das Display (kein Compositor, kein Bildschirm)", "phantom IS the display (no compositor, no screen)");
    let c_compositor = l("phantom IST das Display UND besitzt Bildschirm+Eingabe", "phantom IS the display AND owns the real screen + input");
    let c_xwayland = l("X11-Apps durch phantom (rootless = pro Fenster ein Ziel)", "run X11 apps through phantom (rootless = one target per window)");
    let c_watch = l("Sensorik: Syscall-Tap + Tastatur-Tap", "sense layer: syscall tap + keystroke tap");
    let c_cap = l("Capability-Policy des Steuer-Sockets zeigen / Token lesen", "show the control-socket capability policy / reveal the token");
    let c_help = l("diese Hilfe", "this help");
    let drive = l(
        "Eine laufende App steuern (verbindet sich mit dem Hub; `phantomctl` tut dasselbe):",
        "Drive a running app (connects to the hub; `phantomctl` does the same):",
    );
    let c_list = l("Ziele auflisten (cid, title, app_id, ready)", "list targets (cid, title, app_id, ready)");
    let c_kbd = l("systemweit über uinput", "system-wide via uinput");
    let foot_lang = l(
        "Sprache: PHANTOM_LANG=en für Englisch (Standard: Deutsch).",
        "Language: PHANTOM_LANG=en for English (default: German).",
    );
    let foot_cap = "Capability: PHANTOM_CAP=cold|same-uid|off.";
    let foot_alpha = l(
        "Alpha — auf einer Wegwerf-VM ausführen. https://phantomlinux.com",
        "Alpha — run on a disposable VM. https://phantomlinux.com",
    );
    println!(
        "{intro}\n\n\
         {run}\n\
         \x20 phantom [listen-name]              {c_proxy}\n\
         \x20 phantom --headless [name]          {c_headless}\n\
         \x20 phantom --compositor [name]        {c_compositor}\n\
         \x20 phantom xwayland [--rootless] [n]  {c_xwayland}\n\
         \x20 phantom watch                      {c_watch}\n\
         \x20 phantom cap [token]                {c_cap}\n\
         \x20 phantom help                       {c_help}\n\n\
         {drive}\n\
         \x20 phantom list                       {c_list}\n\
         \x20 phantom act <cid|@title> type <s>|enter|key <code>|click <x> <y>|move <x> <y>|scroll <x> <y> <n>\n\
         \x20 phantom sense <cid|@title> shot [path]|text|intent\n\
         \x20 phantom kbd type <s>|enter|key <code>   {c_kbd}\n\n\
         {foot_lang} {foot_cap}\n{foot_alpha}"
    );
}

fn main() {

    if std::env::args().nth(1).as_deref() == Some("watch") {
        watch();
        return;
    }

    if std::env::args().nth(1).as_deref() == Some("--sd-notify-test") {
        sys::sd_notify("READY=1");
        eprintln!("sd-notify-test: NOTIFY_SOCKET={:?}", std::env::var("NOTIFY_SOCKET"));
        thread::sleep(std::time::Duration::from_secs(20));
        sys::sd_notify("STOPPING=1");
        return;
    }

    if matches!(std::env::args().nth(1).as_deref(), Some("help") | Some("--help") | Some("-h")) {
        print_help();
        return;
    }

    let runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/1000".into());

    if let Some(verb) = std::env::args().nth(1) {
        if CONTROL_VERBS.contains(&verb.as_str()) {
            let line = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
            let ctl = std::env::var("PHANTOM_CTL").unwrap_or_else(|_| format!("{runtime}/phantom.ctl"));
            match UnixStream::connect(&ctl) {
                Ok(mut s) => {
                    let _ = s.write_all(line.as_bytes());
                    let _ = s.write_all(b"\n");
                    let _ = s.shutdown(std::net::Shutdown::Write);
                    let mut reply = String::new();
                    let _ = s.read_to_string(&mut reply);
                    print!("{reply}");
                }
                Err(e) => {
                    eprintln!("{} {ctl}: {e}", l("phantom: Hub nicht erreichbar an", "phantom: cannot reach the hub at"));
                    std::process::exit(1);
                }
            }
            return;
        }
    }

    if std::env::args().nth(1).as_deref() == Some("cap") {
        let g = phantom::cap::Gate::init(&runtime);
        println!("{}", g.status());
        if std::env::args().nth(2).as_deref() == Some("token") {
            print!("{}", std::fs::read_to_string(g.token_path()).unwrap_or_default());
        } else {
            println!("token: in {} (reveal with: phantom cap token)", g.token_path());
        }
        return;
    }

    if std::env::args().nth(1).as_deref() == Some("xwayland") {
        let xargs: Vec<String> = std::env::args().skip(2).collect();
        let rootless = xargs.iter().any(|a| a == "--rootless");
        let wl = xargs
            .iter()
            .find(|a| !a.starts_with("--"))
            .cloned()
            .unwrap_or_else(|| "phantom-0".into());
        let res = if rootless {
            xwayland::spawn_rootless(&wl, &runtime)
        } else {
            xwayland::spawn(&wl, &runtime)
        };
        match res {
            Ok(mut x) => {
                println!("DISPLAY=:{}", x.display);
                let _ = std::io::stdout().flush();
                eprintln!(
                    "phantom: Xwayland :{} (pid {}) {} on WAYLAND_DISPLAY={wl}. \
                     Run X11 apps with  DISPLAY=:{}  — Ctrl-C to stop.",
                    x.display,
                    x.child.id(),
                    if x.rootless { "rootless + phantom-xwm (per-window map + X focus)" } else { "rootful + focus WM (see + type X11 apps)" },
                    x.display,
                );
                let _ = x.child.wait();
            }
            Err(e) => {
                eprintln!("phantom: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    let _ = GATE.set(phantom::cap::Gate::init(&runtime));

    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    let headless = argv.first().map(|s| s == "--headless").unwrap_or(false);
    if headless {
        argv.remove(0);
    }

    let compositor = argv.first().map(|s| s == "--compositor").unwrap_or(false);
    if compositor {
        argv.remove(0);
    }
    let listen_name = argv.first().cloned().unwrap_or_else(|| "phantom-0".into());

    let resolve = |n: &str| if n.starts_with('/') { n.to_string() } else { format!("{runtime}/{n}") };
    let listen_path = resolve(&listen_name);
    
    
    let ctl_path = std::env::var("PHANTOM_CTL").unwrap_or_else(|_| format!("{runtime}/phantom.ctl"));

    let shared: Shared = Arc::new(Mutex::new(HashMap::new()));

    {
        let shared = shared.clone();
        let ctl_path = ctl_path.clone();
        thread::spawn(move || control_server(&ctl_path, shared));
    }

    
    
    
    match std::env::var("PHANTOM_WINEVENTS").ok().as_deref() {
        Some("0") => {}
        Some(p) if !p.is_empty() => winbus::starte(&resolve(p)),
        _ => winbus::starte(&format!("{runtime}/phantom.events")),
    }

    let run_app = l("App starten mit", "run an app with");
    if compositor {
        eprintln!("phantom: compositor {listen_path}  {}", l("(phantom IST das Display + besitzt Bildschirm/Eingabe)", "(phantom IS the display + owns the screen/input)"));
        eprintln!("phantom: control {ctl_path}");
        eprintln!("phantom: {run_app}:  WAYLAND_DISPLAY={listen_name} <app>");
        compositor::run(&listen_path, shared);
        return;
    }

    if headless {
        eprintln!("phantom: stand 2026-08-14b (shm-pool-raeumung)");
        eprintln!("phantom: headless {listen_path}  {}", l("(kein Upstream — phantom IST das Display)", "(no upstream — phantom IS the display)"));
        eprintln!("phantom: control {ctl_path}");
        eprintln!("phantom: {run_app}:  WAYLAND_DISPLAY={listen_name} <app>");
        server::run(&listen_path, shared);
        return;
    }

    let upstream_name = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".into());
    let upstream_path = resolve(&upstream_name);
    if listen_path == upstream_path {
        eprintln!("phantom: {}", l("weigere mich, auf dem Upstream-Socket selbst zu lauschen", "refusing to listen on the upstream socket itself"));
        std::process::exit(1);
    }

    
    
    
    
    if !listen_name.contains('/') && !listen_name.starts_with("wayland") {
        eprintln!(
            "phantom: '{listen_name}' {}",
            l(
                "ist KEIN verb — starte PROXY-anzeigeserver dieses namens. verbs: `phantomctl verbs`; abbruch: Strg-C.",
                "is NOT a verb — starting a PROXY display server of that name. verbs: `phantomctl verbs`; abort: Ctrl-C."
            )
        );
    }
    let _ = std::fs::remove_file(&listen_path);
    let listener = match UnixListener::bind(&listen_path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom: {} {listen_path}: {e}", l("kann nicht binden", "cannot bind"));
            std::process::exit(1);
        }
    };
    eprintln!("phantom: proxy   {listen_path}  ->  {upstream_path}");
    eprintln!("phantom: control {ctl_path}");
    eprintln!("phantom: {} {listen_name} <app>", l("App durch phantom starten mit:  WAYLAND_DISPLAY=", "run an app through it with:  WAYLAND_DISPLAY="));

    for client in listener.incoming() {
        let Ok(client) = client else { continue };
        let shared = shared.clone();
        let up = upstream_path.clone();
        thread::spawn(move || {
            let cid = CLIENT_SEQ.fetch_add(1, Ordering::Relaxed);
            if let Err(e) = proxy_client(client, &up, cid, shared.clone()) {
                eprintln!("[c{cid}] {e}");
            }
            shared.lock().unwrap().remove(&cid);
            crate::compositor::focus_override_tot(cid);
            eprintln!("[c{cid}] closed");
        });
    }
}

fn watch() {
    let args: Vec<String> = std::env::args().skip(2).collect();
    let mut opts = kernel::Opts::default();
    let mut json = false;
    let mut grab = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--files" => opts.files = true,
            "--io" => opts.io = true,
            "--grab" => grab = true,
            "--json" => json = true,
            "--pid" => {
                i += 1;
                opts.only_pid = args.get(i).and_then(|s| s.parse().ok());
            }
            "--syscall" => {
                i += 1;
                if let Some(s) = args.get(i) {
                    opts.syscalls.push(s.clone());
                }
            }
            other => eprintln!("phantom watch: ignoring unknown argument {other}"),
        }
        i += 1;
    }

    let kb_handles = input_tap::spawn_all(grab, move |ev: &KeyEvent| {

        let out = std::io::stdout();
        let mut lock = out.lock();
        let what = match ev.key {
            Key::Char(c) => c.to_string(),
            Key::Backspace => "\u{8}bksp".into(),
            Key::Enter => "\u{21b5}enter".into(),
        };
        if json {
            let _ = writeln!(
                lock,
                "{{\"src\":\"kbd\",\"device\":{:?},\"key\":{:?}}}",
                ev.device, what
            );
        } else {
            let _ = writeln!(lock, "KBD   {:>16} {what}", ev.device);
        }
        let _ = lock.flush();
    });
    if kb_handles.is_empty() {
        eprintln!("phantom watch: input devices not readable — kernel tap only (root or `input` group enables keystrokes)");
    } else {
        eprintln!(
            "phantom watch: input tap on {} device(s){}",
            kb_handles.len(),
            if grab { "  [GRAB]" } else { "" }
        );
    }

    let out = std::io::stdout();
    let res = kernel::run(
        &opts,
        |line| eprintln!("{line}"),
        |ev: &KernelEvent| {
            let mut lock = out.lock();
            let ok = if json {
                writeln!(
                    lock,
                    "{{\"src\":\"krn\",\"pid\":{},\"comm\":{:?},\"verb\":{:?},\"event\":{:?},\"info\":{:?}}}",
                    ev.pid, ev.comm, ev.verb, ev.event, ev.info
                )
            } else {
                writeln!(lock, "KRN   {:<5} {:>7} {:<16} {}", ev.verb, ev.pid, ev.comm, ev.info)
            };
            ok.and_then(|_| lock.flush()).is_ok()
        },
    );
    if let Err(e) = res {
        eprintln!("phantom watch: kernel tap: {e}");
        std::process::exit(1);
    }

}
