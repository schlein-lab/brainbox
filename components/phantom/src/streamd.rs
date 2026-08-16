#![allow(dead_code)]

use crate::headless_fb::FrameTap;
use crate::jpeg;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub trait SceneSource: Send + Sync {

    
    
    
    fn scene_frame(&self, force_keyframe: bool, leser: &str) -> Option<Vec<u8>>;

    fn seat_generation(&self) -> u32;

    fn screen_size(&self) -> (u16, u16);

    fn placement_json(&self, query: &str) -> String;
}

pub struct Config {
    pub min_fps: u32,
    pub max_fps: u32,
    pub hard_cap_fps: u32,

    pub quality: u32,

    pub idle_after: Duration,

    pub stuck_after: Duration,
}

impl Default for Config {
    fn default() -> Config {
        Config {

            min_fps: 6,
            max_fps: 20,
            hard_cap_fps: 24,
            quality: 60,
            idle_after: Duration::from_millis(1500),
            stuck_after: Duration::from_secs(5),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {

    Working,

    Idle,

    Stuck,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Working => "WORKING",
            State::Idle => "IDLE",
            State::Stuck => "STUCK",
        }
    }
    fn code(self) -> u8 {
        match self {
            State::Working => 0,
            State::Idle => 1,
            State::Stuck => 2,
        }
    }
    fn from_code(c: u8) -> State {
        match c {
            0 => State::Working,
            2 => State::Stuck,
            _ => State::Idle,
        }
    }

    pub fn parse_verdict(s: &str) -> Option<State> {
        match s.trim().to_ascii_uppercase().as_str() {
            "WORKING" => Some(State::Working),
            "IDLE" => Some(State::Idle),
            "STUCK" => Some(State::Stuck),
            _ => None,
        }
    }
}

#[derive(Clone)]
pub struct WatchdogVerdict {
    inner: Arc<AtomicU8>,
}

impl Default for WatchdogVerdict {
    fn default() -> WatchdogVerdict {
        WatchdogVerdict { inner: Arc::new(AtomicU8::new(255)) }
    }
}

impl WatchdogVerdict {

    pub fn set(&self, v: Option<State>) {
        self.inner.store(v.map(|s| s.code()).unwrap_or(255), Ordering::Relaxed);
    }

    pub fn get(&self) -> Option<State> {
        match self.inner.load(Ordering::Relaxed) {
            255 => None,
            c => Some(State::from_code(c)),
        }
    }
}

pub fn default_verdict_path() -> String {
    std::env::var("PHANTOM_WATCHDOG_VERDICT").unwrap_or_else(|_| {
        match std::env::var("XDG_RUNTIME_DIR") {
            Ok(d) if !d.is_empty() => format!("{d}/phantom-watchdog.verdict"),
            _ => "/tmp/phantom-watchdog.verdict".to_string(),
        }
    })
}

pub fn watch_verdict_file(path: String, verdict: WatchdogVerdict, interval: Duration) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut last: Option<std::time::SystemTime> = None;
        let mut last_set: Option<State> = None;
        loop {

            let mt = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
            if mt != last {
                last = mt;
                let token = std::fs::read_to_string(&path).unwrap_or_default();
                let v = State::parse_verdict(&token);
                if v != last_set {
                    verdict.set(v);
                    last_set = v;
                    eprintln!(
                        "streamd: watchdog verdict <- {:?} (from {path})",
                        v.map(|s| s.as_str()).unwrap_or("UNKNOWN")
                    );
                }
            } else if mt.is_none() && last_set.is_some() {

                verdict.set(None);
                last_set = None;
                eprintln!("streamd: watchdog verdict cleared (file gone: {path})");
            }
            std::thread::sleep(interval);
        }
    })
}

