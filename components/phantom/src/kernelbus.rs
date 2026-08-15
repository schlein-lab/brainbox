use crate::kernel::KernelEvent;
use crate::sys;

use std::collections::VecDeque;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

const RING_CAP: usize = 65_536;

const MAX_DROPS_BEFORE_KICK: u64 = RING_CAP as u64 * 16;

const DROP_REPORT_EVERY: u64 = 1024;

static SEQ: AtomicU64 = AtomicU64::new(0);

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

#[derive(Clone, Default)]
pub struct Filter {

    patterns: Vec<String>,

    only_pid: Option<i32>,

    human: bool,

    replay: usize,
}

impl Filter {

    fn matches(&self, ev: &KernelEvent) -> bool {
        if let Some(want) = self.only_pid {
            if ev.pid != want {
                return false;
            }
        }
        if self.patterns.is_empty() {
            return true;
        }

        let sc = ev.event.strip_prefix("sys_enter_").or_else(|| ev.event.strip_prefix("sys_exit_"));
        let aliases = event_aliases(ev.event);
        self.patterns.iter().any(|p| {
            glob_match(p, ev.verb)
                || glob_match(p, ev.event)
                || sc.is_some_and(|s| glob_match(p, s))
                || aliases.iter().any(|a| glob_match(p, a))
        })
    }
}

fn event_aliases(event: &str) -> &'static [&'static str] {
    match event {
        "sched_process_exec" => &["execve", "execveat", "exec"],
        "sched_process_fork" => &["fork", "vfork", "clone", "clone3"],
        "sched_process_exit" => &["exit", "exit_group"],
        "phantom_open" => &["open", "openat", "openat2"],
        _ => &[],
    }
}

fn glob_match(pat: &str, hay: &str) -> bool {
    match (pat.strip_prefix('*'), pat.strip_suffix('*')) {
        (Some(inner), Some(_)) => {

            let inner = inner.strip_suffix('*').unwrap_or(inner);
            inner.is_empty() || contains_ci(hay, inner)
        }
        (Some(suf), None) => ends_with_ci(hay, suf),
        (None, Some(pre)) => starts_with_ci(hay, pre),
        (None, None) => pat.eq_ignore_ascii_case(hay),
    }
}

fn starts_with_ci(hay: &str, pre: &str) -> bool {
    let (h, p) = (hay.as_bytes(), pre.as_bytes());
    h.len() >= p.len() && h[..p.len()].eq_ignore_ascii_case(p)
}
fn ends_with_ci(hay: &str, suf: &str) -> bool {
    let (h, s) = (hay.as_bytes(), suf.as_bytes());
    h.len() >= s.len() && h[h.len() - s.len()..].eq_ignore_ascii_case(s)
}
fn contains_ci(hay: &str, needle: &str) -> bool {
    let (h, n) = (hay.as_bytes(), needle.as_bytes());
    n.is_empty() || (h.len() >= n.len() && h.windows(n.len()).any(|w| w.eq_ignore_ascii_case(n)))
}

pub fn parse_hello(line: &str) -> Filter {
    let line = line.trim();
    let mut f = Filter::default();

    if !line.starts_with('{') || !line.contains("\"hello\"") {
        return f;
    }

    if let Some(v) = json_str(line, "mode") {
        f.human = v.eq_ignore_ascii_case("human");
    }

    if let Some(v) = json_num(line, "pid") {
        if v > 0 && v <= i32::MAX as i64 {
            f.only_pid = Some(v as i32);
        }
    }

    if let Some(v) = json_num(line, "replay") {
        f.replay = v.clamp(0, RING_CAP as i64) as usize;
    }

    f.patterns = json_str_array(line, "filter");

    let _capability_token = json_str(line, "token");

    f
}

