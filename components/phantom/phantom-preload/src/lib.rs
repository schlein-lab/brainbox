#![no_std]
#![allow(clippy::missing_safety_doc)]

use core::panic::PanicInfo;

mod sys {
    use core::arch::asm;

    pub const SYS_WRITE: i64 = 1;
    pub const SYS_CLOSE: i64 = 3;
    pub const SYS_SOCKET: i64 = 41;
    pub const SYS_CONNECT: i64 = 42;
    pub const SYS_GETPID: i64 = 39;
    pub const SYS_GETPPID: i64 = 110;
    pub const SYS_OPENAT: i64 = 257;
    pub const SYS_READ: i64 = 0;

    pub const AF_UNIX: i64 = 1;
    pub const SOCK_STREAM: i64 = 1;
    pub const SOCK_NONBLOCK: i64 = 0o4000;
    pub const SOCK_CLOEXEC: i64 = 0o2000_000;
    pub const O_RDONLY: i64 = 0;
    pub const O_CLOEXEC: i64 = 0o2000_000;
    pub const AT_FDCWD: i64 = -100;

    #[inline(always)]
    pub unsafe fn sc1(nr: i64, a1: i64) -> i64 {
        let r;
        asm!("syscall", inlateout("rax") nr => r, in("rdi") a1,
             out("rcx") _, out("r11") _, options(nostack));
        r
    }
    #[inline(always)]
    pub unsafe fn sc3(nr: i64, a1: i64, a2: i64, a3: i64) -> i64 {
        let r;
        asm!("syscall", inlateout("rax") nr => r, in("rdi") a1, in("rsi") a2,
             in("rdx") a3, out("rcx") _, out("r11") _, options(nostack));
        r
    }
    #[inline(always)]
    pub unsafe fn sc4(nr: i64, a1: i64, a2: i64, a3: i64, a4: i64) -> i64 {
        let r;
        asm!("syscall", inlateout("rax") nr => r, in("rdi") a1, in("rsi") a2,
             in("rdx") a3, in("r10") a4, out("rcx") _, out("r11") _, options(nostack));
        r
    }

    #[inline(always)]
    pub unsafe fn getpid() -> i32 {
        sc1(SYS_GETPID, 0) as i32
    }
    #[inline(always)]
    pub unsafe fn getppid() -> i32 {
        sc1(SYS_GETPPID, 0) as i32
    }
    #[inline(always)]
    pub unsafe fn socket(domain: i64, ty: i64, proto: i64) -> i64 {
        sc3(SYS_SOCKET, domain, ty, proto)
    }
    #[inline(always)]
    pub unsafe fn connect(fd: i64, addr: *const u8, len: i64) -> i64 {
        sc3(SYS_CONNECT, fd, addr as i64, len)
    }
    #[inline(always)]
    pub unsafe fn write(fd: i64, buf: *const u8, len: i64) -> i64 {
        sc3(SYS_WRITE, fd, buf as i64, len)
    }
    #[inline(always)]
    pub unsafe fn read(fd: i64, buf: *mut u8, len: i64) -> i64 {
        sc3(SYS_READ, fd, buf as i64, len)
    }
    #[inline(always)]
    pub unsafe fn close(fd: i64) {
        let _ = sc1(SYS_CLOSE, fd);
    }
    #[inline(always)]
    pub unsafe fn open_ro(path: *const u8) -> i64 {
        sc4(SYS_OPENAT, AT_FDCWD, path as i64, O_RDONLY | O_CLOEXEC, 0)
    }
}

const LINE_CAP: usize = 1024;

struct Line {
    buf: [u8; LINE_CAP],
    len: usize,
}

impl Line {
    const fn new() -> Line {
        Line { buf: [0u8; LINE_CAP], len: 0 }
    }

    fn put(&mut self, b: &[u8]) {
        let space = LINE_CAP - self.len;
        let n = if b.len() < space { b.len() } else { space };
        let mut i = 0;
        while i < n {
            self.buf[self.len + i] = b[i];
            i += 1;
        }
        self.len += n;
    }

