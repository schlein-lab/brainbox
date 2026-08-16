use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};

use phantom::input::VirtualInput;
use phantom::wire::{event, p_u32};
use phantom::{png, sys};

use crate::state::Shared;
use crate::xwl::window_resources;

static KBD: OnceLock<Mutex<Option<VirtualInput>>> = OnceLock::new();

pub(crate) fn with_kbd(f: impl FnOnce(&mut VirtualInput) -> std::io::Result<()>) -> String {
    let mut guard = KBD.get_or_init(|| Mutex::new(None)).lock().unwrap();
    if guard.is_none() {
        match VirtualInput::new() {
            Ok(vi) => *guard = Some(vi),
            Err(e) => {

                let hint = if e.kind() == std::io::ErrorKind::PermissionDenied {
                    " — /dev/uinput not writable by this user; install the udev rule \
                     (MODE 0666) or add the user to the 'input' group, then restart phantom-hub"
                } else {
                    ""
                };
                return format!("err: uinput unavailable: {e}{hint}\n");
            }
        }
    }
    let vi = guard.as_mut().unwrap();
    match f(vi) {
        Ok(()) => "ok\n".into(),
        Err(e) => format!("err: {e}\n"),
    }
}

fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

pub(crate) fn uid_to_name(uid: u32) -> Option<String> {
    let pw = std::fs::read_to_string("/etc/passwd").ok()?;
    for line in pw.lines() {
        let mut f = line.split(':');
        let name = f.next()?;
        let _ = f.next();
        if f.next()?.parse::<u32>().ok()? == uid {
            return Some(name.to_string());
        }
    }
    None
}

pub(crate) fn sense_text(shared: &Shared, cid: u64) -> String {
    use std::os::unix::fs::MetadataExt;
    let (token, app_pid) = {
        let g = shared.lock().unwrap();
        let Some(st) = g.get(&cid) else { return "error: no such client\n".into() };
        let token = st
            .app_id
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| st.title.clone())
            .unwrap_or_default();
        (token, st.pid)
    };
    if token.is_empty() {
        return "error: no app_id/title to address the a11y tree\n".into();
    }
    let tool = std::env::var("PHANTOM_A11Y").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        format!("{home}/uiapi/uiapi.py")
    });
    if !std::path::Path::new(&tool).exists() {
        return "note: text-sense needs the a11y reader (set PHANTOM_A11Y=path/to/uiapi.py)\n".into();
    }
    let inner = format!("python3 {} tree {} --text --max 400", shell_quote(&tool), shell_quote(&token));
    let my_uid = std::fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(0);
    let app_uid = app_pid
        .and_then(|p| std::fs::metadata(format!("/proc/{p}")).ok())
        .map(|m| m.uid());
    let mut cmd = if my_uid == 0 {
        if let Some(uid) = app_uid.filter(|&u| u != 0) {
            let user = uid_to_name(uid).unwrap_or_else(|| uid.to_string());
            let env = format!(
                "XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"
            );
            let mut c = std::process::Command::new("su");
            c.arg(user).arg("-c").arg(format!("{env} {inner}"));
            c
        } else {
            let mut c = std::process::Command::new("sh");
            c.arg("-c").arg(inner);
            c
        }
    } else {
        let mut c = std::process::Command::new("sh");
        c.arg("-c").arg(inner);
        c
    };
    match cmd.output() {
        Ok(o) if !o.stdout.is_empty() => String::from_utf8_lossy(&o.stdout).into_owned(),
        Ok(o) => {
            let err = String::from_utf8_lossy(&o.stderr);
            let last = err.lines().last().unwrap_or("no output");
            format!("note: a11y read empty ({last}). For VS Code, launch it with --force-renderer-accessibility for the full tree.\n")
        }
        Err(e) => format!("error: a11y read: {e}\n"),
    }
}