fn json_str(obj: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let i = obj.find(&needle)?;
    let after = obj[i + needle.len()..].trim_start();
    let after = after.strip_prefix(':')?.trim_start();
    let body = after.strip_prefix('"')?;
    let mut out = String::new();
    let mut chars = body.chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return Some(out),
            '\\' => match chars.next() {
                Some('n') => out.push('\n'),
                Some('t') => out.push('\t'),
                Some('r') => out.push('\r'),
                Some(other) => out.push(other),
                None => break,
            },
            _ => out.push(c),
        }
    }
    None
}

fn json_num(obj: &str, key: &str) -> Option<i64> {
    let needle = format!("\"{key}\"");
    let i = obj.find(&needle)?;
    let after = obj[i + needle.len()..].trim_start();
    let after = after.strip_prefix(':')?.trim_start();
    let end = after.find(|c: char| !c.is_ascii_digit() && c != '-').unwrap_or(after.len());
    after[..end].parse().ok()
}

fn json_str_array(obj: &str, key: &str) -> Vec<String> {
    let needle = format!("\"{key}\"");
    let Some(i) = obj.find(&needle) else {
        return Vec::new();
    };
    let after = obj[i + needle.len()..].trim_start();
    let Some(after) = after.strip_prefix(':') else {
        return Vec::new();
    };
    let after = after.trim_start();
    let Some(after) = after.strip_prefix('[') else {
        return Vec::new();
    };
    let end = after.find(']').unwrap_or(after.len());
    let mut out = Vec::new();

    let mut rest = &after[..end];
    while let Some(q) = rest.find('"') {
        rest = &rest[q + 1..];
        match rest.find('"') {
            Some(e) => {
                if !rest[..e].is_empty() {
                    out.push(rest[..e].to_string());
                }
                rest = &rest[e + 1..];
            }
            None => break,
        }

        if out.len() >= 256 {
            break;
        }
    }
    out
}

struct Sub {

    inner: Mutex<SubState>,
    cv: Condvar,
    filter: Filter,
}

struct SubState {
    ring: VecDeque<String>,

    dropped: u64,

    pending_drop: u64,

    closed: bool,
}

impl Sub {
    fn new(filter: Filter) -> Arc<Sub> {
        Arc::new(Sub {
            inner: Mutex::new(SubState {
                ring: VecDeque::new(),
                dropped: 0,
                pending_drop: 0,
                closed: false,
            }),
            cv: Condvar::new(),
            filter,
        })
    }

    fn push(&self, line: &str) -> bool {
        let mut st = lock(&self.inner);
        if st.closed {
            return false;
        }
        if st.ring.len() >= RING_CAP {

            st.ring.pop_front();
            st.dropped = st.dropped.saturating_add(1);
            st.pending_drop = st.pending_drop.saturating_add(1);
            if st.dropped >= MAX_DROPS_BEFORE_KICK {
                st.closed = true;
                self.cv.notify_one();
                return false;
            }
        }
        st.ring.push_back(line.to_string());
        drop(st);
        self.cv.notify_one();
        true
    }

    fn drain_drop_report(&self) -> Option<String> {
        let mut st = lock(&self.inner);
        if st.pending_drop == 0 {
            return None;
        }
        let n = st.pending_drop;
        st.pending_drop = 0;

        Some(format!("{{\"type\":\"_dropped\",\"count\":{n}}}\n"))
    }

    fn close(&self) {
        lock(&self.inner).closed = true;
        self.cv.notify_one();
    }
}

fn writer_loop(sub: Arc<Sub>, mut stream: UnixStream) {
    loop {

        let batch: Vec<String> = {
            let mut st = lock(&sub.inner);
            while st.ring.is_empty() && !st.closed {
                st = match sub.cv.wait(st) {
                    Ok(g) => g,
                    Err(p) => p.into_inner(),
                };
            }
            if st.closed && st.ring.is_empty() {
                return;
            }
            st.ring.drain(..).collect()
        };
        for line in &batch {
            if stream.write_all(line.as_bytes()).is_err() {
                sub.close();
                return;
            }
        }

        let _ = stream.flush();
    }
}

pub struct DiskRing {
    inner: Mutex<RingState>,

    path: Option<String>,

    byte_cap: usize,
}