    fn put_escaped(&mut self, b: &[u8]) {
        let mut i = 0;
        while i < b.len() {
            let c = b[i];
            match c {
                b'"' => self.put(b"\\\""),
                b'\\' => self.put(b"\\\\"),
                b'\n' => self.put(b"\\n"),
                b'\r' => self.put(b"\\r"),
                b'\t' => self.put(b"\\t"),
                0x00..=0x1f => self.put(b"?"),
                _ => {
                    let one = [c];
                    self.put(&one);
                }
            }
            i += 1;
        }
    }
    fn put_i32(&mut self, mut v: i32) {
        if v < 0 {
            self.put(b"-");

            let mut n = -(v as i64);
            self.put_u64(n as u64);
            n = 0;
            let _ = n;
            return;
        }
        if v == 0 {
            self.put(b"0");
            return;
        }
        let mut tmp = [0u8; 10];
        let mut p = tmp.len();
        while v > 0 {
            p -= 1;
            tmp[p] = b'0' + (v % 10) as u8;
            v /= 10;
        }
        self.put(&tmp[p..]);
    }
    fn put_u64(&mut self, mut v: u64) {
        if v == 0 {
            self.put(b"0");
            return;
        }
        let mut tmp = [0u8; 20];
        let mut p = tmp.len();
        while v > 0 {
            p -= 1;
            tmp[p] = b'0' + (v % 10) as u8;
            v /= 10;
        }
        self.put(&tmp[p..]);
    }
}

unsafe fn read_file(path: &[u8], out: &mut [u8]) -> usize {
    let fd = sys::open_ro(path.as_ptr());
    if fd < 0 {
        return 0;
    }
    let mut total = 0usize;
    while total < out.len() {
        let n = sys::read(fd, out.as_mut_ptr().add(total), (out.len() - total) as i64);
        if n <= 0 {
            break;
        }
        total += n as usize;
    }
    sys::close(fd);
    total
}

fn clean<'a>(b: &'a [u8]) -> &'a [u8] {
    let mut end = b.len();
    let mut i = 0;
    while i < b.len() {
        if b[i] == 0 || b[i] == b'\n' {
            end = i;
            break;
        }
        i += 1;
    }
    &b[..end]
}

const SOCK_PATH: &[u8] = b"/run/phantom/preload.sock\0";

#[repr(C)]
struct SockaddrUn {
    family: u16,
    path: [u8; 108],
}

unsafe fn announce() {

    let pid = sys::getpid();
    let ppid = sys::getppid();

    let mut comm_buf = [0u8; 64];
    let comm_n = read_file(b"/proc/self/comm\0", &mut comm_buf);
    let comm = clean(&comm_buf[..comm_n]);

    let mut exe_buf = [0u8; 512];
    let exe_n = read_file(b"/proc/self/cmdline\0", &mut exe_buf);
    let exe = clean(&exe_buf[..exe_n]);

    let mut line = Line::new();
    line.put(b"{\"v\":1,\"src\":\"preload\",\"pid\":");
    line.put_i32(pid);
    line.put(b",\"ppid\":");
    line.put_i32(ppid);
    line.put(b",\"comm\":\"");
    line.put_escaped(comm);
    line.put(b"\",\"exe\":\"");
    line.put_escaped(exe);
    line.put(b"\"}\n");

    let fd = sys::socket(sys::AF_UNIX, sys::SOCK_STREAM | sys::SOCK_NONBLOCK | sys::SOCK_CLOEXEC, 0);
    if fd < 0 {
        return;
    }

    let mut addr = SockaddrUn { family: sys::AF_UNIX as u16, path: [0u8; 108] };
    let mut i = 0;

    while i < SOCK_PATH.len() && i < addr.path.len() {
        addr.path[i] = SOCK_PATH[i];
        i += 1;
    }

    let path_len = {
        let mut n = 0;
        while n < addr.path.len() && addr.path[n] != 0 {
            n += 1;
        }
        n
    };
    let addrlen = (2 + path_len + 1) as i64;

    let rc = sys::connect(fd, &addr as *const SockaddrUn as *const u8, addrlen);

    if rc < 0 {
        sys::close(fd);
        return;
    }

    let _ = sys::write(fd, line.buf.as_ptr(), line.len as i64);
    sys::close(fd);
}

#[no_mangle]
pub extern "C" fn phantom_preload_init() {

    unsafe { announce() };
}

#[used]
#[link_section = ".init_array"]
static INIT: extern "C" fn() = phantom_preload_init;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {
        core::hint::spin_loop();
    }
}