fn ebene_mischen(leinwand: &mut [u8], lw: usize, lh: usize, l: &crate::compositor::SceneLayer) {
    let info = l.info;
    let (bw, bh) = (info.width as i64, info.height as i64);
    let stride = info.stride as usize;
    let start = info.offset as usize;
    let data = l.mmap.as_slice();
    for by in 0..bh {
        let zy = l.y as i64 + by;
        if zy < 0 || zy >= lh as i64 {
            continue;
        }
        let zeile = start + (by as usize) * stride;
        for bx in 0..bw {
            let zx = l.x as i64 + bx;
            if zx < 0 || zx >= lw as i64 {
                continue;
            }
            let p = zeile + (bx as usize) * 4;
            if p + 3 >= data.len() {
                continue;
            }
            let (b, g, r) = (data[p], data[p + 1], data[p + 2]);
            let a = if info.format == 1 { 255u8 } else { data[p + 3] };
            let z = ((zy as usize) * lw + zx as usize) * 4;
            if a == 255 {
                leinwand[z] = r;
                leinwand[z + 1] = g;
                leinwand[z + 2] = b;
                leinwand[z + 3] = 255;
            } else if a > 0 {
                let inv = 255u32 - a as u32;
                for (i, q) in [r, g, b].iter().enumerate() {
                    leinwand[z + i] = (*q as u32 + leinwand[z + i] as u32 * inv / 255).min(255) as u8;
                }
                leinwand[z + 3] =
                    (a as u32 + leinwand[z + 3] as u32 * inv / 255).min(255) as u8;
            }
        }
    }
}


pub(crate) struct Bild {
    pub w: usize,
    pub h: usize,
    pub ebenen: usize,
    pub rgba: Vec<u8>,
}


pub(crate) fn bild_holen(shared: &Shared, cid: u64) -> Result<Bild, String> {
    let ebenen = crate::compositor::scene_layers_for(shared, cid);
    let erste = ebenen
        .first()
        .ok_or("surface has no committed buffer yet")?;
    let (w, h) = (erste.info.width as usize, erste.info.height as usize);
    if w == 0 || h == 0 {
        return Err("buffer has zero size".into());
    }
    let mut rgba = vec![0u8; w * h * 4];
    for l in &ebenen {
        ebene_mischen(&mut rgba, w, h, l);
    }
    Ok(Bild { w, h, ebenen: ebenen.len(), rgba })
}


pub(crate) const WELT_CID: u64 = u64::MAX;


pub(crate) fn welt_bild(shared: &Shared) -> Result<Bild, String> {
    let (ebenen, w, h) = crate::compositor::welt_scene_layers(shared, None);
    if ebenen.is_empty() || w <= 0 || h <= 0 {
        return Err("welt: kein fenster mit inhalt".into());
    }
    let (w, h) = (w as usize, h as usize);
    let mut rgba = vec![0u8; w * h * 4];
    for l in &ebenen {
        ebene_mischen(&mut rgba, w, h, l);
    }
    Ok(Bild { w, h, ebenen: ebenen.len(), rgba })
}


pub(crate) fn welt_bild_fest(shared: &Shared, w: usize, h: usize) -> Result<Bild, String> {
    let (ebenen, _, _) =
        crate::compositor::welt_scene_layers(shared, Some((w as i32, h as i32)));
    if ebenen.is_empty() {
        return Err("welt: kein fenster mit inhalt".into());
    }
    let mut rgba = vec![0u8; w * h * 4];
    for l in &ebenen {
        ebene_mischen(&mut rgba, w, h, l);
    }
    Ok(Bild { w, h, ebenen: ebenen.len(), rgba })
}

pub(crate) fn do_snapshot(shared: &Shared, cid: u64, path: Option<String>) -> Result<String, String> {
    let b = if cid == WELT_CID { welt_bild(shared)? } else { bild_holen(shared, cid)? };
    
    
    
    let out = path.unwrap_or_else(|| format!("/tmp/phantom-snap-{cid}.jpg"));
    let bytes = if out.ends_with(".png") {
        png::encode_rgba(b.w as u32, b.h as u32, &b.rgba)
    } else {
        phantom::jpeg::encode_rgba(b.w as u32, b.h as u32, &b.rgba, 80)
    };
    std::fs::write(&out, &bytes).map_err(|e| e.to_string())?;
    Ok(format!("{out} ({}x{}, {} Ebenen)", b.w, b.h, b.ebenen))
}


