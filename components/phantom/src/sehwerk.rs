


#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Condvar, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const KACHEL: usize = 64;

pub const RING_DECKEL: usize = 64;

const ANHANG_ZEILEN: usize = 12;
const ANHANG_BYTES: usize = 2048;

const KUNDEN_DECKEL: usize = 16;
const KUNDEN_TTL: Duration = Duration::from_secs(3600);


static SCHADEN_SEQ: AtomicU64 = AtomicU64::new(1);

pub fn naechste_seq() -> u64 {
    SCHADEN_SEQ.fetch_add(1, Ordering::Relaxed)
}

pub fn aktuelle_seq() -> u64 {
    SCHADEN_SEQ.load(Ordering::Relaxed)
}

pub fn aktiv() -> bool {
    static AN: OnceLock<bool> = OnceLock::new();
    *AN.get_or_init(|| std::env::var("PHANTOM_SEHWERK").as_deref() == Ok("1"))
}

pub fn weltdelta_an() -> bool {
    static AN: OnceLock<bool> = OnceLock::new();
    *AN.get_or_init(|| std::env::var("PHANTOM_WELTDELTA").as_deref() != Ok("0"))
}


#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Raum {
    Surface,
    Buffer,
}

#[derive(Clone, Copy, Debug)]
pub struct Schaden {
    pub seq: u64,
    pub raum: Raum,
    pub r: (i32, i32, i32, i32),
    
    pub grob: bool,
}


pub fn schaden_anfuegen(
    ring: &mut Vec<Schaden>,
    raum: Raum,
    rect: (i32, i32, i32, i32),
    seq: u64,
    flaeche: (i32, i32),
) {
    if ring.len() >= RING_DECKEL {
        ring.clear();
        ring.push(Schaden { seq, raum: Raum::Surface, r: (0, 0, flaeche.0.max(1), flaeche.1.max(1)), grob: true });
        return;
    }
    
    
    if let Some(l) = ring.last_mut() {
        if l.grob {
            l.seq = seq;
            return;
        }
    }
    ring.push(Schaden { seq, raum, r: rect, grob: false });
}


pub fn schaden_seit(ring: &[Schaden], cursor: u64) -> (Vec<(i32, i32, i32, i32)>, u64) {
    let mut max = cursor;
    let mut out = Vec::new();
    for s in ring {
        if s.seq > cursor {
            out.push(s.r);
            if s.seq > max {
                max = s.seq;
            }
        }
    }
    (out, max)
}


pub fn region_wort(rects: &[(i32, i32, i32, i32)], flaeche: (i32, i32)) -> &'static str {
    if rects.is_empty() || flaeche.0 <= 0 || flaeche.1 <= 0 {
        return "";
    }
    let (mut x0, mut y0, mut x1, mut y1) = (i32::MAX, i32::MAX, i32::MIN, i32::MIN);
    for &(x, y, w, h) in rects {
        x0 = x0.min(x);
        y0 = y0.min(y);
        x1 = x1.max(x + w);
        y1 = y1.max(y + h);
    }
    let (cx, cy) = ((x0 + x1) / 2, (y0 + y1) / 2);
    let breit = (x1 - x0) as i64 * (y1 - y0) as i64 >= flaeche.0 as i64 * flaeche.1 as i64 / 2;
    if breit {
        return "grossflaechig";
    }
    let sp = if cx < flaeche.0 / 3 { "links" } else if cx > 2 * flaeche.0 / 3 { "rechts" } else { "mitte" };
    let ze = if cy < flaeche.1 / 3 { "oben" } else if cy > 2 * flaeche.1 / 3 { "unten" } else { "mitte" };
    match (ze, sp) {
        ("oben", "links") => "oben-links",
        ("oben", "mitte") => "oben",
        ("oben", "rechts") => "oben-rechts",
        ("mitte", "links") => "links",
        ("mitte", "mitte") => "mittig",
        ("mitte", "rechts") => "rechts",
        ("unten", "links") => "unten-links",
        ("unten", "mitte") => "unten",
        _ => "unten-rechts",
    }
}


