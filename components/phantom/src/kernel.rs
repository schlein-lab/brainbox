use crate::sys::{install_stop_pipe, mount_tracefs, poll_readable};

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};

const INSTANCE: &str = "phantom";
const KPROBE_OPEN: &str = "phantom_open";

#[derive(Clone, Default)]
pub struct Opts {
    pub files: bool,
    pub io: bool,
    pub only_pid: Option<i32>,
    pub syscalls: Vec<String>,
}

pub struct KernelEvent<'a> {
    pub pid: i32,
    pub comm: &'a str,

    pub verb: &'static str,

    pub event: &'a str,

    pub info: &'a str,
}

pub fn classify(event: &str) -> &'static str {
    match event {
        "sched_process_exec" => "EXEC",
        "sched_process_fork" => "FORK",
        "sched_process_exit" => "EXIT",
        KPROBE_OPEN => "OPEN",
        e if e.starts_with("sys_enter_") => "SYS",
        e if e.starts_with("sys_exit_") => "RET",
        _ => "EVT",
    }
}

fn write_file(path: &Path, val: &str) -> io::Result<()> {
    let mut f = OpenOptions::new().write(true).open(path)?;
    f.write_all(val.as_bytes())
}

fn append_file(path: &Path, val: &str) -> io::Result<()> {
    let mut f = OpenOptions::new().append(true).open(path)?;
    f.write_all(val.as_bytes())
}

fn locate_tracefs() -> io::Result<PathBuf> {
    for p in ["/sys/kernel/tracing", "/sys/kernel/debug/tracing"] {
        if Path::new(p).join("trace_pipe").exists() {
            return Ok(PathBuf::from(p));
        }
    }
    let target = "/sys/kernel/tracing";
    mount_tracefs(target).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("no tracefs and could not mount one at {target} ({e}); run as root"),
        )
    })?;
    if Path::new(target).join("trace_pipe").exists() {
        return Ok(PathBuf::from(target));
    }
    Err(io::Error::new(io::ErrorKind::NotFound, "tracefs unavailable after mount"))
}

struct Tap {
    root: PathBuf,
    inst: PathBuf,
    enabled: Vec<PathBuf>,
    kprobe: bool,
}

impl Tap {
    fn enable(&mut self, group: &str, name: &str) -> bool {
        let f = self.inst.join("events").join(group).join(name).join("enable");
        match write_file(&f, "1") {
            Ok(()) => {
                self.enabled.push(f);
                true
            }
            Err(_) => false,
        }
    }
}

impl Drop for Tap {
    fn drop(&mut self) {
        for f in &self.enabled {
            let _ = write_file(f, "0");
        }
        if self.kprobe {
            let _ = append_file(&self.root.join("kprobe_events"), &format!("-:{KPROBE_OPEN}\n"));
        }

        let _ = fs::remove_dir(&self.inst);
    }
}

fn parse_line(line: &str) -> Option<(&str, i32, &str, &str)> {
    let line = line.trim_end();
    if line.is_empty() || line.starts_with('#') {
        return None;
    }

    let p1 = line.find(": ")?;
    let rest = &line[p1 + 2..];
    let p2 = rest.find(": ")?;
    let event = &rest[..p2];
    let info = rest[p2 + 2..].trim();

    let tok = line[..p1].trim().split_whitespace().next().unwrap_or("");
    let (comm, pid) = match tok.rsplit_once('-') {
        Some((c, p)) => (c, p.parse::<i32>().unwrap_or(-1)),
        None => (tok, -1),
    };
    Some((comm, pid, event, info))
}

pub fn run(
    opts: &Opts,
    mut log: impl FnMut(&str),
    mut on_event: impl FnMut(&KernelEvent) -> bool,
) -> io::Result<()> {
    let root = locate_tracefs()?;
    let inst = root.join("instances").join(INSTANCE);

    let _ = fs::remove_dir(&inst);
    fs::create_dir(&inst).map_err(|e| {
        io::Error::new(e.kind(), format!("cannot create trace instance {} ({e}); run as root", inst.display()))
    })?;

    let mut tap = Tap { root: root.clone(), inst: inst.clone(), enabled: Vec::new(), kprobe: false };

    let mut on: Vec<&str> = Vec::new();
    for ev in ["sched_process_exec", "sched_process_fork", "sched_process_exit"] {
        if tap.enable("sched", ev) {
            on.push(ev);
        }
    }

    if opts.files {
        let kp = root.join("kprobe_events");
        let _ = append_file(&kp, &format!("-:{KPROBE_OPEN}\n"));
        let def = format!("p:{KPROBE_OPEN} do_sys_openat2 path=+0(%si):string\n");
        match append_file(&kp, &def) {
            Ok(()) => {
                tap.kprobe = true;
                if tap.enable("kprobes", KPROBE_OPEN) {
                    on.push("file-open");
                } else {
                    log("phantom-kernel: kprobe defined but could not be enabled");
                }
            }
            Err(e) => log(&format!(
                "phantom-kernel: file tap unavailable on this kernel ({e}) — continuing without files"
            )),
        }
    }

    if opts.io {
        for sc in ["sys_enter_write", "sys_enter_read", "sys_enter_connect"] {
            if tap.enable("syscalls", sc) {
                on.push("io");
            }
        }
    }

    let owned: Vec<String> = opts.syscalls.iter().map(|s| format!("sys_enter_{s}")).collect();
    for sc in &owned {
        if tap.enable("syscalls", sc) {
            on.push("syscall");
        } else {
            log(&format!("phantom-kernel: no such syscall tracepoint: {sc}"));
        }
    }

    if on.is_empty() {
        return Err(io::Error::new(io::ErrorKind::Other, "no tracepoints could be enabled"));
    }

    log(&format!(
        "phantom-kernel: tapping the kernel ({} active){}  — every process on this machine",
        on.len(),
        if let Some(p) = opts.only_pid { format!(", pid={p}") } else { String::new() },
    ));
    log(&format!("phantom-kernel: instance {}  (Ctrl-C to stop)", inst.display()));

    let pipe = File::open(inst.join("trace_pipe"))?;
    let pfd = pipe.as_raw_fd();
    let stop = install_stop_pipe()?;
    let mut acc = String::new();
    let mut buf = [0u8; 8192];
    'read: loop {
        match poll_readable(&[pfd, stop])? {
            Some(0) => {
                let n = (&pipe).read(&mut buf)?;
                if n == 0 {
                    break;
                }
                acc.push_str(&String::from_utf8_lossy(&buf[..n]));
                while let Some(nl) = acc.find('\n') {
                    let line: String = acc.drain(..=nl).collect();
                    let Some((comm, pid, event, info)) = parse_line(line.trim_end()) else {
                        continue;
                    };
                    if let Some(want) = opts.only_pid {
                        if pid != want {
                            continue;
                        }
                    }
                    let ev = KernelEvent { pid, comm, verb: classify(event), event, info };
                    if !on_event(&ev) {
                        break 'read;
                    }
                }
            }
            Some(_) => break,
            None => continue,
        }
    }
    Ok(())
}