struct RingState {
    lines: VecDeque<String>,
    bytes: usize,

    dirty: usize,
}

pub const DEFAULT_RING_BYTES: usize = 8 * 1024 * 1024;

impl DiskRing {
    pub fn new(path: Option<String>, byte_cap: usize) -> DiskRing {
        DiskRing {
            inner: Mutex::new(RingState { lines: VecDeque::new(), bytes: 0, dirty: 0 }),
            path,
            byte_cap: byte_cap.max(64 * 1024),
        }
    }

    fn append(&self, line: &str) {
        let mut st = lock(&self.inner);
        st.lines.push_back(line.to_string());
        st.bytes = st.bytes.saturating_add(line.len());
        while st.bytes > self.byte_cap && st.lines.len() > 1 {
            if let Some(old) = st.lines.pop_front() {
                st.bytes = st.bytes.saturating_sub(old.len());
            }
        }
        st.dirty += 1;

        if st.dirty >= 256 {
            st.dirty = 0;
            let snapshot: String = st.lines.iter().cloned().collect();
            drop(st);
            self.flush_to_disk(&snapshot);
        }
    }

    fn tail(&self, n: usize) -> Vec<String> {
        let st = lock(&self.inner);
        let start = st.lines.len().saturating_sub(n);
        st.lines.iter().skip(start).cloned().collect()
    }

    fn flush_to_disk(&self, snapshot: &str) {
        let Some(path) = &self.path else { return };
        let tmp = format!("{path}.tmp");
        if std::fs::write(&tmp, snapshot.as_bytes()).is_ok() {
            let _ = std::fs::rename(&tmp, path);
        }
    }
}

pub struct Bus {
    subs: Mutex<Vec<Arc<Sub>>>,
    ring: DiskRing,

    published: AtomicU64,

    replay_enabled: AtomicBool,
}

impl Bus {
    pub fn new(ring_path: Option<String>, ring_bytes: usize) -> Arc<Bus> {
        let replay = ring_path.is_some();
        Arc::new(Bus {
            subs: Mutex::new(Vec::new()),
            ring: DiskRing::new(ring_path, ring_bytes),
            published: AtomicU64::new(0),
            replay_enabled: AtomicBool::new(replay),
        })
    }

    fn json_line(ev: &KernelEvent, seq: u64, ts: f64) -> String {
        let KernelEvent { pid, comm, verb, event, info } = *ev;
        format!(
            "{{\"seq\":{seq},\"ts\":{ts:.3},\"pid\":{pid},\"comm\":{comm:?},\
             \"verb\":{verb:?},\"event\":{event:?},\"info\":{info:?}}}\n"
        )
    }

    fn human_line(ev: &KernelEvent) -> String {
        format!("KRN   {:<5} {:>7} {:<16} {}\n", ev.verb, ev.pid, ev.comm, ev.info)
    }

    pub fn publish(&self, ev: &KernelEvent) {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let ts = now_secs();

        let json = Self::json_line(ev, seq, ts);
        let mut human: Option<String> = None;

        self.ring.append(&json);

        let report = self.published.fetch_add(1, Ordering::Relaxed) % DROP_REPORT_EVERY == 0;

        let mut subs = lock(&self.subs);

        subs.retain_mut(|sub| {
            if !sub.filter.matches(ev) {

                if report {
                    if let Some(line) = sub.drain_drop_report() {
                        return sub.push(&line);
                    }
                }
                return true;
            }

            if report {
                if let Some(line) = sub.drain_drop_report() {
                    if !sub.push(&line) {
                        return false;
                    }
                }
            }
            let line = if sub.filter.human {
                human.get_or_insert_with(|| Self::human_line(ev)).as_str()
            } else {
                json.as_str()
            };
            sub.push(line)
        });
    }

