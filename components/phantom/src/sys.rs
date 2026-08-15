use std::io;
use std::os::fd::RawFd;
use std::os::raw::{c_char, c_int, c_uint, c_ulong, c_void};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};

const SOL_SOCKET: c_int = 1;
const SCM_RIGHTS: c_int = 1;
const MSG_NOSIGNAL: c_int = 0x4000;

#[repr(C)]
struct IoVec {
    base: *mut c_void,
    len: usize,
}

#[repr(C)]
struct MsgHdr {
    name: *mut c_void,
    namelen: u32,
    iov: *mut IoVec,
    iovlen: usize,
    control: *mut c_void,
    controllen: usize,
    flags: c_int,
}

#[repr(C)]
struct CmsgHdr {
    len: usize,
    level: c_int,
    ctype: c_int,
}

extern "C" {
    fn recvmsg(fd: c_int, msg: *mut MsgHdr, flags: c_int) -> isize;
    fn sendmsg(fd: c_int, msg: *const MsgHdr, flags: c_int) -> isize;
    fn ioctl(fd: c_int, request: c_ulong, arg: c_ulong) -> c_int;
    fn mmap(addr: *mut c_void, len: usize, prot: c_int, flags: c_int, fd: c_int, off: i64) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> c_int;
}

const PROT_READ: c_int = 0x1;
const PROT_WRITE: c_int = 0x2;
const MAP_SHARED: c_int = 0x1;

pub struct Mmap {
    ptr: *mut c_void,
    len: usize,
}

unsafe impl Send for Mmap {}
unsafe impl Sync for Mmap {}

impl Mmap {
    pub fn map_read(fd: c_int, len: usize) -> io::Result<Mmap> {
        if len == 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "zero-length map"));
        }
        let ptr = unsafe { mmap(std::ptr::null_mut(), len, PROT_READ, MAP_SHARED, fd, 0) };
        if ptr as isize == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(Mmap { ptr, len })
    }
    pub fn as_slice(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.ptr as *const u8, self.len) }
    }
    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl Drop for Mmap {
    fn drop(&mut self) {
        unsafe {
            munmap(self.ptr, self.len);
        }
    }
}

pub struct MmapMut {
    ptr: *mut c_void,
    len: usize,
}

unsafe impl Send for MmapMut {}
unsafe impl Sync for MmapMut {}

impl MmapMut {
    pub fn map_rw(fd: c_int, len: usize, offset: i64) -> io::Result<MmapMut> {
        if len == 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "zero-length map"));
        }
        let ptr = unsafe { mmap(std::ptr::null_mut(), len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, offset) };
        if ptr as isize == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(MmapMut { ptr, len })
    }
    pub fn as_slice(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.ptr as *const u8, self.len) }
    }
    pub fn as_mut_slice(&mut self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut(self.ptr as *mut u8, self.len) }
    }
    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl Drop for MmapMut {
    fn drop(&mut self) {
        unsafe {
            munmap(self.ptr, self.len);
        }
    }
}

extern "C" {
    fn memfd_create(name: *const c_char, flags: c_uint) -> c_int;
}

pub fn memfd_with(bytes: &[u8]) -> io::Result<std::os::fd::OwnedFd> {
    use std::io::Write;
    use std::os::fd::{FromRawFd, IntoRawFd, OwnedFd};
    let name = b"phantom\0";
    let fd = unsafe { memfd_create(name.as_ptr() as *const c_char, 0x0001  ) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }

    let mut f = unsafe { std::fs::File::from_raw_fd(fd) };
    f.write_all(bytes)?;
    let raw = f.into_raw_fd();
    Ok(unsafe { OwnedFd::from_raw_fd(raw) })
}

const SO_PEERCRED: c_int = 17;

#[repr(C)]
struct Ucred {
    pid: i32,
    uid: u32,
    gid: u32,
}

extern "C" {
    fn getsockopt(fd: c_int, level: c_int, optname: c_int, optval: *mut c_void, optlen: *mut u32) -> c_int;
}

