use std::ffi::CString;
use std::io::Write;
use std::os::raw::{c_char, c_int, c_long, c_void};

type Pid = i32;

const PTRACE_TRACEME: c_long = 0;
const PTRACE_CONT: c_long = 7;
const PTRACE_GETREGS: c_long = 12;
const PTRACE_SETREGS: c_long = 13;
const PTRACE_SETOPTIONS: c_long = 0x4200;
const PTRACE_SYSCALL: c_long = 24;
const PTRACE_O_TRACESYSGOOD: c_long = 1;

const SYS_READ: u64 = 0;
const SYS_WRITE: u64 = 1;
const SYS_SENDTO: u64 = 44;
const SYS_SENDMSG: u64 = 46;

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct Regs {
    r15: u64, r14: u64, r13: u64, r12: u64, rbp: u64, rbx: u64, r11: u64, r10: u64,
    r9: u64, r8: u64, rax: u64, rcx: u64, rdx: u64, rsi: u64, rdi: u64, orig_rax: u64,
    rip: u64, cs: u64, eflags: u64, rsp: u64, ss: u64, fs_base: u64, gs_base: u64,
    ds: u64, es: u64, fs: u64, gs: u64,
}

#[repr(C)]
struct IoVec {
    base: *mut c_void,
    len: usize,
}

extern "C" {
    fn ptrace(request: c_long, pid: Pid, addr: *mut c_void, data: *mut c_void) -> c_long;
    fn fork() -> Pid;
    fn execvp(file: *const c_char, argv: *const *const c_char) -> c_int;
    fn waitpid(pid: Pid, status: *mut c_int, options: c_int) -> Pid;
    fn _exit(code: c_int) -> !;
    fn process_vm_readv(
        pid: Pid, local: *const IoVec, liovcnt: u64, remote: *const IoVec, riovcnt: u64, flags: u64,
    ) -> isize;
    fn process_vm_writev(
        pid: Pid, local: *const IoVec, liovcnt: u64, remote: *const IoVec, riovcnt: u64, flags: u64,
    ) -> isize;
}

fn getregs(pid: Pid) -> Regs {
    let mut r = Regs::default();
    unsafe { ptrace(PTRACE_GETREGS, pid, std::ptr::null_mut(), &mut r as *mut _ as *mut c_void) };
    r
}

fn setregs(pid: Pid, r: &Regs) {
    unsafe { ptrace(PTRACE_SETREGS, pid, std::ptr::null_mut(), r as *const _ as *mut c_void) };
}

fn read_mem(pid: Pid, addr: u64, len: usize) -> Vec<u8> {
    let len = len.min(4096);
    let mut buf = vec![0u8; len];
    let local = IoVec { base: buf.as_mut_ptr() as *mut c_void, len };
    let remote = IoVec { base: addr as *mut c_void, len };
    let n = unsafe { process_vm_readv(pid, &local, 1, &remote, 1, 0) };
    if n <= 0 {
        return Vec::new();
    }
    buf.truncate(n as usize);
    buf
}

fn write_mem(pid: Pid, addr: u64, bytes: &[u8]) -> isize {
    let local = IoVec { base: bytes.as_ptr() as *mut c_void, len: bytes.len() };
    let remote = IoVec { base: addr as *mut c_void, len: bytes.len() };
    unsafe { process_vm_writev(pid, &local, 1, &remote, 1, 0) }
}

fn fd_label(fd: u64) -> String {
    match fd {
        0 => "stdin".into(),
        1 => "stdout".into(),
        2 => "stderr".into(),
        _ => format!("fd{fd}"),
    }
}

fn show(bytes: &[u8]) -> String {
    let mut s = String::new();
    for &b in bytes {
        match b {
            b'\n' => s.push_str("\\n"),
            b'\t' => s.push_str("\\t"),
            b'\r' => s.push_str("\\r"),
            0x20..=0x7e => s.push(b as char),
            _ => s.push_str(&format!("\\x{b:02x}")),
        }
    }
    s
}

