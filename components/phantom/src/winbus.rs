


use std::collections::VecDeque;
use std::io::Write;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, SyncSender, TrySendError};
use std::sync::{Mutex, OnceLock};

const REPLAY_ZEILEN: usize = 1024;
const SCHLANGE_JE_ABONNENT: usize = 512;

static SEQ: AtomicU64 = AtomicU64::new(0);
static BUS: OnceLock<Bus> = OnceLock::new();

struct Abonnent {
    tx: SyncSender<String>,
    tropfen: u64,
}

struct Bus {
    replay: Mutex<VecDeque<String>>,
    abonnenten: Mutex<Vec<Abonnent>>,
}

fn ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}


pub(crate) fn melde(felder: &str) {
    let Some(bus) = BUS.get() else { return };
    let seq = SEQ.fetch_add(1, Ordering::Relaxed) + 1;
    
    
    let felder = phantom::redakt::redigiere(felder);
    let zeile = format!("{{\"seq\":{seq},\"ts\":{:.3},{felder}}}\n", ts());
    {
        let mut r = bus.replay.lock().unwrap_or_else(|p| p.into_inner());
        if r.len() >= REPLAY_ZEILEN {
            r.pop_front();
        }
        r.push_back(zeile.clone());
    }
    let mut abos = bus.abonnenten.lock().unwrap_or_else(|p| p.into_inner());
    abos.retain_mut(|a| match a.tx.try_send(zeile.clone()) {
        Ok(()) => true,
        Err(TrySendError::Full(_)) => {
            
            
            
            
            
            a.tropfen += 1;
            true
        }
        Err(TrySendError::Disconnected(_)) => false,
    });
}


pub(crate) fn seit(cursor: u64) -> (Vec<(u64, String)>, u64, u64) {
    let Some(bus) = BUS.get() else { return (Vec::new(), 0, 0) };
    let r = bus.replay.lock().unwrap_or_else(|p| p.into_inner());
    let mut aelteste = u64::MAX;
    let mut neueste = 0u64;
    let mut out = Vec::new();
    for z in r.iter() {
        let seq = zeilen_seq(z);
        if seq == 0 {
            continue;
        }
        if seq < aelteste {
            aelteste = seq;
        }
        if seq > neueste {
            neueste = seq;
        }
        if seq > cursor {
            out.push((seq, z.clone()));
        }
    }
    if aelteste == u64::MAX {
        aelteste = 0;
    }
    (out, aelteste, neueste)
}

fn zeilen_seq(z: &str) -> u64 {
    
    z.strip_prefix("{\"seq\":")
        .and_then(|r| r.split([',', '}']).next())
        .and_then(|n| n.parse().ok())
        .unwrap_or(0)
}


pub(crate) fn json_str(s: &str) -> String {
    s.chars()
        .flat_map(|c| match c {
            '"' => vec!['\\', '"'],
            '\\' => vec!['\\', '\\'],
            '\n' => vec!['\\', 'n'],
            c if (c as u32) < 0x20 => vec![' '],
            c => vec![c],
        })
        .collect()
}

fn bediene(mut s: UnixStream, bus: &'static Bus) {
    let (tx, rx) = sync_channel::<String>(SCHLANGE_JE_ABONNENT);
    let nachschub = {
        let r = bus.replay.lock().unwrap_or_else(|p| p.into_inner());
        r.iter().cloned().collect::<Vec<_>>()
    };
    {
        let mut abos = bus.abonnenten.lock().unwrap_or_else(|p| p.into_inner());
        abos.push(Abonnent { tx, tropfen: 0 });
    }
    std::thread::spawn(move || {
        for z in nachschub {
            if s.write_all(z.as_bytes()).is_err() {
                return;
            }
        }
        while let Ok(z) = rx.recv() {
            if s.write_all(z.as_bytes()).is_err() {
                return; 
            }
        }
    });
}


pub(crate) fn starte(pfad: &str) {
    let bus = BUS.get_or_init(|| Bus {
        replay: Mutex::new(VecDeque::new()),
        abonnenten: Mutex::new(Vec::new()),
    });
    
    if std::path::Path::new(pfad).exists() {
        if UnixStream::connect(pfad).is_ok() {
            eprintln!("phantom: winevents {pfad} ist BESETZT — dieser Lauf meldet nur lokal.");
            return;
        }
        let _ = std::fs::remove_file(pfad);
    }
    let listener = match UnixListener::bind(pfad) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom: winevents {pfad} nicht bindbar: {e}");
            return;
        }
    };
    eprintln!("phantom: winevents {pfad}");
    std::thread::spawn(move || {
        for conn in listener.incoming() {
            let Ok(s) = conn else { continue };
            bediene(s, bus);
        }
    });
}