pub fn peer_pid(fd: c_int) -> Option<i32> {
    let mut cred = Ucred { pid: 0, uid: 0, gid: 0 };
    let mut len = std::mem::size_of::<Ucred>() as u32;
    let r = unsafe {
        getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &mut cred as *mut _ as *mut c_void, &mut len)
    };
    if r == 0 && cred.pid > 0 {
        Some(cred.pid)
    } else {
        None
    }
}

pub fn peer_uid(fd: c_int) -> Option<u32> {
    let mut cred = Ucred { pid: 0, uid: 0, gid: 0 };
    let mut len = std::mem::size_of::<Ucred>() as u32;
    let r = unsafe {
        getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &mut cred as *mut _ as *mut c_void, &mut len)
    };
    if r == 0 && cred.pid > 0 {
        Some(cred.uid)
    } else {
        None
    }
}

extern "C" {
    fn getuid() -> u32;
    fn process_vm_writev(
        pid: c_int,
        local: *const IoVec,
        liovcnt: c_ulong,
        remote: *const IoVec,
        riovcnt: c_ulong,
        flags: c_ulong,
    ) -> isize;
}

pub fn own_uid() -> u32 {
    unsafe { getuid() }
}

pub fn vm_write(pid: i32, addr: u64, bytes: &[u8]) -> io::Result<usize> {
    let local = IoVec { base: bytes.as_ptr() as *mut c_void, len: bytes.len() };
    let remote = IoVec { base: addr as *mut c_void, len: bytes.len() };
    let n = unsafe { process_vm_writev(pid, &local, 1, &remote, 1, 0) };
    if n < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(n as usize)
    }
}

extern "C" {
    fn mount(
        src: *const c_char,
        target: *const c_char,
        fstype: *const c_char,
        flags: c_ulong,
        data: *const c_void,
    ) -> c_int;
}

pub fn mount_tracefs(target: &str) -> io::Result<()> {
    let src = b"tracefs\0";
    let fstype = b"tracefs\0";
    let t = std::ffi::CString::new(target)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "target has NUL"))?;
    let r = unsafe {
        mount(
            src.as_ptr() as *const c_char,
            t.as_ptr(),
            fstype.as_ptr() as *const c_char,
            0,
            std::ptr::null(),
        )
    };
    if r < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[repr(C)]
struct Group {
    name: *const c_char,
    passwd: *const c_char,
    gid: u32,
    members: *const *const c_char,
}

extern "C" {
    fn getgrnam(name: *const c_char) -> *const Group;
    fn chown(path: *const c_char, owner: u32, group: u32) -> c_int;
}

pub fn group_gid(name: &str) -> Option<u32> {
    let c = std::ffi::CString::new(name).ok()?;
    let g = unsafe { getgrnam(c.as_ptr()) };
    if g.is_null() {
        return None;
    }
    Some(unsafe { (*g).gid })
}

