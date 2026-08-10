use std::io;
use std::os::raw::{c_int, c_long, c_ulong};

extern "C" {
    fn syscall(num: c_long, ...) -> c_long;

    fn ioctl(fd: c_int, request: c_ulong, arg: c_ulong) -> c_int;
}

const SYS_SECCOMP: c_long = 317;
const SYS_PRCTL: c_long = 157;

const PR_SET_NO_NEW_PRIVS: c_long = 38;

const SECCOMP_SET_MODE_FILTER: c_long = 1;
const SECCOMP_FILTER_FLAG_NEW_LISTENER: c_long = 1 << 3;

const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_USER_NOTIF: u32 = 0x7fc0_0000;
const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;

pub const SECCOMP_USER_NOTIF_FLAG_CONTINUE: u32 = 1 << 0;

const SECCOMP_IOCTL_NOTIF_RECV: c_ulong = 0xc050_2100;
const SECCOMP_IOCTL_NOTIF_SEND: c_ulong = 0xc018_2101;
const SECCOMP_IOCTL_NOTIF_ID_VALID: c_ulong = 0x4008_2102;
const SECCOMP_IOCTL_NOTIF_ADDFD: c_ulong = 0x4018_2103;

pub const SECCOMP_ADDFD_FLAG_SETFD: u32 = 1 << 0;
pub const SECCOMP_ADDFD_FLAG_SEND: u32 = 1 << 1;

const AUDIT_ARCH_X86_64: u32 = 0xC000_003E;

#[repr(C)]
#[derive(Clone, Copy)]
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

const BPF_LD: u16 = 0x00;
const BPF_JMP: u16 = 0x05;
const BPF_RET: u16 = 0x06;
const BPF_W: u16 = 0x00;
const BPF_ABS: u16 = 0x20;
const BPF_JEQ: u16 = 0x10;
const BPF_K: u16 = 0x00;

const OFF_NR: u32 = 0;
const OFF_ARCH: u32 = 4;

#[inline]
fn stmt(code: u16, k: u32) -> SockFilter {
    SockFilter { code, jt: 0, jf: 0, k }
}
#[inline]
fn jump(code: u16, k: u32, jt: u8, jf: u8) -> SockFilter {
    SockFilter { code, jt, jf, k }
}

fn build_filter(traps: &[u32]) -> Vec<SockFilter> {
    let mut prog: Vec<SockFilter> = Vec::new();

    prog.push(stmt(BPF_LD | BPF_W | BPF_ABS, OFF_ARCH));

    prog.push(jump(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0));

    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS));

    prog.push(stmt(BPF_LD | BPF_W | BPF_ABS, OFF_NR));

    let n = traps.len();
    for (i, &t) in traps.iter().enumerate() {
        let to_notif = (n - i) as u8;
        prog.push(jump(BPF_JMP | BPF_JEQ | BPF_K, t, to_notif, 0));
    }

    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW));

    prog.push(stmt(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF));

    prog
}

pub fn install_notify_filter(traps: &[u32]) -> io::Result<c_int> {

    let r = unsafe {
        syscall(SYS_PRCTL, PR_SET_NO_NEW_PRIVS as c_long, 1 as c_long, 0 as c_long, 0 as c_long, 0 as c_long)
    };
    if r != 0 {
        return Err(io::Error::last_os_error());
    }

    let prog = build_filter(traps);
    let fprog = SockFprog { len: prog.len() as u16, filter: prog.as_ptr() };

    let fd = unsafe {
        syscall(
            SYS_SECCOMP,
            SECCOMP_SET_MODE_FILTER,
            SECCOMP_FILTER_FLAG_NEW_LISTENER,
            &fprog as *const SockFprog as c_long,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }

    Ok(fd as c_int)
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct SeccompData {
    pub nr: i32,
    pub arch: u32,
    pub instruction_pointer: u64,
    pub args: [u64; 6],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct SeccompNotif {

    pub id: u64,

    pub pid: u32,
    pub flags: u32,
    pub data: SeccompData,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct SeccompNotifResp {
    pub id: u64,

    pub val: i64,

    pub error: i32,

    pub flags: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct SeccompNotifAddfd {
    pub id: u64,
    pub flags: u32,

    pub srcfd: u32,

    pub newfd: u32,
    pub newfd_flags: u32,
}

pub fn notif_recv(listener: c_int) -> io::Result<SeccompNotif> {
    let mut notif = SeccompNotif::default();
    let r = unsafe {
        ioctl(listener, SECCOMP_IOCTL_NOTIF_RECV, &mut notif as *mut _ as c_ulong)
    };
    if r < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(notif)
}

pub fn notif_send(listener: c_int, resp: &SeccompNotifResp) -> io::Result<()> {

    let mut r = *resp;
    let rc = unsafe {
        ioctl(listener, SECCOMP_IOCTL_NOTIF_SEND, &mut r as *mut _ as c_ulong)
    };
    if rc < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn notif_id_valid(listener: c_int, id: u64) -> bool {
    let mut id = id;
    let r = unsafe {
        ioctl(listener, SECCOMP_IOCTL_NOTIF_ID_VALID, &mut id as *mut _ as c_ulong)
    };
    r == 0
}

pub fn notif_addfd(listener: c_int, addfd: &SeccompNotifAddfd) -> io::Result<c_int> {
    let mut a = *addfd;
    let r = unsafe {
        ioctl(listener, SECCOMP_IOCTL_NOTIF_ADDFD, &mut a as *mut _ as c_ulong)
    };
    if r < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(r)
}

impl SeccompNotifResp {

    pub fn allow(id: u64) -> Self {
        SeccompNotifResp { id, val: 0, error: 0, flags: SECCOMP_USER_NOTIF_FLAG_CONTINUE }
    }

    pub fn errno(id: u64, errno: i32) -> Self {
        SeccompNotifResp { id, val: 0, error: -errno, flags: 0 }
    }

    pub fn fake(id: u64, val: i64) -> Self {
        SeccompNotifResp { id, val, error: 0, flags: 0 }
    }
}

pub fn syscall_nr(name: &str) -> Option<u32> {
    Some(match name {
        "read" => 0,
        "write" => 1,
        "open" => 2,
        "close" => 3,
        "openat" => 257,
        "connect" => 42,
        "execve" => 59,
        "execveat" => 322,
        "ptrace" => 101,
        "socket" => 41,
        "mmap" => 9,
        "kill" => 62,
        _ => return None,
    })
}

pub fn syscall_name(nr: i32) -> &'static str {
    match nr {
        0 => "read",
        1 => "write",
        2 => "open",
        3 => "close",
        257 => "openat",
        42 => "connect",
        59 => "execve",
        322 => "execveat",
        101 => "ptrace",
        41 => "socket",
        9 => "mmap",
        62 => "kill",
        _ => "syscall",
    }
}