pub(crate) fn do_zoom(
    shared: &Shared,
    cid: u64,
    x: i64,
    y: i64,
    w: i64,
    h: i64,
    path: Option<String>,
) -> Result<String, String> {
    if w <= 0 || h <= 0 {
        return Err("zoom braucht w>0 und h>0".into());
    }
    let b = bild_holen(shared, cid)?;
    
    let x0 = x.clamp(0, b.w as i64) as usize;
    let y0 = y.clamp(0, b.h as i64) as usize;
    let x1 = (x + w).clamp(0, b.w as i64) as usize;
    let y1 = (y + h).clamp(0, b.h as i64) as usize;
    let (cw, ch) = (x1.saturating_sub(x0), y1.saturating_sub(y0));
    if cw == 0 || ch == 0 {
        return Err(format!(
            "ausschnitt {x},{y} {w}x{h} liegt ausserhalb des fensters ({}x{})",
            b.w, b.h
        ));
    }
    let faktor = (512usize.div_ceil(cw)).clamp(1, 4);
    let (zw, zh) = (cw * faktor, ch * faktor);
    let mut px = vec![0u8; zw * zh * 4];
    for zy in 0..zh {
        let sy = y0 + zy / faktor;
        for zx in 0..zw {
            let sx = x0 + zx / faktor;
            let s = (sy * b.w + sx) * 4;
            let d = (zy * zw + zx) * 4;
            px[d..d + 4].copy_from_slice(&b.rgba[s..s + 4]);
        }
    }
    let out = path.unwrap_or_else(|| format!("/tmp/phantom-zoom-{cid}.jpg"));
    let bytes = if out.ends_with(".png") {
        png::encode_rgba(zw as u32, zh as u32, &px)
    } else {
        phantom::jpeg::encode_rgba(zw as u32, zh as u32, &px, 85)
    };
    std::fs::write(&out, &bytes).map_err(|e| e.to_string())?;
    Ok(format!(
        "{out} ({zw}x{zh}, ausschnitt {x0},{y0} {cw}x{ch}, faktor {faktor})"
    ))
}


const SOM_FARBEN: [[u8; 3]; 6] = [
    [255, 64, 64],
    [64, 160, 255],
    [64, 200, 96],
    [255, 176, 32],
    [200, 96, 255],
    [32, 208, 208],
];

const SOM_ZIFFERN: [[u8; 5]; 10] = [
    [0b111, 0b101, 0b101, 0b101, 0b111],
    [0b010, 0b110, 0b010, 0b010, 0b111],
    [0b111, 0b001, 0b111, 0b100, 0b111],
    [0b111, 0b001, 0b111, 0b001, 0b111],
    [0b101, 0b101, 0b111, 0b001, 0b001],
    [0b111, 0b100, 0b111, 0b001, 0b111],
    [0b111, 0b100, 0b111, 0b101, 0b111],
    [0b111, 0b001, 0b010, 0b010, 0b010],
    [0b111, 0b101, 0b111, 0b101, 0b111],
    [0b111, 0b101, 0b111, 0b001, 0b111],
];

fn som_punkt(px: &mut [u8], bw: usize, bh: usize, x: i64, y: i64, f: [u8; 3]) {
    if x < 0 || y < 0 || x >= bw as i64 || y >= bh as i64 {
        return;
    }
    let d = (y as usize * bw + x as usize) * 4;
    px[d] = f[0];
    px[d + 1] = f[1];
    px[d + 2] = f[2];
}

fn som_rect(px: &mut [u8], bw: usize, bh: usize, x: i64, y: i64, w: i64, h: i64, f: [u8; 3]) -> bool {
    if w <= 0 || h <= 0 || x + w <= 0 || y + h <= 0 || x >= bw as i64 || y >= bh as i64 {
        return false;
    }
    for dx in 0..w {
        for lage in [y, y + 1, y + h - 2, y + h - 1] {
            som_punkt(px, bw, bh, x + dx, lage, f);
        }
    }
    for dy in 0..h {
        for lage in [x, x + 1, x + w - 2, x + w - 1] {
            som_punkt(px, bw, bh, lage, y + dy, f);
        }
    }
    true
}