pub fn fnv1a64(daten: &[u8], start: u64) -> u64 {
    let mut h = if start == 0 { 0xcbf29ce484222325 } else { start };
    for &b in daten {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}


pub fn kacheln_fortschreiben(
    rgba: &[u8],
    w: usize,
    h: usize,
    netz: &mut Vec<u64>,
) -> (u32, Option<(i32, i32, i32, i32)>) {
    if w == 0 || h == 0 || rgba.len() < w * h * 4 {
        return (0, None);
    }
    let sp = (w + KACHEL - 1) / KACHEL;
    let ze = (h + KACHEL - 1) / KACHEL;
    let n = sp * ze;
    let frisch = netz.len() != n;
    if frisch {
        netz.clear();
        netz.resize(n, 0);
    }
    let mut neu = vec![0u64; n];
    for y in 0..h {
        let kz = y / KACHEL;
        let zeile = &rgba[y * w * 4..(y + 1) * w * 4];
        for ks in 0..sp {
            let a = ks * KACHEL * 4;
            let b = ((ks + 1) * KACHEL * 4).min(zeile.len());
            let idx = kz * sp + ks;
            neu[idx] = fnv1a64(&zeile[a..b], neu[idx]);
        }
    }
    let mut geaendert = 0u32;
    let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0usize, 0usize);
    for i in 0..n {
        if neu[i] != netz[i] {
            geaendert += 1;
            let (ks, kz) = (i % sp, i / sp);
            x0 = x0.min(ks * KACHEL);
            y0 = y0.min(kz * KACHEL);
            x1 = x1.max(((ks + 1) * KACHEL).min(w));
            y1 = y1.max(((kz + 1) * KACHEL).min(h));
        }
    }
    *netz = neu;
    if frisch {
        
        return (geaendert, Some((0, 0, w as i32, h as i32)));
    }
    if geaendert == 0 {
        (0, None)
    } else {
        (geaendert, Some((x0 as i32, y0 as i32, (x1 - x0) as i32, (y1 - y0) as i32)))
    }
}


#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Zustand {
    Working,
    Idle,
    Stuck,
}

impl Zustand {
    pub fn wort(self) -> &'static str {
        match self {
            Zustand::Working => "working",
            Zustand::Idle => "idle",
            Zustand::Stuck => "stuck",
        }
    }
}

struct Blick {
    generation: u64,
    letzte_aenderung: Option<Instant>,
    letzte_praesentation: Option<Instant>,
    gemeldet: Zustand,
    
    wechsel_anstehend: Option<Zustand>,
}

static BLICK: OnceLock<Mutex<Blick>> = OnceLock::new();

fn blick_zelle() -> &'static Mutex<Blick> {
    BLICK.get_or_init(|| {
        Mutex::new(Blick { generation: 0, letzte_aenderung: None, letzte_praesentation: None, gemeldet: Zustand::Idle, wechsel_anstehend: None })
    })
}

const IDLE_NACH: Duration = Duration::from_millis(1500);
const STUCK_NACH: Duration = Duration::from_secs(5);


pub fn praesentiert(generation: u64, kacheln_geaendert: u32) -> Zustand {
    let jetzt = Instant::now();
    let mut b = blick_zelle().lock().unwrap_or_else(|p| p.into_inner());
    b.generation = generation;
    b.letzte_praesentation = Some(jetzt);
    if kacheln_geaendert > 0 {
        b.letzte_aenderung = Some(jetzt);
    }
    let z = zustand_aus(&b, jetzt);
    if z != b.gemeldet {
        b.gemeldet = z;
        b.wechsel_anstehend = Some(z);
    }
    z
}


pub fn wechsel_abholen() -> Option<Zustand> {
    let mut b = blick_zelle().lock().unwrap_or_else(|p| p.into_inner());
    b.wechsel_anstehend.take()
}

fn zustand_aus(b: &Blick, jetzt: Instant) -> Zustand {
    match (b.letzte_aenderung, b.letzte_praesentation) {
        (Some(a), Some(p)) => {
            if jetzt.duration_since(a) < IDLE_NACH {
                Zustand::Working
            } else if jetzt.duration_since(a) >= STUCK_NACH && jetzt.duration_since(p) < STUCK_NACH {
                Zustand::Stuck
            } else {
                Zustand::Idle
            }
        }
        _ => Zustand::Idle,
    }
}


pub fn blick() -> (u64, Zustand, u64) {
    let b = blick_zelle().lock().unwrap_or_else(|p| p.into_inner());
    let jetzt = Instant::now();
    let alter = b.letzte_aenderung.map(|a| jetzt.duration_since(a).as_millis() as u64).unwrap_or(u64::MAX);
    (b.generation, zustand_aus(&b, jetzt), alter)
}


static PULS: OnceLock<(Mutex<u64>, Condvar)> = OnceLock::new();

fn puls_zelle() -> &'static (Mutex<u64>, Condvar) {
    PULS.get_or_init(|| (Mutex::new(0), Condvar::new()))
}


pub fn puls_schlag() {
    let (m, cv) = puls_zelle();
    {
        let mut g = m.lock().unwrap_or_else(|p| p.into_inner());
        *g = g.wrapping_add(1);
    }
    cv.notify_all();
}