pub fn chown_path(path: &str, uid: u32, gid: u32) -> io::Result<()> {
    let p = std::ffi::CString::new(path)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path has NUL"))?;
    let r = unsafe { chown(p.as_ptr(), uid, gid) };
    if r < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

pub fn ioctl_val(fd: c_int, request: u64, arg: u64) -> io::Result<()> {
    let r = unsafe { ioctl(fd, request as c_ulong, arg as c_ulong) };
    if r < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[repr(C)]
pub struct PollFd {
    pub fd: c_int,
    pub events: i16,
    pub revents: i16,
}

pub const POLLIN: i16 = 0x0001;
pub const POLLERR: i16 = 0x0008;
pub const POLLHUP: i16 = 0x0010;

extern "C" {
    fn poll(fds: *mut PollFd, nfds: c_ulong, timeout: c_int) -> c_int;
    fn pipe(fds: *mut c_int) -> c_int;
    fn signal(signum: c_int, handler: usize) -> usize;
    fn write(fd: c_int, buf: *const c_void, count: usize) -> isize;
    fn read(fd: c_int, buf: *mut c_void, count: usize) -> isize;
    fn fcntl(fd: c_int, cmd: c_int, arg: c_int) -> c_int;
}

const F_GETFL: c_int = 3;
const F_SETFL: c_int = 4;
const O_NONBLOCK: c_int = 0o4000;

pub struct Wakeup {
    r: RawFd,
    w: RawFd,
}

impl Wakeup {

    pub fn new() -> Wakeup {
        let mut fds = [0 as c_int; 2];
        if unsafe { pipe(fds.as_mut_ptr()) } != 0 {
            return Wakeup { r: -1, w: -1 };
        }

        for &fd in &fds {
            let fl = unsafe { fcntl(fd, F_GETFL, 0) };
            if fl >= 0 {
                unsafe { fcntl(fd, F_SETFL, fl | O_NONBLOCK) };
            }
        }
        Wakeup { r: fds[0] as RawFd, w: fds[1] as RawFd }
    }

    pub fn read_fd(&self) -> RawFd {
        self.r
    }

    pub fn signal(&self) {
        if self.w < 0 {
            return;
        }
        let b = [1u8; 1];
        unsafe {
            write(self.w, b.as_ptr() as *const c_void, 1);
        }
    }

    pub fn drain(&self) {
        if self.r < 0 {
            return;
        }
        let mut b = [0u8; 64];
        loop {
            let n = unsafe { read(self.r, b.as_mut_ptr() as *mut c_void, b.len()) };
            if n <= 0 {
                break;
            }
        }
    }
}

impl Drop for Wakeup {
    fn drop(&mut self) {
        if self.r >= 0 {
            unsafe { close(self.r) };
        }
        if self.w >= 0 {
            unsafe { close(self.w) };
        }
    }
}

static WAKE_W: AtomicI32 = AtomicI32::new(-1);

extern "C" fn on_stop(_sig: c_int) {
    let fd = WAKE_W.load(Ordering::SeqCst);
    if fd >= 0 {
        let b = [0u8; 1];
        unsafe {
            write(fd, b.as_ptr() as *const c_void, 1);
        }
    }
}

pub fn install_stop_pipe() -> io::Result<RawFd> {
    let mut fds = [0 as c_int; 2];
    if unsafe { pipe(fds.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    WAKE_W.store(fds[1], Ordering::SeqCst);
    const SIGINT: c_int = 2;
    const SIGTERM: c_int = 15;
    let h = on_stop as extern "C" fn(c_int) as usize;
    unsafe {
        signal(SIGINT, h);
        signal(SIGTERM, h);
    }
    Ok(fds[0] as RawFd)
}

pub fn poll_readable(fds: &[RawFd]) -> io::Result<Option<usize>> {
    poll_readable_timeout(fds, -1)
}

pub fn poll_readable_timeout(fds: &[RawFd], timeout_ms: i32) -> io::Result<Option<usize>> {
    let mut pfds: Vec<PollFd> =
        fds.iter().map(|&fd| PollFd { fd, events: POLLIN, revents: 0 }).collect();
    let r = unsafe { poll(pfds.as_mut_ptr(), pfds.len() as c_ulong, timeout_ms) };
    if r < 0 {
        let e = io::Error::last_os_error();
        if e.kind() == io::ErrorKind::Interrupted {
            return Ok(None);
        }
        return Err(e);
    }
    let mut hup_only = false;
    for (i, p) in pfds.iter().enumerate() {
        if p.revents & POLLIN != 0 {

            return Ok(Some(i));
        }
        if p.revents & (POLLHUP | POLLERR) != 0 {
            hup_only = true;
        }
    }

    if hup_only {
        let nap = if timeout_ms > 0 { timeout_ms as u64 } else { 50 };
        std::thread::sleep(std::time::Duration::from_millis(nap));
    }
    Ok(None)
}

static G_TTY: AtomicI32 = AtomicI32::new(-1);
static G_CARD: AtomicI32 = AtomicI32::new(-1);
static G_KDSETMODE: AtomicU64 = AtomicU64::new(0);
static G_KD_TEXT: AtomicU64 = AtomicU64::new(0);
static G_DROP_MASTER: AtomicU64 = AtomicU64::new(0);
static G_STOP: AtomicBool = AtomicBool::new(false);

extern "C" {
    fn raise(sig: c_int) -> c_int;
}

const SIG_DFL: usize = 0;

extern "C" fn screen_guard(sig: c_int) {

    let tty = G_TTY.load(Ordering::SeqCst);
    if tty >= 0 {
        unsafe {
            ioctl(tty, G_KDSETMODE.load(Ordering::SeqCst) as c_ulong, G_KD_TEXT.load(Ordering::SeqCst) as c_ulong);
        }
    }
    let card = G_CARD.load(Ordering::SeqCst);
    if card >= 0 {
        unsafe {
            ioctl(card, G_DROP_MASTER.load(Ordering::SeqCst) as c_ulong, 0);
        }
    }
    const SIGINT: c_int = 2;
    const SIGTERM: c_int = 15;
    if sig == SIGINT || sig == SIGTERM {
        G_STOP.store(true, Ordering::SeqCst);
    } else {

        unsafe {
            signal(sig, SIG_DFL);
            raise(sig);
        }
    }
}

pub fn install_screen_guard(tty_fd: i32, kdsetmode: u64, kd_text: u64, card_fd: i32, drop_master: u64) {
    G_TTY.store(tty_fd, Ordering::SeqCst);
    G_CARD.store(card_fd, Ordering::SeqCst);
    G_KDSETMODE.store(kdsetmode, Ordering::SeqCst);
    G_KD_TEXT.store(kd_text, Ordering::SeqCst);
    G_DROP_MASTER.store(drop_master, Ordering::SeqCst);
    let h = screen_guard as extern "C" fn(c_int) as usize;

    for sig in [2, 15, 11  , 6  , 7  , 8  , 4  ] {
        unsafe {
            signal(sig, h);
        }
    }
}

pub fn screen_guard_stop() -> bool {
    G_STOP.load(Ordering::SeqCst)
}

pub fn set_guard_card(card_fd: i32) {
    G_CARD.store(card_fd, Ordering::SeqCst);
}

extern "C" {
    fn socket(domain: c_int, ty: c_int, protocol: c_int) -> c_int;
    fn sendto(fd: c_int, buf: *const c_void, len: usize, flags: c_int, addr: *const c_void, addrlen: u32) -> isize;
    fn close(fd: c_int) -> c_int;
}

const AF_UNIX: c_int = 1;
const SOCK_DGRAM: c_int = 2;

#[repr(C)]
struct SockaddrUn {
    family: u16,
    path: [u8; 108],
}

pub fn sd_notify(state: &str) {
    let Ok(sock) = std::env::var("NOTIFY_SOCKET") else {
        return;
    };
    if sock.is_empty() {
        return;
    }
    let fd = unsafe { socket(AF_UNIX, SOCK_DGRAM, 0) };
    if fd < 0 {
        return;
    }
    let mut addr = SockaddrUn { family: AF_UNIX as u16, path: [0; 108] };
    let raw = sock.as_bytes().to_vec();
    let abstract_ns = raw.first() == Some(&b'@');
    let n = raw.len().min(108);
    addr.path[..n].copy_from_slice(&raw[..n]);
    if abstract_ns {
        addr.path[0] = 0;
    }

    let addrlen = (2 + n + if abstract_ns { 0 } else { 1 }) as u32;
    let r = unsafe {
        sendto(fd, state.as_ptr() as *const c_void, state.len(), 0, &addr as *const _ as *const c_void, addrlen)
    };
    if std::env::var_os("PHANTOM_NOTIFY_DEBUG").is_some() {
        eprintln!(
            "sd_notify({state:?}): sock={sock:?} abstract={abstract_ns} n={n} addrlen={addrlen} sendto={r} err={}",
            io::Error::last_os_error()
        );
    }
    unsafe {
        close(fd);
    }
}

pub fn watchdog_interval() -> Option<std::time::Duration> {
    std::env::var("WATCHDOG_USEC").ok().and_then(|s| s.parse::<u64>().ok()).map(|us| std::time::Duration::from_micros(us / 2))
}

const HDR: usize = std::mem::size_of::<CmsgHdr>();

#[inline]
fn align8(n: usize) -> usize {
    (n + 7) & !7
}

#[repr(align(8))]
struct Control([u8; 256]);

pub fn recv_with_fds(fd: c_int, buf: &mut [u8], fds: &mut Vec<c_int>) -> io::Result<usize> {
    let mut iov = IoVec { base: buf.as_mut_ptr() as *mut c_void, len: buf.len() };
    let mut ctrl = Control([0u8; 256]);
    let mut msg = MsgHdr {
        name: std::ptr::null_mut(),
        namelen: 0,
        iov: &mut iov,
        iovlen: 1,
        control: ctrl.0.as_mut_ptr() as *mut c_void,
        controllen: ctrl.0.len(),
        flags: 0,
    };
    let n = unsafe { recvmsg(fd, &mut msg, 0) };
    if n < 0 {
        return Err(io::Error::last_os_error());
    }

    let mut off = 0usize;
    while off + HDR <= msg.controllen {
        let c = unsafe { &*(ctrl.0.as_ptr().add(off) as *const CmsgHdr) };

        if c.len < HDR || c.len > msg.controllen - off {
            break;
        }
        if c.level == SOL_SOCKET && c.ctype == SCM_RIGHTS {
            let data = off + align8(HDR);
            let nbytes = c.len - align8(HDR);

            let avail = ctrl.0.len().saturating_sub(data);
            let count = nbytes.min(avail) / 4;
            for i in 0..count {
                let p = data + i * 4;
                let raw = [ctrl.0[p], ctrl.0[p + 1], ctrl.0[p + 2], ctrl.0[p + 3]];
                fds.push(c_int::from_ne_bytes(raw));
            }
        }
        off += align8(c.len);
    }
    Ok(n as usize)
}

pub fn send_with_fds(fd: c_int, buf: &[u8], fds: &[c_int]) -> io::Result<()> {

    if fds.len() > 60 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "too many fds in one message"));
    }
    let mut sent = 0usize;
    let mut first = true;
    while sent < buf.len() {
        let mut iov = IoVec {
            base: buf[sent..].as_ptr() as *mut c_void,
            len: buf.len() - sent,
        };
        let mut ctrl = Control([0u8; 256]);
        let (control, controllen) = if first && !fds.is_empty() {
            let datalen = fds.len() * 4;
            unsafe {
                let c = ctrl.0.as_mut_ptr() as *mut CmsgHdr;
                (*c).len = align8(HDR) + datalen;
                (*c).level = SOL_SOCKET;
                (*c).ctype = SCM_RIGHTS;
            }
            let base = align8(HDR);
            for (i, f) in fds.iter().enumerate() {
                ctrl.0[base + i * 4..base + i * 4 + 4].copy_from_slice(&f.to_ne_bytes());
            }
            (ctrl.0.as_mut_ptr() as *mut c_void, align8(HDR) + align8(datalen))
        } else {
            (std::ptr::null_mut(), 0usize)
        };
        let msg = MsgHdr {
            name: std::ptr::null_mut(),
            namelen: 0,
            iov: &mut iov,
            iovlen: 1,
            control,
            controllen,
            flags: 0,
        };
        let n = unsafe { sendmsg(fd, &msg, MSG_NOSIGNAL) };
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        sent += n as usize;
        first = false;
    }
    Ok(())
}