fn som_nummer(px: &mut [u8], bw: usize, bh: usize, x: i64, y: i64, n: usize, f: [u8; 3]) {
    let text = n.to_string();
    let sk: i64 = 2; 
    let breite = text.len() as i64 * (3 * sk + 2) + 2;
    for dy in -1..(5 * sk + 1) {
        for dx in -1..breite {
            som_punkt(px, bw, bh, x + dx, y + dy, [16, 16, 16]);
        }
    }
    let mut cx = x + 1;
    for z in text.bytes() {
        let zi = (z - b'0') as usize;
        for (reihe, bits) in SOM_ZIFFERN[zi].iter().enumerate() {
            for spalte in 0..3i64 {
                if bits & (0b100 >> spalte) != 0 {
                    for oy in 0..sk {
                        for ox in 0..sk {
                            som_punkt(px, bw, bh, cx + spalte * sk + ox,
                                      y + reihe as i64 * sk + oy, f);
                        }
                    }
                }
            }
        }
        cx += 3 * sk + 2;
    }
}


pub(crate) fn do_som(
    shared: &Shared,
    cid: u64,
    ziele: &[(i64, i64, i64, i64)],
    path: Option<String>,
) -> Result<String, String> {
    if ziele.is_empty() {
        return Err("som braucht mindestens ein ziel (x y w h ...)".into());
    }
    if ziele.len() > 200 {
        return Err("som: mehr als 200 ziele — karte enger fassen".into());
    }
    let b = bild_holen(shared, cid)?;
    let mut px = b.rgba.clone();
    let mut drin = 0usize;
    for (i, &(x, y, w, h)) in ziele.iter().enumerate() {
        let f = SOM_FARBEN[i % SOM_FARBEN.len()];
        if som_rect(&mut px, b.w, b.h, x, y, w, h, f) {
            drin += 1;
            som_nummer(&mut px, b.w, b.h, x + 3, y + 3, i + 1, f);
        }
    }
    let out = path.unwrap_or_else(|| format!("/tmp/phantom-som-{cid}.jpg"));
    let bytes = if out.ends_with(".png") {
        png::encode_rgba(b.w as u32, b.h as u32, &px)
    } else {
        phantom::jpeg::encode_rgba(b.w as u32, b.h as u32, &px, 85)
    };
    std::fs::write(&out, &bytes).map_err(|e| e.to_string())?;
    Ok(format!(
        "{out} ({}x{}, {drin}/{} ziele im bild, nummer = eingabe-reihenfolge)",
        b.w, b.h, ziele.len()
    ))
}


static AUFNAHME_LAEUFT: AtomicBool = AtomicBool::new(false);
static AUFNAHME_STOPP: AtomicBool = AtomicBool::new(false);
static AUFNAHME_STAND: Mutex<String> = Mutex::new(String::new());

fn stand_setzen(s: String) {
    if let Ok(mut g) = AUFNAHME_STAND.lock() {
        *g = s;
    }
}

pub(crate) fn do_record_status() -> String {
    let laeuft = AUFNAHME_LAEUFT.load(Ordering::SeqCst);
    let stand = AUFNAHME_STAND
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default();
    let stand = if stand.is_empty() { "noch keine Aufnahme".to_string() } else { stand };
    format!("{}: {stand}\n", if laeuft { "laeuft" } else { "steht" })
}

pub(crate) fn do_record_stop() -> String {
    if !AUFNAHME_LAEUFT.load(Ordering::SeqCst) {
        return "ok: es lief keine Aufnahme\n".into();
    }
    AUFNAHME_STOPP.store(true, Ordering::SeqCst);
    "ok: Aufnahme wird beendet\n".into()
}