pub fn puls_warte(dauer: Duration) -> bool {
    let (m, cv) = puls_zelle();
    let g = m.lock().unwrap_or_else(|p| p.into_inner());
    let start = *g;
    let (g2, timeout) = cv
        .wait_timeout_while(g, dauer, |v| *v == start)
        .unwrap_or_else(|p| p.into_inner());
    drop(g2);
    !timeout.timed_out()
}


struct Kunde {
    cursor: u64,
    zuletzt: Instant,
}

static KUNDEN: OnceLock<Mutex<HashMap<String, Kunde>>> = OnceLock::new();

fn kunden() -> &'static Mutex<HashMap<String, Kunde>> {
    KUNDEN.get_or_init(|| Mutex::new(HashMap::new()))
}


pub fn weltdelta_anhang<F>(sitz: &str, zeilen_seit: F) -> Option<String>
where
    F: Fn(u64) -> (Vec<(u64, String)>, u64, u64),
{
    if sitz.is_empty() || !weltdelta_an() {
        return None;
    }
    let jetzt = Instant::now();
    let cursor = {
        let mut k = kunden().lock().unwrap_or_else(|p| p.into_inner());
        k.retain(|_, v| jetzt.duration_since(v.zuletzt) < KUNDEN_TTL);
        if k.len() >= KUNDEN_DECKEL && !k.contains_key(sitz) {
            
            if let Some(alt) = k.iter().min_by_key(|(_, v)| v.zuletzt).map(|(n, _)| n.clone()) {
                k.remove(&alt);
            }
        }
        let e = k.entry(sitz.to_string()).or_insert(Kunde { cursor: 0, zuletzt: jetzt });
        e.zuletzt = jetzt;
        e.cursor
    };
    let (zeilen, aelteste, neueste) = zeilen_seit(cursor);
    if neueste <= cursor && zeilen.is_empty() {
        return None;
    }
    let mut teile: Vec<String> = Vec::new();
    
    
    if cursor > 0 && aelteste > cursor + 1 {
        teile.push(format!(
            "luecke: ereignisse seq {}–{} nicht mehr im ring — `delta {}` fuer re-sync",
            cursor + 1,
            aelteste - 1,
            cursor
        ));
    }
    
    
    let mut commits = 0u64;
    let mut commit_letzt = String::new();
    let mut rest: Vec<String> = Vec::new();
    for (_, z) in &zeilen {
        if z.contains("\"ereignis\":\"commit\"") {
            commits += 1;
            commit_letzt = z.trim_end().to_string();
        } else {
            rest.push(z.trim_end().to_string());
        }
    }
    if commits == 1 {
        rest.insert(0, commit_letzt);
    } else if commits > 1 {
        rest.insert(0, format!("bild aendert sich laufend: {} commit-meldungen zusammengefasst", commits));
    }
    let mut bytes = teile.iter().map(|t| t.len()).sum::<usize>();
    let mut n = 0usize;
    let gesamt = rest.len();
    for z in rest {
        if n >= ANHANG_ZEILEN || bytes + z.len() > ANHANG_BYTES {
            teile.push(format!("… +{} weitere — `delta {}` fuer alles", gesamt - n, cursor));
            break;
        }
        bytes += z.len();
        n += 1;
        teile.push(z);
    }
    {
        let mut k = kunden().lock().unwrap_or_else(|p| p.into_inner());
        if let Some(e) = k.get_mut(sitz) {
            e.cursor = neueste;
        }
    }
    if teile.is_empty() {
        None
    } else {
        Some(format!("— seither:\n{}", teile.join("\n")))
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_deckel_vergroebert_statt_verwirft() {
        let mut ring = Vec::new();
        for i in 0..(RING_DECKEL + 5) {
            schaden_anfuegen(&mut ring, Raum::Buffer, (i as i32, 0, 1, 1), i as u64 + 1, (800, 600));
        }
        assert_eq!(ring.len(), 1, "voller Ring muss zu EINEM Eintrag vergroebern");
        assert!(ring[0].grob);
        assert_eq!(ring[0].r, (0, 0, 800, 600));
        
        let seq_vorher = ring[0].seq;
        schaden_anfuegen(&mut ring, Raum::Surface, (5, 5, 2, 2), seq_vorher + 7, (800, 600));
        assert_eq!(ring.len(), 1);
        assert_eq!(ring[0].seq, seq_vorher + 7);
    }

    #[test]
    fn schaden_seit_ist_nicht_destruktiv() {
        let mut ring = Vec::new();
        schaden_anfuegen(&mut ring, Raum::Buffer, (1, 1, 10, 10), 5, (100, 100));
        schaden_anfuegen(&mut ring, Raum::Buffer, (2, 2, 10, 10), 9, (100, 100));
        let (a, m) = schaden_seit(&ring, 5);
        assert_eq!(a.len(), 1);
        assert_eq!(m, 9);
        let (b, _) = schaden_seit(&ring, 5);
        assert_eq!(b.len(), 1, "zweiter Leser sieht dieselben Rects — nichts gestohlen");
        let (c, _) = schaden_seit(&ring, 0);
        assert_eq!(c.len(), 2);
    }

    #[test]
    fn raum_scale1_deckungsgleich() {
        
        
        
        assert_ne!(Raum::Surface, Raum::Buffer);
        let mut ring = Vec::new();
        schaden_anfuegen(&mut ring, Raum::Surface, (1, 2, 3, 4), 1, (10, 10));
        schaden_anfuegen(&mut ring, Raum::Buffer, (1, 2, 3, 4), 2, (10, 10));
        assert_eq!(ring[0].r, ring[1].r);
    }

    #[test]
    fn kacheln_erster_lauf_dann_delta() {
        let (w, h) = (130usize, 70usize);
        let mut bild = vec![0u8; w * h * 4];
        let mut netz = Vec::new();
        let (g1, r1) = kacheln_fortschreiben(&bild, w, h, &mut netz);
        assert!(g1 > 0, "Erstlauf zaehlt alles als neu");
        assert_eq!(r1, Some((0, 0, w as i32, h as i32)));
        let (g2, r2) = kacheln_fortschreiben(&bild, w, h, &mut netz);
        assert_eq!(g2, 0);
        assert_eq!(r2, None);
        
        bild[(3 * w + 70) * 4] = 0xff;
        let (g3, r3) = kacheln_fortschreiben(&bild, w, h, &mut netz);
        assert_eq!(g3, 1);
        let r = r3.unwrap();
        assert_eq!((r.0, r.1), (64, 0));
    }

    #[test]
    fn region_wort_trifft_quadranten() {
        assert_eq!(region_wort(&[(900, 700, 20, 20)], (1000, 800)), "unten-rechts");
        assert_eq!(region_wort(&[(10, 10, 20, 20)], (1000, 800)), "oben-links");
        assert_eq!(region_wort(&[(0, 0, 900, 700)], (1000, 800)), "grossflaechig");
    }

    #[test]
    fn weltdelta_luecke_wird_benannt_und_cursor_rueckt() {
        std::env::remove_var("PHANTOM_WELTDELTA");
        
        let quelle = |cursor: u64| {
            let zeilen: Vec<(u64, String)> = (50u64..=60)
                .filter(|s| *s > cursor)
                .map(|s| (s, format!("{{\"seq\":{s},\"ereignis\":\"title_changed\"}}")))
                .collect();
            (zeilen, 50, 60)
        };
        
        let a = weltdelta_anhang("test-kunde-a", quelle).expect("anhang");
        assert!(!a.contains("luecke"), "Erstkontakt ist kein Verlust: {a}");
        
        {
            let mut k = kunden().lock().unwrap();
            k.get_mut("test-kunde-a").unwrap().cursor = 10;
        }
        let b = weltdelta_anhang("test-kunde-a", quelle).expect("anhang");
        assert!(b.contains("luecke: ereignisse seq 11–49"), "{b}");
        
        assert!(weltdelta_anhang("test-kunde-a", quelle).is_none());
    }

    #[test]
    fn weltdelta_kollabiert_commit_rauschen_und_deckelt() {
        let quelle = |cursor: u64| {
            let mut zeilen = Vec::new();
            for s in 1u64..=40 {
                if s <= cursor {
                    continue;
                }
                if s % 2 == 0 {
                    zeilen.push((s, format!("{{\"seq\":{s},\"ereignis\":\"commit\",\"cid\":1}}")));
                } else {
                    zeilen.push((s, format!("{{\"seq\":{s},\"ereignis\":\"title_changed\",\"titel\":\"t{s}\"}}")));
                }
            }
            (zeilen, 1, 40)
        };
        let a = weltdelta_anhang("test-kunde-b", quelle).expect("anhang");
        assert!(a.contains("20 commit-meldungen zusammengefasst"), "{a}");
        assert!(a.lines().count() <= ANHANG_ZEILEN + 3, "Zeilen-Deckel: {a}");
        assert!(a.len() <= ANHANG_BYTES + 200, "Byte-Deckel: {}", a.len());
    }

    #[test]
    fn puls_weckt_warter() {
        let t = std::thread::spawn(|| puls_warte(Duration::from_secs(5)));
        std::thread::sleep(Duration::from_millis(30));
        puls_schlag();
        assert!(t.join().unwrap(), "Warter muss vom Schlag geweckt werden");
        assert!(!puls_warte(Duration::from_millis(30)), "ohne Schlag: Frist laeuft ab");
    }
}
