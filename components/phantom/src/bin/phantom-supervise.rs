use phantom::seccomp::{
    self, install_notify_filter, notif_id_valid, notif_recv, notif_send, SeccompNotifResp,
};
use phantom::i18n::l;
use phantom::sys::{recv_with_fds, send_with_fds, vm_write};

use std::ffi::CString;
use std::io::Write;
use std::os::raw::c_int;

type Pid = i32;

extern "C" {
    fn fork() -> Pid;
    fn execvp(file: *const i8, argv: *const *const i8) -> c_int;
    fn waitpid(pid: Pid, status: *mut c_int, options: c_int) -> Pid;
    fn socketpair(domain: c_int, ty: c_int, protocol: c_int, sv: *mut c_int) -> c_int;
    fn close(fd: c_int) -> c_int;
    fn _exit(code: c_int) -> !;
}

const AF_UNIX: c_int = 1;
const SOCK_STREAM: c_int = 1;

fn main() {
    let argv: Vec<String> = std::env::args().collect();

    let mut traps_names: Vec<String> = vec!["openat".into()];

    let mut inject: Option<(i32, Vec<u8>)> = None;

    let mut adopt_pid: Option<i32> = None;

    let mut restart = false;
    let mut restart_max: Option<u32> = None;
    let mut i = 1;
    while i < argv.len() {
        match argv[i].as_str() {
            "--trap" => {
                i += 1;
                if let Some(list) = argv.get(i) {
                    traps_names = list.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
                }
            }
            "--inject-read" => {
                i += 1;
                match argv.get(i).and_then(|s| s.split_once('=')) {
                    Some((fd_s, text)) => match fd_s.trim().parse::<i32>() {
                        Ok(fd) => inject = Some((fd, text.replace("\\n", "\n").into_bytes())),
                        Err(_) => {
                            eprintln!("phantom-supervise: --inject-read wants FD=TEXT (FD must be a number)");
                            std::process::exit(2);
                        }
                    },
                    None => {
                        eprintln!("phantom-supervise: --inject-read wants FD=TEXT, e.g. 0=hello");
                        std::process::exit(2);
                    }
                }
            }
            "--adopt" => {
                i += 1;
                match argv.get(i).and_then(|s| s.parse::<i32>().ok()) {
                    Some(p) => adopt_pid = Some(p),
                    None => {
                        eprintln!("phantom-supervise: --adopt wants a numeric PID");
                        std::process::exit(2);
                    }
                }
            }
            "--restart" => restart = true,
            "--restart-max" => {
                i += 1;
                match argv.get(i).and_then(|s| s.parse::<u32>().ok()) {
                    Some(n) => {
                        restart_max = Some(n);
                        restart = true;
                    }
                    None => {
                        eprintln!("phantom-supervise: --restart-max wants a number");
                        std::process::exit(2);
                    }
                }
            }
            "-h" | "--help" => {
                usage();
                return;
            }
            "--" => {
                i += 1;
                break;
            }
            s if s.starts_with('-') => {
                eprintln!("phantom-supervise: unknown flag {s}");
                usage();
                std::process::exit(2);
            }
            _ => break,
        }
        i += 1;
    }

    let mut cmd: Vec<String> = argv[i..].to_vec();

    if let Some(pid) = adopt_pid {
        match adopt(pid) {
            Ok((aargv, cwd, env)) => {
                cmd = aargv;
                for (k, v) in env {
                    std::env::set_var(k, v);
                }
                if let Some(d) = cwd {
                    let _ = std::env::set_current_dir(&d);
                }
                eprintln!(
                    "phantom-supervise: adopted pid {pid} → relaunching under supervision: {}",
                    cmd.join(" ")
                );
            }
            Err(e) => {
                eprintln!("phantom-supervise: --adopt {pid}: {e}");
                std::process::exit(1);
            }
        }
    }

    if cmd.is_empty() {
        usage();
        std::process::exit(2);
    }

    if inject.is_some() && !traps_names.iter().any(|n| n == "read") {
        traps_names.push("read".into());
    }

    let mut traps: Vec<u32> = Vec::new();
    for n in &traps_names {
        match seccomp::syscall_nr(n) {
            Some(nr) => traps.push(nr),
            None => {
                eprintln!("phantom-supervise: unknown syscall name {n:?} (known set is small; extend seccomp::syscall_nr)");
                std::process::exit(2);
            }
        }
    }

    let mut attempt: u32 = 0;
    loop {
        attempt += 1;
        let code = launch_and_supervise(&cmd, &traps, &traps_names, inject.clone());
        if !restart {
            std::process::exit(code);
        }
        if let Some(max) = restart_max {
            if attempt >= max {
                eprintln!("phantom-supervise: reached --restart-max {max} (last exit {code})");
                std::process::exit(code);
            }
        }
        eprintln!(
            "phantom-supervise: {:?} exited ({code}) — restarting (#{attempt}{})",
            cmd[0],
            restart_max.map(|m| format!("/{m}")).unwrap_or_default(),
        );
        std::thread::sleep(std::time::Duration::from_millis(400));
    }
}

