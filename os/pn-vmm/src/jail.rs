use std::io::Write;

const CLONE_NEWNS: libc::c_int = 0x0002_0000;
const CLONE_NEWUSER: libc::c_int = 0x1000_0000;
const MS_BIND: libc::c_ulong = 0x1000;
const MS_REC: libc::c_ulong = 0x4000;
const MS_PRIVATE: libc::c_ulong = 0x0004_0000;
const PR_SET_DUMPABLE: libc::c_int = 4;
const PR_SET_NO_NEW_PRIVS: libc::c_int = 38;

const HIDE_FILE: &str = "/etc/brainbox/pn-vmm-jail-hide.conf";

const DEFAULT_HIDE: &[&str] = &[

    ".ssh", ".env", ".env.local", ".aws", ".gnupg", ".netrc", ".git-credentials", ".docker", ".kube",
    ".config/gcloud", ".config/gh", ".cargo/credentials.toml", ".cargo/credentials",
    ".npmrc", ".pypirc",

    ".claude/.credentials.json", ".claude.json", ".claude/backups",

    ".pn-poolhome",

    ".brainbox-fleet", ".config/pn-breakglass", ".config/brainbox-workers",
    ".config/brainbox-portal/key.pem",

    ".config/phantom", ".config/job-announcer.env",
    ".config/ha-llt.token", ".config/homeassistant-owner.env", ".config/nabu-display.env",
    "zyrkel/.env", "Zyrkel/.env", "smarthome/.env", "smarthome/config/portal.env",
    "homeassistant/config/secrets.yaml",

    ".bash_history", ".zsh_history", ".python_history",

    "/etc/brainbox/secrets.env",
];

fn warn(strict: bool, msg: &str) -> Result<(), String> {
    if strict {
        Err(msg.to_string())
    } else {
        eprintln!("[pn-vmm] jail WARN (continuing, seccomp still active): {msg}");
        Ok(())
    }
}

pub fn apply() -> Result<(), String> {

    unsafe {
        if libc::prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
            eprintln!("[pn-vmm] jail WARN: PR_SET_NO_NEW_PRIVS failed: {}", std::io::Error::last_os_error());
        }
    }

    let r = jail_namespaces();

    unsafe {
        if libc::prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0 {
            eprintln!("[pn-vmm] jail WARN: PR_SET_DUMPABLE failed: {}", std::io::Error::last_os_error());
        }
    }
    r
}

