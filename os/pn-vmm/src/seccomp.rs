const BPF_LD: u16 = 0x00;
const BPF_W: u16 = 0x00;
const BPF_ABS: u16 = 0x20;
const BPF_JMP: u16 = 0x05;
const BPF_JEQ: u16 = 0x10;
const BPF_RET: u16 = 0x06;
const BPF_K: u16 = 0x00;

#[cfg(target_arch = "x86_64")]
const AUDIT_ARCH_THIS: u32 = 0xC000_003E;
#[cfg(target_arch = "aarch64")]
const AUDIT_ARCH_THIS: u32 = 0xC000_00B7;
const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;
const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_TRAP: u32 = 0x0003_0000;

const SECCOMP_SET_MODE_FILTER: libc::c_ulong = 1;
const SECCOMP_FILTER_FLAG_TSYNC: libc::c_ulong = 1;
const PR_SET_NO_NEW_PRIVS: libc::c_int = 38;

#[repr(C)]
struct SockFilter {
    code: u16,
    jt: u8,
    jf: u8,
    k: u32,
}
#[repr(C)]
struct SockFprog {
    len: u16,
    filter: *const SockFilter,
}

fn stmt(code: u16, k: u32) -> SockFilter {
    SockFilter { code, jt: 0, jf: 0, k }
}
fn jeq(nr: u32, jt: u8, jf: u8) -> SockFilter {
    SockFilter { code: BPF_JMP | BPF_JEQ | BPF_K, jt, jf, k: nr }
}

#[cfg(target_arch = "x86_64")]
const ALLOW: &[u32] = &[
    0, 1, 3, 5, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 24, 25, 26, 27, 28, 32, 33, 35, 36,
    37, 38, 39, 44, 45, 46, 47, 48, 60, 72, 74, 75, 96, 128, 131, 186, 202, 204, 218, 219, 228, 229, 230,
    231, 232, 233, 234, 271, 273, 274, 281, 288, 290, 302, 318, 324, 334,
];

#[cfg(target_arch = "aarch64")]
const ALLOW: &[u32] = &[
    19, 21, 22, 23, 24, 25, 29, 57, 62, 63, 64, 65, 66, 67, 68, 73, 79, 80, 82, 83, 93, 94, 96, 98,
    99, 100, 101, 102, 103, 113, 114, 115, 123, 124, 128, 131, 132, 135, 137, 139, 169, 172, 178, 206,
    207, 210, 211, 212, 214, 215, 216, 222, 226, 227, 232, 233, 242, 261, 278, 283, 291, 293,
];

extern "C" fn sigsys(_sig: libc::c_int, info: *mut libc::siginfo_t, _ctx: *mut libc::c_void) {
    let nr: i32 = if info.is_null() {
        -1
    } else {
        unsafe { *((info as *const u8).add(24) as *const i32) }
    };
    let mut buf = [0u8; 64];
    let mut n = 0usize;
    for &b in b"[pn-vmm] FATAL seccomp: blocked syscall " {
        buf[n] = b;
        n += 1;
    }
    let mut num = if nr < 0 { 0u32 } else { nr as u32 };
    let mut tmp = [0u8; 10];
    let mut t = 0usize;
    if num == 0 {
        tmp[t] = b'0';
        t += 1;
    }
    while num > 0 {
        tmp[t] = b'0' + (num % 10) as u8;
        num /= 10;
        t += 1;
    }
    while t > 0 {
        t -= 1;
        buf[n] = tmp[t];
        n += 1;
    }
    for &b in b" -> abort\n" {
        buf[n] = b;
        n += 1;
    }
    unsafe {
        libc::write(2, buf.as_ptr() as *const libc::c_void, n);
        libc::syscall(libc::SYS_exit_group, 159);
    }
}

pub fn install() -> Result<(), String> {
    if std::env::var("PN_VMM_SECCOMP").map(|v| v == "0" || v == "off").unwrap_or(false) {
        eprintln!("[pn-vmm] seccomp DISABLED via PN_VMM_SECCOMP=0 (no in-process sandbox)");
        return Ok(());
    }
    assert!(ALLOW.len() < 250, "allowlist too long for u8 BPF jump offsets");

    unsafe {
        let mut sa: libc::sigaction = std::mem::zeroed();
        sa.sa_sigaction = sigsys as usize;
        sa.sa_flags = libc::SA_SIGINFO;
        libc::sigemptyset(&mut sa.sa_mask);
        libc::sigaction(libc::SIGSYS, &sa, std::ptr::null_mut());
    }

    let mut prog: Vec<SockFilter> = Vec::with_capacity(ALLOW.len() + 6);

    prog.push(stmt(BPF_LD | BPF_W | BPF_ABS, 4));
    prog.push(jeq(AUDIT_ARCH_THIS, 1, 0));
    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS));
    prog.push(stmt(BPF_LD | BPF_W | BPF_ABS, 0));
    let k = ALLOW.len();
    for (i, nr) in ALLOW.iter().enumerate() {

        let jt = (k - i) as u8;
        prog.push(jeq(*nr, jt, 0));
    }
    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_TRAP));
    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW));

    unsafe {
        if libc::prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
            return Err(format!("prctl(NO_NEW_PRIVS): {}", std::io::Error::last_os_error()));
        }
        let fprog = SockFprog { len: prog.len() as u16, filter: prog.as_ptr() };
        let r = libc::syscall(
            libc::SYS_seccomp,
            SECCOMP_SET_MODE_FILTER,
            SECCOMP_FILTER_FLAG_TSYNC,
            &fprog as *const SockFprog as usize,
        );
        if r != 0 {
            return Err(format!("seccomp(SET_MODE_FILTER, TSYNC): {}", std::io::Error::last_os_error()));
        }
    }
    eprintln!("[pn-vmm] seccomp active: {k} syscalls allowed on all threads (openat/execve/socket/clone/ptrace denied)");
    Ok(())
}