fn launch_and_supervise(
    cmd: &[String],
    traps: &[u32],
    traps_names: &[String],
    inject: Option<(i32, Vec<u8>)>,
) -> i32 {

    let cstrs: Vec<CString> = cmd
        .iter()
        .map(|s| CString::new(s.as_str()).expect("argv has NUL"))
        .collect();
    let mut cargv: Vec<*const i8> = cstrs.iter().map(|c| c.as_ptr()).collect();
    cargv.push(std::ptr::null());

    let mut sv = [0 as c_int; 2];
    if unsafe { socketpair(AF_UNIX, SOCK_STREAM, 0, sv.as_mut_ptr()) } != 0 {
        eprintln!("phantom-supervise: socketpair: {}", std::io::Error::last_os_error());
        return 1;
    }
    let (sock_parent, sock_child) = (sv[0], sv[1]);

    let pid = unsafe { fork() };
    if pid < 0 {
        eprintln!("phantom-supervise: fork: {}", std::io::Error::last_os_error());
        return 1;
    }

    if pid == 0 {

        unsafe { close(sock_parent) };
        let listener = match install_notify_filter(traps) {
            Ok(fd) => fd,
            Err(e) => {
                eprintln!("phantom-supervise(child): seccomp install failed: {e}");
                unsafe { _exit(126) };
            }
        };

        if let Err(e) = send_with_fds(sock_child, b"L", &[listener]) {
            eprintln!("phantom-supervise(child): send listener fd: {e}");
            unsafe { _exit(126) };
        }

        unsafe {
            close(listener);
            close(sock_child);

            execvp(cargv[0], cargv.as_ptr());

            let e = std::io::Error::last_os_error();
            eprintln!("phantom-supervise(child): exec {:?}: {e}", cmd[0]);
            _exit(127);
        }
    }

    unsafe { close(sock_child) };
    let listener = match recv_listener(sock_parent) {
        Ok(fd) => fd,
        Err(e) => {
            eprintln!("phantom-supervise: did not receive listener fd: {e}");

            let mut st: c_int = 0;
            unsafe { waitpid(pid, &mut st, 0) };
            return 1;
        }
    };
    unsafe { close(sock_parent) };

    let mode = match &inject {
        Some((fd, b)) => format!("INJECT-READ fd={fd} ({} bytes)", b.len()),
        None => "allow-only (observe)".into(),
    };
    eprintln!(
        "phantom-supervise: supervising pid {pid} ({}) — trapping [{}] — {mode}",
        cmd.join(" "),
        traps_names.join(", "),
    );

    supervise(listener, pid, cmd, inject)
}