    pub fn add_subscriber(self: &Arc<Bus>, mut stream: UnixStream) {
        let filter = read_hello(&mut stream);

        if filter.replay > 0 {
            if !self.replay_enabled.load(Ordering::Relaxed) {

                let _ = stream.write_all(
                    b"{\"type\":\"_replay\",\"available\":false,\"count\":0}\n",
                );
            } else {
                let tail = self.ring.tail(filter.replay);
                let _ = stream.write_all(
                    format!("{{\"type\":\"_replay\",\"available\":true,\"count\":{}}}\n", tail.len())
                        .as_bytes(),
                );

                let mut ok = true;
                for line in tail {
                    if stream.write_all(line.as_bytes()).is_err() {
                        ok = false;
                        break;
                    }
                }
                if !ok {
                    return;
                }
            }
        }

        let sub = Sub::new(filter);
        let writer_sub = sub.clone();
        let ws = match stream.try_clone() {
            Ok(s) => s,
            Err(_) => return,
        };
        std::thread::spawn(move || writer_loop(writer_sub, ws));
        lock(&self.subs).push(sub);
    }

    pub fn serve(self: Arc<Bus>, path: &str, group: &str) {
        let _ = std::fs::remove_file(path);
        let listener = match std::os::unix::net::UnixListener::bind(path) {
            Ok(l) => l,
            Err(e) => {
                eprintln!("phantom-kerneld: cannot bind bus {path}: {e}");
                return;
            }
        };
        gate_socket(path, group);
        for conn in listener.incoming() {
            let Ok(stream) = conn else { continue };
            self.add_subscriber(stream);
        }
    }
}

fn read_hello(stream: &mut UnixStream) -> Filter {
    use std::os::fd::AsRawFd;
    let fd = stream.as_raw_fd();

    match sys::poll_readable_timeout(&[fd], 200) {
        Ok(Some(_)) => {}
        _ => return Filter::default(),
    }
    let mut buf = [0u8; 4096];
    match stream.read(&mut buf) {
        Ok(0) | Err(_) => Filter::default(),
        Ok(n) => {

            let text = String::from_utf8_lossy(&buf[..n]);
            let first = text.split('\n').next().unwrap_or("");
            parse_hello(first)
        }
    }
}