#[derive(Clone)]
pub struct LiveState {
    inner: Arc<Mutex<LiveInner>>,
}
struct LiveInner {
    state: State,
    last_change_gen: u64,
    last_seen_gen: u64,
}
impl Default for LiveState {
    fn default() -> LiveState {
        LiveState { inner: Arc::new(Mutex::new(LiveInner { state: State::Idle, last_change_gen: 0, last_seen_gen: 0 })) }
    }
}
impl LiveState {
    fn publish(&self, state: State, gen: u64, changed: bool) {
        if let Ok(mut s) = self.inner.lock() {
            s.state = state;
            s.last_seen_gen = gen;
            if changed {
                s.last_change_gen = gen;
            }
        }
    }

    fn probe(&self, tap_gen: u64) -> State {
        if let Ok(s) = self.inner.lock() {
            if tap_gen > s.last_seen_gen {
                return State::Working;
            }
            return s.state;
        }
        State::Idle
    }
}

fn seam_fingerprint(rgba: &[u8], w: u32, h: u32) -> u64 {
    if rgba.is_empty() || w == 0 || h == 0 {
        return 0;
    }
    let w = w as usize;
    let h = h as usize;

    let sx = (w / 32).max(1);
    let sy = (h / 18).max(1);
    let mut acc: u64 = 1469598103934665603;
    let mut y = 0;
    while y < h {
        let mut x = 0;
        while x < w {
            let i = (y * w + x) * 4;
            if i + 3 <= rgba.len() {

                acc ^= rgba[i] as u64;
                acc = acc.wrapping_mul(1099511628211);
                acc ^= rgba[i + 1] as u64;
                acc = acc.wrapping_mul(1099511628211);
                acc ^= rgba[i + 2] as u64;
                acc = acc.wrapping_mul(1099511628211);
            }
            x += sx;
        }
        y += sy;
    }
    acc
}

pub fn nonblank_fraction(rgba: &[u8], w: u32, h: u32) -> f32 {
    if rgba.is_empty() || w == 0 || h == 0 {
        return 0.0;
    }
    let w = w as usize;
    let h = h as usize;
    let sx = (w / 64).max(1);
    let sy = (h / 36).max(1);
    let (mut total, mut lit) = (0u64, 0u64);
    let mut y = 0;
    while y < h {
        let mut x = 0;
        while x < w {
            let i = (y * w + x) * 4;
            if i + 3 <= rgba.len() {
                total += 1;
                if rgba[i] > 8 || rgba[i + 1] > 8 || rgba[i + 2] > 8 {
                    lit += 1;
                }
            }
            x += sx;
        }
        y += sy;
    }
    if total == 0 {
        0.0
    } else {
        lit as f32 / total as f32
    }
}

pub fn spawn(
    tap: FrameTap,
    addr: String,
    cfg: Config,
    scene: Option<Arc<dyn SceneSource>>,
) -> std::io::Result<WatchdogVerdict> {
    let listener = TcpListener::bind(&addr)?;
    eprintln!("streamd: MJPEG on http://{addr}/stream  (state at /state) — display.md §7 lane");
    if scene.is_some() {
        eprintln!("streamd: client-composite lane on http://{addr}/scene.bin  (placement at /placement)");
    }
    let verdict = WatchdogVerdict::default();
    let vd = verdict.clone();
    let live = LiveState::default();

    let vpath = default_verdict_path();
    eprintln!("streamd: watchdog verdict file = {vpath} (write WORKING|IDLE|STUCK|UNKNOWN)");
    let _watcher = watch_verdict_file(vpath, verdict.clone(), Duration::from_millis(500));
    std::thread::spawn(move || serve_blocking(listener, tap, cfg, vd, live, scene));
    Ok(verdict)
}

pub fn serve_blocking(
    listener: TcpListener,
    tap: FrameTap,
    cfg: Config,
    verdict: WatchdogVerdict,
    live: LiveState,
    scene: Option<Arc<dyn SceneSource>>,
) {
    let cfg = Arc::new(cfg);
    for conn in listener.incoming() {
        let stream = match conn {
            Ok(s) => s,
            Err(_) => continue,
        };
        let tap = tap.clone();
        let cfg = cfg.clone();
        let verdict = verdict.clone();
        let live = live.clone();
        let scene = scene.clone();
        std::thread::spawn(move || {
            if let Err(e) = handle_client(stream, tap, &cfg, verdict, live, scene) {
                eprintln!("streamd: client closed: {e}");
            }
        });
    }
}