pub(crate) fn do_record(
    shared: &Shared,
    cid: u64,
    pfad: String,
    fps: u32,
    sekunden: f64,
) -> Result<String, String> {
    if AUFNAHME_LAEUFT.swap(true, Ordering::SeqCst) {
        return Err("es laeuft schon eine Aufnahme (record stop / record status)".into());
    }
    let fps = fps.clamp(1, 60);
    
    
    
    let erst = match if cid == WELT_CID { welt_bild(shared) } else { bild_holen(shared, cid) } {
        Ok(b) => b,
        Err(e) => {
            AUFNAHME_LAEUFT.store(false, Ordering::SeqCst);
            return Err(e);
        }
    };
    let (w, h) = (erst.w, erst.h);
    AUFNAHME_STOPP.store(false, Ordering::SeqCst);
    stand_setzen(format!("{w}x{h} @{fps} -> {pfad}: warte auf die Gegenstelle"));

    let geteilt = shared.clone();
    let ziel_pfad = pfad.clone();
    std::thread::spawn(move || {
        
        
        
        let mut ziel = match std::fs::OpenOptions::new().write(true).create(true).open(&ziel_pfad) {
            Ok(f) => f,
            Err(e) => {
                stand_setzen(format!("Ziel {ziel_pfad} nicht schreibbar: {e}"));
                AUFNAHME_LAEUFT.store(false, Ordering::SeqCst);
                return;
            }
        };
        let takt = std::time::Duration::from_nanos(1_000_000_000u64 / fps as u64);
        let start = std::time::Instant::now();
        let mut faellig = start;
        let mut n: u64 = 0;
        let mut letztes_bild: Option<Vec<u8>> = None;
        let mut fehl_seit: Option<std::time::Instant> = None;
        let grund = loop {
            if AUFNAHME_STOPP.load(Ordering::SeqCst) {
                break "gestoppt".to_string();
            }
            if sekunden > 0.0 && start.elapsed().as_secs_f64() >= sekunden {
                break "Zeit um".to_string();
            }
            
            
            
            
            
            
            let b = match if cid == WELT_CID {
                welt_bild_fest(&geteilt, w, h)
            } else {
                bild_holen(&geteilt, cid)
            } {
                Ok(b) => {
                    fehl_seit = None;
                    letztes_bild = Some(b.rgba.clone());
                    b
                }
                Err(e) => {
                    let seit = *fehl_seit.get_or_insert_with(std::time::Instant::now);
                    if seit.elapsed().as_secs_f64() > 2.0 {
                        break format!("Fenster liefert kein Bild mehr (seit 2s): {e}");
                    }
                    match &letztes_bild {
                        Some(rgba) => {
                            
                            if let Err(e) = ziel.write_all(rgba) {
                                break format!("Schreiben abgebrochen (Gegenstelle weg?): {e}");
                            }
                            n += 1;
                            faellig += takt;
                            let jetzt = std::time::Instant::now();
                            if faellig > jetzt {
                                std::thread::sleep(faellig - jetzt);
                            } else {
                                faellig = jetzt;
                            }
                            continue;
                        }
                        None => break format!("Fenster liefert kein Bild: {e}"),
                    }
                }
            };
            if b.w != w || b.h != h {
                
                
                
                break format!("Fenstergroesse wechselte {w}x{h} -> {}x{}", b.w, b.h);
            }
            if let Err(e) = ziel.write_all(&b.rgba) {
                break format!("Schreiben abgebrochen (Gegenstelle weg?): {e}");
            }
            n += 1;
            if n % (fps as u64) == 0 {
                stand_setzen(format!(
                    "{w}x{h} @{fps} -> {ziel_pfad}: {n} Bilder, {:.1}s",
                    start.elapsed().as_secs_f64()
                ));
            }
            faellig += takt;
            let jetzt = std::time::Instant::now();
            if faellig > jetzt {
                std::thread::sleep(faellig - jetzt);
            } else {
                
                
                faellig = jetzt;
            }
        };
        let _ = ziel.flush();
        stand_setzen(format!(
            "{grund} nach {n} Bildern in {:.1}s ({w}x{h} @{fps}, {ziel_pfad})",
            start.elapsed().as_secs_f64()
        ));
        
        
        
        crate::winbus::melde(&format!(
            "\"ereignis\":\"record_stop\",\"cid\":{cid},\"bilder\":{n},\"grund\":\"{}\"",
            crate::winbus::json_str(&grund)
        ));
        AUFNAHME_LAEUFT.store(false, Ordering::SeqCst);
    });

    
    
    
    
    let mb_je_min = (w as u64 * h as u64 * 4 * fps as u64 * 60) / (1024 * 1024);
    let pfad_hinweis = if pfad.starts_with("/work") {
        ""
    } else {
        " ⚠ pfad liegt NICHT unter /work — /tmp ist RAM und stirbt mit der zelle"
    };
    Ok(format!(
        "{pfad} {w}x{h} @{fps} — {} ebene{}, ~{mb_je_min} MB/min roh{pfad_hinweis} — \
         ffmpeg: -f rawvideo -pix_fmt rgba -s {w}x{h} -framerate {fps} -i {pfad}",
        erst.ebenen,
        if erst.ebenen == 1 { "" } else { "n" }
    ))
}