fn adopt(pid: i32) -> Result<(Vec<String>, Option<String>, Vec<(String, String)>), String> {
    let cmdline = std::fs::read(format!("/proc/{pid}/cmdline"))
        .map_err(|e| format!("read /proc/{pid}/cmdline: {e}"))?;
    let argv: Vec<String> = cmdline
        .split(|&b| b == 0)
        .filter(|s| !s.is_empty())
        .map(|s| String::from_utf8_lossy(s).into_owned())
        .collect();
    if argv.is_empty() {
        return Err("process has an empty cmdline (kernel thread or zombie?)".into());
    }
    let cwd = std::fs::read_link(format!("/proc/{pid}/cwd"))
        .ok()
        .and_then(|p| p.to_str().map(String::from));

    let env: Vec<(String, String)> = std::fs::read(format!("/proc/{pid}/environ"))
        .map(|raw| {
            raw.split(|&b| b == 0)
                .filter(|s| !s.is_empty())
                .filter_map(|s| {
                    let t = String::from_utf8_lossy(s);
                    t.split_once('=').map(|(k, v)| (k.to_string(), v.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    terminate(pid)?;
    Ok((argv, cwd, env))
}

fn terminate(pid: i32) -> Result<(), String> {
    extern "C" {
        fn kill(pid: i32, sig: c_int) -> c_int;
    }
    let gone = || !std::path::Path::new(&format!("/proc/{pid}")).exists();
    unsafe { kill(pid, 15) };
    for _ in 0..30 {
        if gone() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    unsafe { kill(pid, 9) };
    for _ in 0..20 {
        if gone() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    Err("process did not exit after SIGTERM + SIGKILL".into())
}

fn recv_listener(sock: c_int) -> std::io::Result<c_int> {
    let mut buf = [0u8; 8];
    let mut fds: Vec<c_int> = Vec::new();
    let n = recv_with_fds(sock, &mut buf, &mut fds)?;
    if n == 0 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::UnexpectedEof,
            "child closed without sending the listener fd (seccomp install likely failed)",
        ));
    }
    fds.into_iter().next().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidData, "no fd in the child's message")
    })
}

fn supervise(listener: c_int, child: Pid, cmd: &[String], inject: Option<(i32, Vec<u8>)>) -> i32 {
    let stderr = std::io::stderr();
    let read_nr = seccomp::syscall_nr("read").map(|n| n as i32).unwrap_or(-1);
    let mut injected = false;
    loop {
        match notif_recv(listener) {
            Ok(notif) => {

                let d = &notif.data;
                let mut e = stderr.lock();
                let _ = writeln!(
                    e,
                    "phantom-supervise: TRAP pid={} {}(nr={}) args=[{:#x}, {:#x}, {:#x}, {:#x}, {:#x}, {:#x}]",
                    notif.pid,
                    seccomp::syscall_name(d.nr),
                    d.nr,
                    d.args[0], d.args[1], d.args[2], d.args[3], d.args[4], d.args[5],
                );
                drop(e);

                let mut handled = false;
                if let Some((fd, bytes)) = inject.as_ref() {
                    if !injected && d.nr == read_nr && d.args[0] as i32 == *fd {
                        let buf_addr = d.args[1];
                        let n = bytes.len().min(d.args[2] as usize);

                        if notif_id_valid(listener, notif.id) {
                            if let Ok(w) = vm_write(notif.pid as i32, buf_addr, &bytes[..n]) {
                                let resp = SeccompNotifResp::fake(notif.id, w as i64);
                                let _ = notif_send(listener, &resp);
                                injected = true;
                                handled = true;
                                let _ = writeln!(
                                    stderr.lock(),
                                    "phantom-supervise: INJECTED {w} byte(s) into pid={} read(fd={fd})",
                                    notif.pid,
                                );
                            }
                        }
                    }
                }

                if !handled {
                    let resp = SeccompNotifResp::allow(notif.id);
                    if let Err(err) = notif_send(listener, &resp) {

                        if err.raw_os_error() != Some(2) {
                            eprintln!("phantom-supervise: NOTIF_SEND failed: {err}");
                            break;
                        }
                    }
                }
            }
            Err(e) => {

                if e.raw_os_error() != Some(2) {

                    eprintln!("phantom-supervise: listener closed ({e})");
                }
                break;
            }
        }
    }
    unsafe { close(listener) };

    let mut status: c_int = 0;
    let r = unsafe { waitpid(child, &mut status, 0) };
    if r > 0 {
        if status & 0x7f == 0 {
            let code = (status >> 8) & 0xff;
            eprintln!("phantom-supervise: {:?} exited ({code})", cmd[0]);
            return code;
        } else {
            let sig = status & 0x7f;
            eprintln!("phantom-supervise: {:?} killed by signal {sig}", cmd[0]);
            return 128 + sig;
        }
    }
    0
}

fn usage() {
    eprintln!("phantom-supervise — {}", l(
        "startet ein Programm unter einem seccomp-User-Notification-Supervisor.",
        "launch a program under a seccomp user-notification supervisor.",
    ));
    eprintln!(
        "\
         \n\
         usage: phantom-supervise [opts] <program> [args...]\n\
                phantom-supervise --adopt <pid> [opts]\n\
         \n\
         --trap LIST          comma-separated syscall names to intercept (default: openat)\n\
                              known: read write open close openat connect execve execveat\n\
                                     ptrace socket mmap kill\n\
         --inject-read FD=TEXT  forge the FIRST read(FD,…) of the launched program: write\n\
                              TEXT into its buffer and return its length, kernel-mediated,\n\
                              with no cooperation from the program (\\n → newline).\n\
         --adopt PID          take over an ALREADY-RUNNING process: capture its argv/cwd/env\n\
                              from /proc, terminate it, and relaunch it under supervision.\n\
                              (A controlled restart — the kernel can't filter a process that\n\
                              didn't filter itself. Don't adopt a systemd service.)\n\
         --restart            keep the target alive — relaunch it whenever it exits.\n\
         --restart-max N      relaunch at most N times, then exit with its last code.\n\
         \n\
         demo (no root needed):\n\
           phantom-supervise /bin/cat /etc/hostname              # logs the openat(s), then prints the file\n\
           phantom-supervise --trap openat,connect curl …        # observe opens + connects of any launched tree\n\
           phantom-supervise --inject-read 0=hi -- /bin/cat </dev/null   # cat prints 'hi' it never read\n\
         \n\
         Without --inject-read the supervisor ALLOWS every trapped syscall (observe-only).\n\
         With it, the chosen read is forged in-path — block (-EPERM), fake, or addfd are\n\
         the other actuations the same loop supports.\n\
         Supervises only programs phantom LAUNCHES (a filter is installed by the task\n\
         itself; an already-running pid cannot be retrofitted)."
    );
    eprintln!("\n{}", l(
        "Sprache: PHANTOM_LANG=en für englische Meldungen (Standard: Deutsch).",
        "Language: PHANTOM_LANG=en for English messages (default: German).",
    ));
}