fn read_request_line(stream: &mut TcpStream) -> std::io::Result<String> {
    let mut buf = Vec::with_capacity(256);
    let mut byte = [0u8; 1];

    loop {
        let n = stream.read(&mut byte)?;
        if n == 0 {
            break;
        }
        if byte[0] == b'\n' {
            break;
        }
        if byte[0] != b'\r' {
            buf.push(byte[0]);
        }
        if buf.len() > 8192 {
            break;
        }
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn graceful_finish(stream: &mut TcpStream) {
    let _ = stream.flush();

    let _ = stream.shutdown(Shutdown::Write);

    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let mut sink = [0u8; 4096];
    loop {
        match stream.read(&mut sink) {
            Ok(0) => break,
            Ok(_) => continue,
            Err(_) => break,
        }
    }
}

fn handle_client(
    mut stream: TcpStream,
    tap: FrameTap,
    cfg: &Config,
    verdict: WatchdogVerdict,
    live: LiveState,
    scene: Option<Arc<dyn SceneSource>>,
) -> std::io::Result<()> {
    let req = read_request_line(&mut stream)?;
    let target = req.split_whitespace().nth(1).unwrap_or("/");

    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p, q),
        None => (target, ""),
    };

    if path == "/scene.bin" {
        let r = match &scene {
            Some(src) => serve_scene_bin(&mut stream, src.as_ref(), query),
            None => serve_503(&mut stream, "scene source not wired"),
        };
        graceful_finish(&mut stream);
        return r;
    }
    if path == "/placement" {
        let r = match &scene {
            Some(src) => serve_placement(&mut stream, src.as_ref(), query),
            None => serve_503(&mut stream, "scene source not wired"),
        };
        graceful_finish(&mut stream);
        return r;
    }

    if path.starts_with("/state") {
        let r = serve_state(&mut stream, &tap, &verdict, &live);
        graceful_finish(&mut stream);
        return r;
    }

    if path.starts_with("/frame.rgba") {
        let r = serve_frame_rgba(&mut stream, &tap);
        graceful_finish(&mut stream);
        return r;
    }
    if path == "/" || path.starts_with("/view") {
        let r = serve_index(&mut stream);
        graceful_finish(&mut stream);
        return r;
    }

    serve_mjpeg(stream, tap, cfg, verdict, live)
}

fn serve_frame_rgba(stream: &mut TcpStream, tap: &FrameTap) -> std::io::Result<()> {
    match tap.snapshot() {
        Some((rgba, w, h, gen)) => {
            let header = format!(
                "HTTP/1.1 200 OK\r\n\
                 Content-Type: application/octet-stream\r\n\
                 X-Phantom-W: {w}\r\nX-Phantom-H: {h}\r\nX-Phantom-Gen: {gen}\r\n\
                 Content-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
                rgba.len()
            );
            stream.write_all(header.as_bytes())?;
            stream.write_all(&rgba)
        }
        None => {
            let body = "no frame presented yet\n";
            let resp = format!(
                "HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n\
                 Content-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(), body
            );
            stream.write_all(resp.as_bytes())
        }
    }
}