pub(crate) fn do_inject(shared: &Shared, cid: u64, keys: &[(u16, u32)]) -> Result<usize, String> {
    let (rcid, kbds, surface, writer, client_fd, mut serial, mut time) = {
        let g = shared.lock().unwrap();

        let (rcid, surface) = window_resources(&g, cid)?;
        let st = &g[&rcid];

        let kbds: Vec<u32> = if st.keyboards.is_empty() {
            st.keyboard.into_iter().collect()
        } else {
            st.keyboards.iter().take(1).copied().collect()
        };
        if kbds.is_empty() {
            return Err("client has no keyboard yet".into());
        }
        (rcid, kbds, surface, st.writer.clone(), st.client_fd, st.serial, st.time)
    };

    let mut out: Vec<u8> = Vec::new();
    let mut count = 0usize;

    
    
    
    
    if let Some(alt) = crate::winreg::fokus_wechsel(rcid, surface) {
        for &kbd in &kbds {
            let mut p = Vec::new();
            p_u32(&mut p, serial);
            serial += 1;
            p_u32(&mut p, alt);
            out.extend(event(kbd, 2, &p));
        }
    }

    for &kbd in &kbds {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        serial += 1;
        p_u32(&mut p, surface);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 1, &p));
    }

    let modifiers = |out: &mut Vec<u8>, kbd: u32, serial: u32, depressed: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, depressed);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 4, &p));
    };
    let key = |out: &mut Vec<u8>, kbd: u32, serial: u32, time: u32, code: u16, state: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, time);
        p_u32(&mut p, code as u32);
        p_u32(&mut p, state);
        out.extend(event(kbd, 3, &p));
    };

    for &(code, mask) in keys {
        
        
        let (tp, tr) = (time, time + 1);
        for &kbd in &kbds {
            if mask != 0 {
                modifiers(&mut out, kbd, serial, mask);
                serial += 1;
            }
            key(&mut out, kbd, serial, tp, code, 1);
            serial += 1;
            key(&mut out, kbd, serial, tr, code, 0);
            serial += 1;
            if mask != 0 {
                modifiers(&mut out, kbd, serial, 0);
                serial += 1;
            }
        }
        time += 2;
        count += 1;
    }

    {
        let _g = writer.lock().unwrap();
        sys::send_with_fds(client_fd, &out, &[]).map_err(|e| e.to_string())?;
    }
    if let Some(st) = shared.lock().unwrap().get_mut(&rcid) {
        st.serial = serial;
        st.time = time;
    }
    Ok(count)
}


pub(crate) fn do_popup_done(shared: &Shared, cid: u64) -> Result<usize, String> {
    let ziele = {
        let g = shared.lock().unwrap();
        let Some(fd) = g.get(&cid).map(|s| s.client_fd) else {
            return Err(format!("no such cid {cid}"));
        };
        g.values()
            .filter(|s| s.client_fd == fd && !s.popup_objs.is_empty())
            .map(|s| (s.client_fd, s.writer.clone(), s.popup_objs.clone()))
            .collect::<Vec<_>>()
    };
    let mut n = 0usize;
    for (fd, writer, objs) in &ziele {
        let mut out: Vec<u8> = Vec::new();
        for (_surf, obj) in objs {
            out.extend(event(*obj, 1, &[]));
            n += 1;
        }
        let _g = writer.lock().unwrap();
        sys::send_with_fds(*fd, &out, &[]).map_err(|e| e.to_string())?;
    }
    if n > 0 {
        let mut g = shared.lock().unwrap();
        if let Some(fd) = g.get(&cid).map(|s| s.client_fd) {
            for s in g.values_mut().filter(|s| s.client_fd == fd) {
                for surf in s.popup_objs.keys().copied().collect::<Vec<_>>() {
                    s.popup_surf.remove(&surf);
                }
                s.popup_objs.clear();
            }
        }
    }
    Ok(n)
}