fn load_hide_file(strict: bool, out: &mut Vec<String>) -> Result<(), String> {
    let path = std::env::var("PN_VMM_JAIL_HIDE_FILE").unwrap_or_else(|_| HIDE_FILE.to_string());
    if path.is_empty() {
        return Ok(());
    }
    match std::fs::read_to_string(&path) {
        Ok(txt) => {
            for line in txt.lines() {
                let l = line.split('#').next().unwrap_or("").trim();
                if !l.is_empty() {
                    out.push(l.to_string());
                }
            }
            Ok(())
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => warn(strict, &format!("hide list {path} exists but is unreadable: {e}")),
    }
}

fn scratch_candidates() -> Vec<String> {
    let mut v = vec!["/tmp/.pn-jail".to_string()];
    if let Ok(x) = std::env::var("XDG_RUNTIME_DIR") {
        if !x.is_empty() {
            v.push(format!("{x}/.pn-jail"));
        }
    }
    v.push("/dev/shm/.pn-jail".to_string());
    v
}

fn jail_namespaces() -> Result<(), String> {
    if std::env::var("PN_VMM_JAIL").map(|v| v == "0" || v == "off").unwrap_or(false) {
        eprintln!("[pn-vmm] jail: crown-jewel hiding DISABLED via PN_VMM_JAIL=0 (no_new_privs/dumpable still set)");
        return Ok(());
    }

    let strict = std::env::var("PN_VMM_JAIL_STRICT")
        .map(|v| {
            let v = v.trim().to_ascii_lowercase();
            !(v == "0" || v == "off" || v == "no" || v == "false")
        })
        .unwrap_or(true);

    let home = match std::env::var("HOME") {
        Ok(h) if !h.is_empty() => h,
        _ => return warn(strict, "HOME unset — cannot locate crown-jewel paths to hide"),
    };
    let uid = unsafe { libc::getuid() };
    let gid = unsafe { libc::getgid() };

    if unsafe { libc::unshare(CLONE_NEWUSER | CLONE_NEWNS) } != 0 {
        return warn(strict, &format!("unshare(NEWUSER|NEWNS): {}", std::io::Error::last_os_error()));
    }

    let _ = std::fs::write("/proc/self/setgroups", "deny");
    if std::fs::write("/proc/self/uid_map", format!("{uid} {uid} 1")).is_err()
        || std::fs::write("/proc/self/gid_map", format!("{gid} {gid} 1")).is_err()
    {
        return warn(strict, "writing identity uid_map/gid_map failed");
    }

    if unsafe {
        libc::mount(b"none\0".as_ptr() as *const libc::c_char, b"/\0".as_ptr() as *const libc::c_char,
                    std::ptr::null(), MS_REC | MS_PRIVATE, std::ptr::null())
    } != 0
    {
        return warn(strict, &format!("mount(/, MS_PRIVATE|MS_REC): {}", std::io::Error::last_os_error()));
    }

    let mut scratch = String::new();
    let mut empty_file = String::new();
    let mut mounted_scratch = false;
    let mut scratch_err = String::from("no candidate mountpoint could be created");
    for cand in scratch_candidates() {
        if std::fs::create_dir_all(&cand).is_err() {
            scratch_err = format!("{cand}: {}", std::io::Error::last_os_error());
            continue;
        }
        if unsafe {
            libc::mount(b"tmpfs\0".as_ptr() as *const libc::c_char, cstr(&cand).as_ptr(),
                        b"tmpfs\0".as_ptr() as *const libc::c_char, 0,
                        b"mode=0000\0".as_ptr() as *const libc::c_void)
        } != 0
        {
            scratch_err = format!("{cand}: {}", std::io::Error::last_os_error());
            continue;
        }
        let ef = format!("{cand}/empty");
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).write(true).open(&ef) {
            let _ = f.write_all(b"");
            let _ = f.flush();
        }
        unsafe { libc::chmod(cstr(&ef).as_ptr(), 0o000); }
        scratch = cand;
        empty_file = ef;
        mounted_scratch = true;
        break;
    }

    let mut hide: Vec<String> = DEFAULT_HIDE.iter().map(|s| s.to_string()).collect();
    if let Ok(extra) = std::env::var("PN_VMM_JAIL_HIDE") {
        for p in extra.split(':').filter(|p| !p.is_empty()) {
            hide.push(p.to_string());
        }
    }
    load_hide_file(strict, &mut hide)?;

    let mut hidden = 0usize;
    let mut absent = 0usize;
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for rel in &hide {
        let path = if rel.starts_with('/') { rel.clone() } else { format!("{home}/{rel}") };
        if !seen.insert(path.clone()) {
            continue;
        }

        let meta = match std::fs::metadata(&path) {
            Ok(m) => m,
            Err(_) => {
                absent += 1;
                continue;
            }
        };
        let ok = if meta.is_dir() {
            unsafe {
                libc::mount(b"tmpfs\0".as_ptr() as *const libc::c_char, cstr(&path).as_ptr(),
                            b"tmpfs\0".as_ptr() as *const libc::c_char, 0,
                            b"mode=0000\0".as_ptr() as *const libc::c_void) == 0
            }
        } else if mounted_scratch {
            unsafe {
                libc::mount(cstr(&empty_file).as_ptr(), cstr(&path).as_ptr(),
                            std::ptr::null(), MS_BIND, std::ptr::null()) == 0
            }
        } else {
            false
        };
        if ok {
            hidden += 1;
        } else {

            let why = if !meta.is_dir() && !mounted_scratch {
                format!("no empty 0000 inode to bind over it — private scratch tmpfs unavailable ({scratch_err})")
            } else {
                std::io::Error::last_os_error().to_string()
            };
            warn(strict, &format!("could not hide {path}: {why}"))?;
        }
    }
    eprintln!("[pn-vmm] jail: userns+mountns active, {hidden} crown-jewel path(s) hidden, {absent} absent \
               ({} scratch{})",
              if mounted_scratch { scratch.as_str() } else { "none" },
              if strict { ", fail-closed" } else { ", PERMISSIVE via PN_VMM_JAIL_STRICT=0" });
    Ok(())
}

fn cstr(s: &str) -> std::ffi::CString {
    std::ffi::CString::new(s).unwrap_or_else(|_| std::ffi::CString::new("").unwrap())
}