fn serve_503(stream: &mut TcpStream, msg: &str) -> std::io::Result<()> {
    let body = format!("{msg}\n");
    let resp = format!(
        "HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n\
         Content-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    stream.write_all(resp.as_bytes())
}

fn serve_scene_bin(stream: &mut TcpStream, scene: &dyn SceneSource, query: &str) -> std::io::Result<()> {
    let force_keyframe = query
        .split('&')
        .filter_map(|kv| kv.split_once('='))
        .any(|(k, v)| k == "keyframe" && (v == "1" || v.eq_ignore_ascii_case("true")));
    let leser = query
        .split('&')
        .filter_map(|kv| kv.split_once('='))
        .find(|(k, _)| *k == "leser")
        .map(|(_, v)| v)
        .unwrap_or("_legacy");
    match scene.scene_frame(force_keyframe, leser) {
        Some(bytes) => {
            let (w, h) = scene.screen_size();
            let gen = scene.seat_generation();
            let header = format!(
                "HTTP/1.1 200 OK\r\n\
                 Content-Type: application/octet-stream\r\n\
                 X-Phantom-Gen: {gen}\r\nX-Phantom-W: {w}\r\nX-Phantom-H: {h}\r\n\
                 Content-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
                bytes.len()
            );
            stream.write_all(header.as_bytes())?;
            stream.write_all(&bytes)
        }
        None => serve_503(stream, "no frame"),
    }
}

fn serve_placement(stream: &mut TcpStream, scene: &dyn SceneSource, query: &str) -> std::io::Result<()> {
    let body = scene.placement_json(query);
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    stream.write_all(resp.as_bytes())
}

fn serve_index(stream: &mut TcpStream) -> std::io::Result<()> {
    let body = "<!doctype html><meta charset=utf-8><title>phantom screen</title>\
<body style='margin:0;background:#111'>\
<img src='/stream' style='max-width:100vw;max-height:100vh;display:block;margin:auto'>\
</body>";
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    stream.write_all(resp.as_bytes())
}

fn serve_state(stream: &mut TcpStream, tap: &FrameTap, verdict: &WatchdogVerdict, live: &LiveState) -> std::io::Result<()> {
    let (state, nonblank, generation) = match tap.snapshot() {
        Some((rgba, w, h, gen)) => {
            let nb = nonblank_fraction(&rgba, w, h);
            
            
            
            let st = verdict.get().unwrap_or_else(|| {
                if crate::sehwerk::aktiv() {
                    zustand_zu_state(crate::sehwerk::blick().1)
                } else {
                    live.probe(gen)
                }
            });
            (st, nb, gen)
        }
        None => (verdict.get().unwrap_or(State::Idle), 0.0, 0),
    };
    let body = format!(
        "{{\"state\":\"{}\",\"nonblank\":{:.4},\"generation\":{},\"watchdog\":\"{}\"}}\n",
        state.as_str(),
        nonblank,
        generation,
        verdict.get().map(|s| s.as_str()).unwrap_or("UNKNOWN")
    );
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    stream.write_all(resp.as_bytes())
}

fn serve_mjpeg(
    mut stream: TcpStream,
    tap: FrameTap,
    cfg: &Config,
    verdict: WatchdogVerdict,
    live: LiveState,
) -> std::io::Result<()> {
    const BOUNDARY: &str = "phantomframe";
    let header = format!(
        "HTTP/1.1 200 OK\r\n\
         Content-Type: multipart/x-mixed-replace; boundary={BOUNDARY}\r\n\
         Cache-Control: no-cache, private\r\n\
         Pragma: no-cache\r\n\
         Connection: close\r\n\r\n"
    );
    stream.write_all(header.as_bytes())?;

    let mut last_fp: Option<u64> = None;
    let mut last_change = Instant::now();
    let mut last_gen: u64 = 0;

    let mut last_present = Instant::now();

    let mut fps: f32 = cfg.min_fps as f32;
    let cap = cfg.max_fps.min(cfg.hard_cap_fps).max(1) as f32;

    loop {

        let cur_gen = tap.generation();
        if cur_gen == last_gen && cur_gen != 0 {

            fps = (fps * 0.8).max(cfg.min_fps as f32);
            std::thread::sleep(Duration::from_millis(120));

            if last_change.elapsed() < cfg.idle_after {
                continue;
            }

        }

        let (rgba, w, h, gen) = match tap.snapshot() {
            Some(f) => f,
            None => {

                std::thread::sleep(Duration::from_millis(100));
                continue;
            }
        };

        
        
        let (changed, presented) = if crate::sehwerk::aktiv() {
            let (_, z, alter_ms) = crate::sehwerk::blick();
            let _ = z;
            (alter_ms < 200, gen != last_gen)
        } else {
            let fp = seam_fingerprint(&rgba, w, h);
            let c = last_fp.map(|p| p != fp).unwrap_or(true);
            last_fp = Some(fp);
            (c, gen != last_gen)
        };
        if changed {
            last_change = Instant::now();
        }

        if changed || presented {
            fps = (fps * 1.6).min(cap);
        }
        if presented {
            last_present = Instant::now();
        }
        last_gen = gen;

        let still_for = last_change.elapsed();
        let heuristic = if changed {
            State::Working
        } else if still_for >= cfg.stuck_after && last_present.elapsed() < cfg.stuck_after {
            State::Stuck
        } else {
            State::Idle
        };
        let state = verdict.get().unwrap_or_else(|| {
            if crate::sehwerk::aktiv() {
                zustand_zu_state(crate::sehwerk::blick().1)
            } else {
                heuristic
            }
        });

        live.publish(state, gen, changed);

        let jpg = jpeg::encode_rgba(w, h, &rgba, cfg.quality);
        let part = format!(
            "--{BOUNDARY}\r\n\
             Content-Type: image/jpeg\r\n\
             Content-Length: {}\r\n\
             X-Phantom-State: {}\r\n\
             X-Phantom-Generation: {}\r\n\r\n",
            jpg.len(),
            state.as_str(),
            gen
        );
        stream.write_all(part.as_bytes())?;
        stream.write_all(&jpg)?;
        stream.write_all(b"\r\n")?;
        stream.flush()?;

        let frame_ms = (1000.0 / fps.max(0.1)) as u64;
        std::thread::sleep(Duration::from_millis(frame_ms.clamp(66, 1000)));
    }
}

fn zustand_zu_state(z: crate::sehwerk::Zustand) -> State {
    match z {
        crate::sehwerk::Zustand::Working => State::Working,
        crate::sehwerk::Zustand::Idle => State::Idle,
        crate::sehwerk::Zustand::Stuck => State::Stuck,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_verdict_tokens() {
        assert_eq!(State::parse_verdict("STUCK"), Some(State::Stuck));
        assert_eq!(State::parse_verdict(" working\n"), Some(State::Working));
        assert_eq!(State::parse_verdict("Idle"), Some(State::Idle));

        assert_eq!(State::parse_verdict("UNKNOWN"), None);
        assert_eq!(State::parse_verdict(""), None);
        assert_eq!(State::parse_verdict("nonsense"), None);
    }

    #[test]
    fn verdict_overrides_heuristic() {

        let vd = WatchdogVerdict::default();
        let heuristic = State::Working;
        assert_eq!(vd.get().unwrap_or(heuristic), State::Working);
        vd.set(Some(State::Stuck));
        assert_eq!(vd.get().unwrap_or(heuristic), State::Stuck);
        vd.set(None);
        assert_eq!(vd.get().unwrap_or(heuristic), State::Working);
    }

    #[test]
    fn verdict_file_watcher_flips_to_stuck_then_clears() {

        let dir = std::env::temp_dir();
        let path = dir.join(format!("phantom-wd-test-{}.verdict", std::process::id()));
        let p = path.to_string_lossy().to_string();
        let _ = std::fs::remove_file(&path);
        let vd = WatchdogVerdict::default();
        let _h = watch_verdict_file(p.clone(), vd.clone(), Duration::from_millis(20));

        std::thread::sleep(Duration::from_millis(60));
        assert_eq!(vd.get(), None, "no file ⇒ no override");

        std::fs::write(&path, b"STUCK\n").unwrap();
        let mut got = None;
        for _ in 0..50 {
            std::thread::sleep(Duration::from_millis(20));
            got = vd.get();
            if got == Some(State::Stuck) {
                break;
            }
        }
        assert_eq!(got, Some(State::Stuck), "verdict file STUCK must flip the shared verdict");

        std::fs::write(&path, b"UNKNOWN\n").unwrap();
        let mut cleared = vd.get();
        for _ in 0..50 {
            std::thread::sleep(Duration::from_millis(20));
            cleared = vd.get();
            if cleared.is_none() {
                break;
            }
        }
        assert_eq!(cleared, None, "UNKNOWN must clear the override");
        let _ = std::fs::remove_file(&path);
    }
}