fn main() {
    #[cfg(not(target_arch = "x86_64"))]
    {
        eprintln!("phantom-trace: nur auf x86_64 unterstuetzt (ptrace-Registerlayout ist arch-spezifisch)");
        std::process::exit(2);
    }

    let args: Vec<String> = std::env::args().collect();

    let split = args.iter().position(|a| a == "--");
    let cmd: Vec<String> = match split {
        Some(i) if i + 1 < args.len() => args[i + 1..].to_vec(),
        _ => {
            eprintln!("usage: phantom-trace -- <program> [args...]");
            eprintln!("       PHANTOM_INJECT=\"text\" phantom-trace -- <program>   (forge first stdin read)");
            eprintln!("       PHANTOM_TRACE_OUT=<file> phantom-trace -- <program>  (observations -> file, keeps stdout clean)");
            std::process::exit(2);
        }
    };

    let cstrs: Vec<CString> = cmd.iter().map(|s| CString::new(s.as_str()).unwrap()).collect();
    let mut cargv: Vec<*const c_char> = cstrs.iter().map(|c| c.as_ptr()).collect();
    cargv.push(std::ptr::null());

    let inject = std::env::var("PHANTOM_INJECT").ok().map(|s| s.replace("\\n", "\n").into_bytes());
    let mut injected = false;

    let show_all = std::env::var("PHANTOM_TRACE_ALL").is_ok();
    let tapped = |fd: u64| show_all || fd <= 2;

    let pid = unsafe { fork() };
    if pid == 0 {

        unsafe {
            ptrace(PTRACE_TRACEME, 0, std::ptr::null_mut(), std::ptr::null_mut());
            execvp(cargv[0], cargv.as_ptr());
            _exit(127);
        }
    }

    eprintln!("phantom-trace: tapping intention of pid {pid}  ({})", cmd.join(" "));

    let mut status: c_int = 0;
    unsafe { waitpid(pid, &mut status, 0) };
    unsafe {
        ptrace(PTRACE_SETOPTIONS, pid, std::ptr::null_mut(), PTRACE_O_TRACESYSGOOD as *mut c_void);
    }

    let mut at_entry = true;
    let mut pending_read: Option<(u64, u64)> = None;

    let mut obs: Box<dyn Write> = match std::env::var("PHANTOM_TRACE_OUT") {
        Ok(p) if !p.is_empty() => match std::fs::File::create(&p) {
            Ok(f) => {
                eprintln!("phantom-trace: observations -> {p}");
                Box::new(f)
            }
            Err(e) => {
                eprintln!("phantom-trace: cannot open PHANTOM_TRACE_OUT={p}: {e}");
                std::process::exit(2);
            }
        },
        _ => Box::new(std::io::stderr()),
    };

    loop {

        unsafe { ptrace(PTRACE_SYSCALL, pid, std::ptr::null_mut(), std::ptr::null_mut()) };
        let r = unsafe { waitpid(pid, &mut status, 0) };
        if r < 0 {
            break;
        }

        if status & 0x7f == 0 {
            let code = (status >> 8) & 0xff;
            eprintln!("phantom-trace: pid {pid} exited ({code})");
            break;
        }
        let stopped = (status & 0xff) == 0x7f;
        let stopsig = (status >> 8) & 0xff;
        if !stopped {
            continue;
        }
        if stopsig != 0x85 {

            unsafe {
                ptrace(PTRACE_CONT, pid, std::ptr::null_mut(), stopsig as *mut c_void);
            }
            continue;
        }

        let mut regs = getregs(pid);
        let nr = regs.orig_rax;

        if at_entry {
            match nr {
                SYS_WRITE | SYS_SENDTO | SYS_SENDMSG => {

                    let (fd, buf, len) = (regs.rdi, regs.rsi, regs.rdx);
                    if len > 0 && nr == SYS_WRITE && tapped(fd) {
                        let bytes = read_mem(pid, buf, len as usize);
                        if !bytes.is_empty() {
                            let _ = writeln!(obs, "→ {} «{}»", fd_label(fd), show(&bytes));
                            let _ = obs.flush();
                        }
                    } else if nr != SYS_WRITE && tapped(fd) {
                        let _ = writeln!(obs, "→ {} (sendmsg/sendto, {len}B)", fd_label(fd));
                        let _ = obs.flush();
                    }
                }
                SYS_READ => {

                    pending_read = Some((regs.rdi, regs.rsi));
                }
                _ => {}
            }
        } else {

            if nr == SYS_READ {
                if let Some((fd, buf)) = pending_read.take() {
                    let ret = regs.rax as i64;
                    if ret > 0 {

                        if fd == 0 && !injected {
                            if let Some(forge) = &inject {
                                let n = write_mem(pid, buf, forge);
                                if n > 0 {
                                    regs.rax = n as u64;
                                    setregs(pid, &regs);
                                    injected = true;
                                    let _ = writeln!(obs, "⇐ stdin INJECTED «{}» ({n}B)", show(forge));
                                    let _ = obs.flush();
                                    at_entry = !at_entry;
                                    continue;
                                }
                            }
                        }
                        if tapped(fd) {
                            let bytes = read_mem(pid, buf, ret as usize);
                            if !bytes.is_empty() {
                                let _ = writeln!(obs, "← {} «{}»", fd_label(fd), show(&bytes));
                                let _ = obs.flush();
                            }
                        }
                    }
                }
            }
        }
        at_entry = !at_entry;
    }
}