pub(crate) fn do_inject_keycode(shared: &Shared, cid: u64, code: u16, mods: u32) -> Result<(), String> {
    let (rcid, kbds, surface, writer, client_fd, mut serial, mut time) = {
        let g = shared.lock().unwrap();
        let (rcid, surface) = window_resources(&g, cid)?;
        let st = &g[&rcid];
        let kbds: Vec<u32> = if st.keyboards.is_empty() {
            st.keyboard.into_iter().collect()
        } else {
            st.keyboards.iter().take(1).copied().collect()
        };
        if kbds.is_empty() {
            return Err("client has no keyboard yet".into());
        }
        (rcid, kbds, surface, st.writer.clone(), st.client_fd, st.serial, st.time)
    };

    let mut out: Vec<u8> = Vec::new();

    if let Some(alt) = crate::winreg::fokus_wechsel(rcid, surface) {
        for &kbd in &kbds {
            let mut p = Vec::new();
            p_u32(&mut p, serial);
            serial += 1;
            p_u32(&mut p, alt);
            out.extend(event(kbd, 2, &p));
        }
    }

    for &kbd in &kbds {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        serial += 1;
        p_u32(&mut p, surface);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 1, &p));
    }

    let modifiers = |out: &mut Vec<u8>, kbd: u32, serial: u32, depressed: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, depressed);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        p_u32(&mut p, 0);
        out.extend(event(kbd, 4, &p));
    };
    let key = |out: &mut Vec<u8>, kbd: u32, serial: u32, time: u32, code: u16, state: u32| {
        let mut p = Vec::new();
        p_u32(&mut p, serial);
        p_u32(&mut p, time);
        p_u32(&mut p, code as u32);
        p_u32(&mut p, state);
        out.extend(event(kbd, 3, &p));
    };

    let (tp, tr) = (time, time + 1);
    for &kbd in &kbds {
        if mods != 0 {
            modifiers(&mut out, kbd, serial, mods);
            serial += 1;
        }
        key(&mut out, kbd, serial, tp, code, 1);
        serial += 1;
        key(&mut out, kbd, serial, tr, code, 0);
        serial += 1;
        if mods != 0 {
            modifiers(&mut out, kbd, serial, 0);
            serial += 1;
        }
    }
    time += 2;

    {
        let _g = writer.lock().unwrap();
        sys::send_with_fds(client_fd, &out, &[]).map_err(|e| e.to_string())?;
    }
    if let Some(st) = shared.lock().unwrap().get_mut(&rcid) {
        st.serial = serial;
        st.time = time;
    }
    Ok(())
}

#[cfg(test)]
mod som_tests {
    use super::*;

    #[test]
    fn rect_ausserhalb_ist_ehrlich_false() {
        let mut px = vec![0u8; 10 * 10 * 4];
        assert!(!som_rect(&mut px, 10, 10, 20, 20, 5, 5, [255, 0, 0]));
        assert!(px.iter().all(|&b| b == 0), "nichts darf gemalt sein");
    }

    #[test]
    fn rect_im_bild_malt_den_rahmen() {
        let mut px = vec![0u8; 20 * 20 * 4];
        assert!(som_rect(&mut px, 20, 20, 2, 2, 10, 10, [255, 0, 0]));
        let ecke = (2usize * 20 + 2) * 4;
        assert_eq!(px[ecke], 255, "obere linke ecke traegt die farbe");
        let mitte = (7usize * 20 + 7) * 4;
        assert_eq!(px[mitte], 0, "die mitte bleibt unangetastet (nur rahmen)");
    }

    #[test]
    fn nummer_malt_ziffer_und_grund() {
        let mut px = vec![0u8; 40 * 40 * 4];
        som_nummer(&mut px, 40, 40, 5, 5, 7, [0, 255, 0]);
        
        assert!(px.chunks(4).any(|c| c[0] == 16 && c[1] == 16 && c[2] == 16));
        
        assert!(px.chunks(4).any(|c| c[1] == 255 && c[0] == 0));
    }
}