fn gate_socket(path: &str, group: &str) {
    use std::os::unix::fs::PermissionsExt;
    if let Err(e) = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o660)) {
        eprintln!("phantom-kerneld: chmod 0660 {path}: {e} (continuing)");
    }
    match sys::group_gid(group) {
        Some(gid) => {

            if let Err(e) = sys::chown_path(path, 0, gid) {
                eprintln!("phantom-kerneld: chown root:{group} {path}: {e} (continuing)");
            }
        }
        None => {
            eprintln!(
                "phantom-kerneld: group {group:?} not found — socket left at default \
                 ownership; create it with `groupadd -f {group}` (continuing)"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev<'a>(pid: i32, comm: &'a str, verb: &'static str, event: &'a str) -> KernelEvent<'a> {
        KernelEvent { pid, comm, verb, event, info: "" }
    }

    #[test]
    fn glob_exact_prefix_suffix_contains() {
        assert!(glob_match("EXEC", "EXEC"));
        assert!(glob_match("exec", "EXEC"));
        assert!(!glob_match("EXEC", "EXIT"));
        assert!(glob_match("open*", "openat"));
        assert!(!glob_match("open*", "close"));
        assert!(glob_match("*connect", "sys_enter_connect"));
        assert!(glob_match("*enter*", "sys_enter_connect"));
        assert!(glob_match("*", "anything"));
    }

    #[test]
    fn filter_matches_real_tap_vocabulary() {

        let exec = ev(7, "bash", "EXEC", "sched_process_exec");
        let open = ev(7, "bash", "OPEN", "phantom_open");
        let conn = ev(7, "curl", "SYS", "sys_enter_connect");

        assert!(parse_hello(r#"{"hello":1,"filter":["execve"]}"#).matches(&exec));
        assert!(parse_hello(r#"{"hello":1,"filter":["open"]}"#).matches(&open));
        assert!(parse_hello(r#"{"hello":1,"filter":["connect"]}"#).matches(&conn));

        assert!(parse_hello(r#"{"hello":1,"filter":["exec*"]}"#).matches(&exec));
        assert!(parse_hello(r#"{"hello":1,"filter":["*EXEC*"]}"#).matches(&exec));

        assert!(!parse_hello(r#"{"hello":1,"filter":["execve"]}"#).matches(&open));
    }

    #[test]
    fn hello_filter_matches_verb_event_and_syscall_name() {

        let f = parse_hello(r#"{"hello":1,"filter":["EXEC","open*","connect"]}"#);
        assert!(f.matches(&ev(1, "sh", "EXEC", "sched_process_exec")));
        assert!(f.matches(&ev(1, "sh", "OPEN", "openat")));
        assert!(f.matches(&ev(1, "curl", "SYS", "sys_enter_connect")));
        assert!(!f.matches(&ev(1, "sh", "EXIT", "sched_process_exit")));
    }

    #[test]
    fn empty_or_non_hello_means_match_all() {

        assert!(Filter::default().matches(&ev(1, "x", "EXIT", "sched_process_exit")));

        let f = parse_hello("not json at all");
        assert!(f.patterns.is_empty() && f.only_pid.is_none() && !f.human);
        assert!(f.matches(&ev(1, "x", "EXIT", "sched_process_exit")));
    }

    #[test]
    fn hello_pid_mode_replay_and_token_seam() {
        let f = parse_hello(r#"{"hello":1,"mode":"human","pid":4711,"replay":200,"token":"abc"}"#);
        assert!(f.human);
        assert_eq!(f.only_pid, Some(4711));
        assert_eq!(f.replay, 200);

        assert!(f.matches(&ev(4711, "x", "EXEC", "sched_process_exec")));
        assert!(!f.matches(&ev(4712, "x", "EXEC", "sched_process_exec")));

        assert_eq!(json_str(r#"{"hello":1,"token":"abc"}"#, "token").as_deref(), Some("abc"));
    }

    #[test]
    fn replay_is_clamped_to_ring_cap() {
        let f = parse_hello(&format!(r#"{{"hello":1,"replay":{}}}"#, RING_CAP * 100));
        assert_eq!(f.replay, RING_CAP);
        let f = parse_hello(r#"{"hello":1,"replay":-5}"#);
        assert_eq!(f.replay, 0);
    }

    #[test]
    fn json_str_array_is_bounded_and_handles_escapes() {
        let v = json_str_array(r#"{"filter":["a","b","c"]}"#, "filter");
        assert_eq!(v, vec!["a", "b", "c"]);

        assert!(json_str_array(r#"{"x":1}"#, "filter").is_empty());
    }

    #[test]
    fn disk_ring_stays_within_byte_budget() {

        let ring = DiskRing::new(None, 64 * 1024);
        for i in 0..10_000 {
            ring.append(&format!("line {i} with some padding to take up bytes\n"));
        }
        let st = lock(&ring.inner);
        assert!(st.bytes <= ring.byte_cap, "bytes {} > cap {}", st.bytes, ring.byte_cap);
        assert!(!st.lines.is_empty(), "ring should retain the most recent lines");
    }

    #[test]
    fn disk_ring_tail_returns_last_n_oldest_first() {
        let ring = DiskRing::new(None, DEFAULT_RING_BYTES);
        for i in 0..100 {
            ring.append(&format!("{i}\n"));
        }
        let tail = ring.tail(3);
        assert_eq!(tail, vec!["97\n", "98\n", "99\n"]);

        assert_eq!(ring.tail(10_000).len(), 100);
    }

    #[test]
    fn ring_overflow_drops_oldest_and_counts() {
        let sub = Sub::new(Filter::default());

        for i in 0..RING_CAP {
            assert!(sub.push(&format!("{i}\n")));
        }
        assert!(sub.push("overflow\n"));
        let report = sub.drain_drop_report().expect("a drop should be pending");
        assert!(report.contains("\"_dropped\"") && report.contains("\"count\":1"));

        assert!(sub.drain_drop_report().is_none());
    }
}
