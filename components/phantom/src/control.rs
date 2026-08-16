use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixListener;
use std::thread;

use phantom::input::{active_layout, char_to_key_layout, Modifier};
use phantom::sys;

use crate::act::{
    do_inject, do_record, do_record_status, do_record_stop, do_snapshot, sense_text,
    with_kbd,
};
use crate::compositor;
use crate::state::{Shared, GATE};
use crate::xwl::{cid_ready, xwl_set_title};

pub(crate) fn control_server(path: &str, shared: Shared) {
    
    
    
    
    
    
    if std::path::Path::new(path).exists() {
        match std::os::unix::net::UnixStream::connect(path) {
            Ok(_) => {
                eprintln!(
                    "phantom: control {path} ist BESETZT (ein anderer phantom lebt) — \
                     dieser Lauf bekommt keinen Steuerkanal. Eigener Kanal: PHANTOM_CTL=<pfad>"
                );
                return;
            }
            Err(_) => {
                
            }
        }
    }
    let _ = std::fs::remove_file(path);
    let listener = match UnixListener::bind(path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("phantom: cannot bind control socket {path}: {e}");
            return;
        }
    };
    for conn in listener.incoming() {
        let Ok(mut s) = conn else { continue };
        let shared = shared.clone();
        thread::spawn(move || {
            let mut line = String::new();
            let mut buf = [0u8; 4096];
            let mut r = match s.try_clone() {
                Ok(r) => r,
                Err(_) => return,
            };
            loop {
                match r.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        line.push_str(&String::from_utf8_lossy(&buf[..n]));
                        if line.contains('\n') {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }

            let peer = sys::peer_uid(s.as_raw_fd());
            let reply = match GATE.get() {
                Some(g) => match g.authorize(line.trim(), peer) {
                    Ok((eff, vertraut)) => {
                        
                        
                        let (sitz, cmd) = sitz_abtrennen(&eff);
                        let mut antwort = handle_control_v(cmd, &shared, vertraut);
                        
                        
                        if let Some(name) = sitz {
                            if vertraut && !phantom::cap::immer_ok_verb(cmd) {
                                if let Some(anhang) =
                                    phantom::sehwerk::weltdelta_anhang(name, crate::winbus::seit)
                                {
                                    if !antwort.ends_with('\n') {
                                        antwort.push('\n');
                                    }
                                    antwort.push_str(&anhang);
                                    antwort.push('\n');
                                }
                            }
                        }
                        antwort
                    }
                    Err(msg) => format!("error: {msg}\n"),
                },
                None => handle_control(line.trim(), &shared),
            };
            let _ = s.write_all(reply.as_bytes());
        });
    }
}


fn parse_key_spec(s: &str) -> Result<Vec<(u16, u32)>, String> {
    
    
    
    let mut maske: u32 = 0;
    let teile: Vec<&str> = s.split('+').map(|t| t.trim()).filter(|t| !t.is_empty()).collect();
    let Some((taste, mods)) = teile.split_last() else {
        return Err("key <code|name|ctrl+a|shift+ctrl+down>".to_string());
    };
    for m in mods {
        maske |= match m.to_ascii_lowercase().as_str() {
            "ctrl" | "control" | "strg" => 4,
            "shift" | "umschalt" => 1,
            "alt" | "meta" => 8,
            "altgr" => 128,
            other => {
                return Err(format!("unbekannter Modifikator {other:?} (ctrl|shift|alt|altgr)"))
            }
        };
    }
    
    let benannt: &[(&str, u16)] = &[
        ("return", 28), ("enter", 28), ("tab", 15), ("esc", 1), ("escape", 1),
        ("backspace", 14), ("delete", 111), ("entf", 111), ("home", 102), ("end", 107),
        ("up", 103), ("down", 108), ("left", 105), ("right", 106), ("space", 57),
        ("pageup", 104), ("pagedown", 109), ("insert", 110),
        
        
        ("f1", 59), ("f2", 60), ("f3", 61), ("f4", 62), ("f5", 63), ("f6", 64),
        ("f7", 65), ("f8", 66), ("f9", 67), ("f10", 68), ("f11", 87), ("f12", 88),
        ("menu", 127), ("kontextmenue", 127),
    ];
    let t = taste.to_ascii_lowercase();
    if let Some((_, code)) = benannt.iter().find(|(n, _)| *n == t) {
        return Ok(vec![(*code, maske)]);
    }
    
    
    
    
    if let Ok(code) = taste.parse::<u16>() {
        if mods.is_empty() {
            return Ok(vec![(code, maske)]);
        }
        if taste.len() > 1 {
            return Err(format!(
                "in einer kombination ist die zahl {taste:?} mehrdeutig (zeichen oder \
                 evdev-code?) — taste benennen oder einzelnes zeichen nutzen"
            ));
        }
    }
    let mut zeichen = taste.chars();
    let (Some(ch), None) = (zeichen.next(), zeichen.next()) else {
        return Err(format!("unbekannte Taste {taste:?} (Name, Zeichen oder evdev-Code)"));
    };
    match char_to_key_layout(ch, active_layout()) {
        
        
        Some((code, eigen)) => Ok(vec![(
            code,
            if mods.is_empty() { phantom::input::modifier_maske(eigen) } else { maske },
        )]),
        None => Err(format!("Taste {ch:?} liegt nicht auf diesem Layout")),
    }
}

fn parse_keys(sub: &str, arg: Option<&str>) -> Result<Vec<(u16, u32)>, String> {
    match sub {

        "text" | "type" => {
            let layout = active_layout();
            Ok(arg
                .unwrap_or("")
                .chars()
                .filter_map(|c| char_to_key_layout(c, layout))
                .map(|(code, m)| (code, phantom::input::modifier_maske(m)))
                .collect())
        }
        "enter" => Ok(vec![(28, 0)]),
        "key" => {
            let s = arg.unwrap_or("").trim();
            if s.is_empty() {
                return Err("key <code|name|ctrl+a|shift+ctrl+down>".to_string());
            }
            if let Ok(c) = s.parse::<u16>() {
                return Ok(vec![(c, 0)]);
            }
            parse_key_spec(s)
        }
        _ => Err("text <s> | enter | key <code>".to_string()),
    }
}

pub(crate) fn cid_by_title(shared: &Shared, substr: &str) -> Option<u64> {
    let g = shared.lock().unwrap();
    let q = substr.to_lowercase();
    g.iter()
        .filter(|(cid, _)| cid_ready(&g, **cid))
        .find(|(_, st)| st.title.as_deref().map(|t| t.to_lowercase().contains(&q)).unwrap_or(false))
        .map(|(cid, _)| *cid)
}

pub(crate) fn resolve_target(shared: &Shared, tok: &str) -> Option<u64> {
    if let Ok(cid) = tok.parse::<u64>() {
        return shared.lock().unwrap().contains_key(&cid).then_some(cid);
    }
    cid_by_title(shared, tok.strip_prefix('@').unwrap_or(tok))
}

fn parse_source(shared: &Shared, tok: &str) -> Result<compositor::SourceKind, String> {
    if let Some(hex) = tok.strip_prefix('#').or_else(|| tok.strip_prefix("solid:")) {
        if hex.len() == 6 {
            let r = u8::from_str_radix(&hex[0..2], 16).map_err(|_| "colour must be #RRGGBB")?;
            let g = u8::from_str_radix(&hex[2..4], 16).map_err(|_| "colour must be #RRGGBB")?;
            let b = u8::from_str_radix(&hex[4..6], 16).map_err(|_| "colour must be #RRGGBB")?;
            return Ok(compositor::SourceKind::Solid([r, g, b]));
        }
        return Err("colour must be #RRGGBB".into());
    }
    match resolve_target(shared, tok) {
        Some(cid) => Ok(compositor::SourceKind::Client(cid)),
        None => Err(format!("no such target {tok:?} (cid, @title, or #RRGGBB)")),
    }
}

fn parse_scene_add(shared: &Shared, a: &[&str]) -> Result<compositor::PlacementSpec, String> {
    use phantom::scene::{Crop, Fit, Rect};
    if a.len() < 5 {
        return Err("need <target> <dx> <dy> <dw> <dh>".into());
    }
    let src = parse_source(shared, a[0])?;
    let dx = a[1].parse::<i32>().map_err(|_| "dx not an int")?;
    let dy = a[2].parse::<i32>().map_err(|_| "dy not an int")?;
    let dw = a[3].parse::<u32>().map_err(|_| "dw not a uint")?;
    let dh = a[4].parse::<u32>().map_err(|_| "dh not a uint")?;
    let mut crop = None;
    let mut fit = Fit::Stretch;
    let mut z = 0i32;
    let mut opacity = 255u8;
    let mut i = 5;
    while i < a.len() {
        match a[i] {
            "crop" => {
                if i + 4 >= a.len() {
                    return Err("crop <sx> <sy> <sw> <sh>".into());
                }
                let x = a[i + 1].parse().map_err(|_| "crop sx")?;
                let y = a[i + 2].parse().map_err(|_| "crop sy")?;
                let w = a[i + 3].parse().map_err(|_| "crop sw")?;
                let h = a[i + 4].parse().map_err(|_| "crop sh")?;
                crop = Some(Crop { x, y, w, h });
                i += 5;
            }
            "fit" => {
                let f = a.get(i + 1).copied().ok_or("fit <mode>")?;
                fit = match f {
                    "stretch" => Fit::Stretch,
                    "contain" => Fit::Contain,
                    "cover" => Fit::Cover,
                    "none" => Fit::None,
                    _ => return Err("fit stretch|contain|cover|none".into()),
                };
                i += 2;
            }
            "z" => {
                z = a.get(i + 1).copied().ok_or("z <n>")?.parse().map_err(|_| "z not an int")?;
                i += 2;
            }
            "opacity" | "op" => {
                opacity = a.get(i + 1).copied().ok_or("opacity <n>")?.parse().map_err(|_| "opacity 0-255")?;
                i += 2;
            }
            other => return Err(format!("unknown option {other:?}")),
        }
    }
    Ok(compositor::PlacementSpec { src, crop, dest: Rect { x: dx, y: dy, w: dw, h: dh }, fit, z, opacity })
}


fn fenster_geo(
    g: &std::collections::HashMap<u64, crate::state::ClientState>,
    cid: u64,
) -> Option<(i32, i32)> {
    let st = g.get(&cid)?;
    let surf = st.surface?;
    
    let quelle = if st.buffers.is_empty() {
        st.xparent.and_then(|p| g.get(&p)).unwrap_or(st)
    } else {
        st
    };
    let b = st
        .surf_buffer
        .get(&surf)
        .or_else(|| quelle.surf_buffer.get(&surf))?;
    let info = quelle.buffers.get(b)?;
    Some((info.width, info.height))
}


fn commits_von(
    g: &std::collections::HashMap<u64, crate::state::ClientState>,
    cid: u64,
) -> u64 {
    let Some(fd) = g.get(&cid).map(|s| s.client_fd) else { return 0 };
    g.values().filter(|s| s.client_fd == fd).map(|s| s.commits).max().unwrap_or(0)
}


fn popup_offen(
    g: &std::collections::HashMap<u64, crate::state::ClientState>,
    cid: u64,
) -> bool {
    let Some(fd) = g.get(&cid).map(|s| s.client_fd) else { return false };
    g.values().filter(|s| s.client_fd == fd).any(|s| !s.popup_surf.is_empty())
}


fn echo_warten(shared: &Shared, cid: u64, basis: u64, basis_schaden: u64, frist_ms: u64) -> String {
    if std::env::var("PHANTOM_ACT_ECHO").ok().as_deref() == Some("0") {
        return String::new();
    }
    let start = std::time::Instant::now();
    loop {
        let (jetzt, rects, flaeche, popup) = {
            let g = shared.lock().unwrap();
            let n = commits_von(&g, cid);
            let (r, _) = schaden_von(&g, cid, basis_schaden);
            let f = fenster_geo(&g, cid).unwrap_or((0, 0));
            (n, r, f, popup_offen(&g, cid))
        };
        if jetzt > basis {
            
            
            let wo = if rects.is_empty() {
                String::new()
            } else {
                let ort = phantom::sehwerk::region_wort(&rects, flaeche);
                format!(", {} rect{} {}", rects.len(), if rects.len() == 1 { "" } else { "s" }, ort)
            };
            
            
            let grab = if popup {
                " · ACHTUNG: popup offen (unsichtbares menue? grab frisst \
                 tasten — karte liest es; `popup-zu <cid>` schliesst, esc \
                 wird oft gefressen)"
            } else {
                ""
            };
            return format!(
                "echo: ziel committete binnen {}ms (+{} commit{}{wo}){grab}\n",
                start.elapsed().as_millis(),
                jetzt - basis,
                if jetzt - basis == 1 { "" } else { "s" }
            );
        }
        let rest = frist_ms.saturating_sub(start.elapsed().as_millis() as u64);
        if rest == 0 {
            
            
            
            
            let (lebt, override_cid, popup) = {
                let g = shared.lock().unwrap();
                (
                    g.get(&cid).map(|s| s.surface.is_some()).unwrap_or(false),
                    compositor::focused_get(),
                    popup_offen(&g, cid),
                )
            };
            let zeuge = match (lebt, override_cid) {
                (false, _) => {
                    " zeuge: ziel-surface ist WEG (fenster zu?) — eingaben ohne empfaenger"
                        .to_string()
                }
                (true, Some(f)) if f != cid => format!(
                    " zeuge: focus-override zeigt auf cid {f}, nicht aufs ziel — `focus auto` loest"
                ),
                _ if popup => " zeuge: die verbindung haelt ein POPUP offen — \
                               unsichtbares menue grabbt vermutlich alle tasten \
                               (`popup-zu <cid>` schliesst protokollseitig; esc \
                               wird von leichen GEFRESSEN; karte liest das menue)"
                    .to_string(),
                _ => " zeuge: ziel-surface lebt, app schluckte die eingabe ohne zu zeichnen \
                      (GTK-fokus? ENDZUSTAND lesen, echo ist kein urteil)"
                    .to_string(),
            };
            return format!(
                "echo: KEIN commit des ziels binnen {frist_ms}ms —{zeuge}. \
                 pruefe ziel/fokus (list, await commit)\n"
            );
        }
        
        phantom::sehwerk::puls_warte(std::time::Duration::from_millis(rest.min(100)));
    }
}


const VERB_TAFEL: &[(&str, &str, &str)] = &[
    ("list", "", "fenster mit cid/pid/ready/titel/geo/commits; alias: clients"),
    ("clients", "", "alias fuer list"),
    ("act", "<cid|@titel> type|text|enter|key|move|click|drag|scroll …", "antwortet mit ergebnis-echo (commit-quittung); PHANTOM_ACT_ECHO=0 schaltet ab"),
    ("sense", "<cid|@titel> shot [pfad]|text|intent", ""),
    ("snapshot", "<cid> [pfad.png]", ""),
    ("record", "<cid|@titel|welt> <pfad> [fps] [sek] | stop | status", "welt = ALLE fenster inkl. dialoge als stapel; nennt vorab masse/ebenen/datenrate; pfad gehoert nach /work"),
    ("await", "ready|title|commit|gone|damage <ziel> <frist_s> | stable <ziel> <ruhe_s> <frist_s>", "blockiert bis flanke oder frist; frist ist PFLICHT; damage/stable = sehwerk-flanken"),
    ("delta", "<seit_seq>", "bus-ereignisse seit seq (redigiert, gedeckelt); re-sync fuer den weltdelta-anhang"),
    ("verbs", "", "diese auskunft, eine json-zeile je verb"),
    ("focus", "[cid|auto]", ""),
    ("popup-zu", "<cid|@titel>", "schliesst offene xdg_popups protokollseitig (popup_done) — heilt escape-resistente menue-leichen"),
    ("inject", "<cid> <keys…>", ""),
    ("inject-title", "<substr> <keys…>", ""),
    ("keys", "", "NUR im proxy-modus gefuellt — in der zelle immer leer"),
    ("kbd", "type <s>|enter|key <code>", "systemweit ueber uinput"),
    ("dock", "…", "nur compositor-modus"),
    ("room", "…", "nur compositor-modus"),
    ("launch", "<cmd…>", ""),
    ("spawn", "<cmd…>", ""),
    ("spielwiese", "[cid|auto]", "alias: foreground, fg"),
    ("foreground", "[cid|auto]", "alias von spielwiese"),
    ("fg", "[cid|auto]", "alias von spielwiese"),
    ("scene", "get|clear|set|add …", "virtuelle bildschirm-platzierung"),
    ("screen", "move|click|drag|scroll|text|key …", "eingabe in BILDSCHIRM-koordinaten; braucht compositor-eingabe"),
    ("cap", "", "capability-gate zeigen; alias: cap-status"),
    ("cap-status", "", "alias von cap"),
    ("xwin", "<serial> <titel>", "intern: xwm traegt X11-titel nach"),
    ("xmeta", "<serial> <pid> <klasse>", "intern: xwm traegt X11-pid+klasse nach (statt 'xwayland')"),
    ("zoom", "<cid|@titel> <x> <y> <w> <h> [pfad]", "ausschnitt scharf hochgezogen (faktor 1-4, ziel ~512px); default JPEG"),
    ("som", "<cid|@titel> <x y w h ...> [pfad]", "vollbild mit nummerierten ziel-rechtecken (set-of-marks); default JPEG"),
    ("notiz", "<text>", "eigene zeile in den welt-strom (redigiert, gedeckelt); erscheint im weltdelta aller sitze"),
];


fn sitz_abtrennen(zeile: &str) -> (Option<&str>, &str) {
    match zeile.strip_prefix("sitz:") {
        Some(rest) => match rest.split_once(' ') {
            Some((name, cmd)) => (Some(name), cmd.trim_start()),
            None => (Some(rest), ""),
        },
        None => (None, zeile),
    }
}


fn schaden_von(
    g: &std::collections::HashMap<u64, crate::state::ClientState>,
    cid: u64,
    basis: u64,
) -> (Vec<(i32, i32, i32, i32)>, u64) {
    let Some(fd) = g.get(&cid).map(|s| s.client_fd) else { return (Vec::new(), basis) };
    let mut alle = Vec::new();
    let mut max = basis;
    for st in g.values().filter(|s| s.client_fd == fd) {
        for ring in st.surf_damage.values() {
            let (rects, m) = phantom::sehwerk::schaden_seit(ring, basis);
            alle.extend(rects);
            if m > max {
                max = m;
            }
        }
    }
    (alle, max)
}


fn handle_control(line: &str, shared: &Shared) -> String {
    handle_control_v(line, shared, true)
}

fn handle_control_v(line: &str, shared: &Shared, vertraut: bool) -> String {
    let mut it = line.splitn(4, ' ');
    match it.next().unwrap_or("") {
        "clients" | "list" => {
            let fg = compositor::foreground_cid(shared);
            let g = shared.lock().unwrap();
            if g.is_empty() {
                return "(no clients connected)\n".into();
            }
            let mut ids: Vec<&u64> = g.keys().collect();
            ids.sort();
            let mut out = String::new();
            for cid in ids {
                let st = &g[cid];

                let mark = if Some(*cid) == fg { " *" } else { "" };
                
                
                let popup = if !st.popup_surf.is_empty() { " POPUP-OFFEN" } else { "" };
                let geo = fenster_geo(&g, *cid)
                    .map(|(w, h)| format!("{w}x{h}"))
                    .unwrap_or_else(|| "?".into());
                
                
                let titel = if vertraut { st.title.as_deref().unwrap_or("") } else { "—" };
                let app = if vertraut { st.app_id.as_deref().unwrap_or("") } else { "—" };
                out.push_str(&format!(
                    "cid={cid} pid={} ready={} geo={geo} commits={} title={:?} app_id={:?}{mark}{popup}\n",
                    st.pid.map(|p| p.to_string()).unwrap_or_else(|| "?".into()),
                    cid_ready(&g, *cid),
                    commits_von(&g, *cid),
                    titel,
                    app,
                ));
            }
            out
        }

        "verbs" => {
            let mut out = String::new();
            for (name, sig, hinweis) in VERB_TAFEL {
                out.push_str(&format!(
                    "{{\"verb\":\"{name}\",\"signatur\":\"{sig}\",\"hinweis\":\"{hinweis}\"}}\n"
                ));
            }
            out
        }

        "await" => {
            
            
            
            
            const HILFE: &str = "usage: await ready|title|commit|gone|damage <ziel> <frist_s> | await stable <ziel> <ruhe_s> <frist_s>\n\
                \x20 ready:  ziel existiert und ist bedienbar\n\
                \x20 title:  irgendein fenstertitel enthaelt <ziel>\n\
                \x20 commit: das ziel zeichnet (mindestens ein commit NACH aufrufbeginn)\n\
                \x20 damage: das ziel meldet schadensgeometrie NACH aufrufbeginn\n\
                \x20 stable: das ziel ist seit <ruhe_s> ohne neue schadensgeometrie\n\
                \x20 gone:   das ziel ist verschwunden\n";
            let was = it.next().unwrap_or("");
            
            
            
            
            let mut rest = String::new();
            if let Some(a) = it.next() {
                rest.push_str(a);
            }
            if let Some(b) = it.next() {
                if !rest.is_empty() {
                    rest.push(' ');
                }
                rest.push_str(b);
            }
            let Some((ziel, frist_s)) = rest.trim_end().rsplit_once(' ') else {
                return HILFE.into();
            };
            let mut ziel = ziel.trim();
            let frist: f64 = match frist_s.parse() {
                Ok(f) if (0.1..=600.0).contains(&f) => f,
                _ => return HILFE.into(),
            };
            
            let mut ruhe: f64 = 0.0;
            if was == "stable" {
                let Some((z2, ruhe_s)) = ziel.rsplit_once(' ') else {
                    return HILFE.into();
                };
                ruhe = match ruhe_s.parse() {
                    Ok(r) if (0.05..=60.0).contains(&r) => r,
                    _ => return HILFE.into(),
                };
                ziel = z2.trim();
            }
            if ziel.is_empty()
                || !matches!(was, "ready" | "title" | "commit" | "gone" | "damage" | "stable")
            {
                return HILFE.into();
            }
            let start = std::time::Instant::now();
            let mut basis: Option<u64> = None;
            
            let schaden_basis = phantom::sehwerk::aktuelle_seq();
            let mut letzter_stand: u64 = schaden_basis;
            let mut ruhig_seit = std::time::Instant::now();
            loop {
                let treffer = resolve_target(shared, ziel);
                let erfuellt = match was {
                    "gone" => treffer.is_none(),
                    "damage" => match treffer {
                        None => false,
                        Some(c) => {
                            let g = shared.lock().unwrap();
                            let (r, _) = schaden_von(&g, c, schaden_basis);
                            !r.is_empty()
                        }
                    },
                    "stable" => match treffer {
                        None => false,
                        Some(c) => {
                            let stand = {
                                let g = shared.lock().unwrap();
                                let (_, m) = schaden_von(&g, c, 0);
                                m
                            };
                            if stand != letzter_stand {
                                letzter_stand = stand;
                                ruhig_seit = std::time::Instant::now();
                            }
                            ruhig_seit.elapsed().as_secs_f64() >= ruhe
                        }
                    },
                    "title" => {
                        let g = shared.lock().unwrap();
                        g.values().any(|s| {
                            s.title.as_deref().map_or(false, |t| t.contains(ziel))
                        })
                    }
                    "ready" => match treffer {
                        None => false,
                        Some(c) => {
                            let g = shared.lock().unwrap();
                            cid_ready(&g, c)
                        }
                    },
                    "commit" => match treffer {
                        None => false,
                        Some(c) => {
                            let g = shared.lock().unwrap();
                            let n = commits_von(&g, c);
                            match basis {
                                None => {
                                    basis = Some(n);
                                    false
                                }
                                Some(b) => n > b,
                            }
                        }
                    },
                    _ => unreachable!(),
                };
                if erfuellt {
                    let cid = resolve_target(shared, ziel)
                        .map(|c| format!(" cid={c}"))
                        .unwrap_or_default();
                    return format!(
                        "ok: {was}{cid} nach {:.2}s\n",
                        start.elapsed().as_secs_f64()
                    );
                }
                if start.elapsed().as_secs_f64() >= frist {
                    
                    let g = shared.lock().unwrap();
                    let mut ist = String::new();
                    let mut ids: Vec<&u64> = g.keys().collect();
                    ids.sort();
                    for cid in ids {
                        let st = &g[cid];
                        ist.push_str(&format!(
                            "  cid={cid} ready={} title={:?}\n",
                            cid_ready(&g, *cid),
                            st.title.as_deref().unwrap_or("")
                        ));
                    }
                    if ist.is_empty() {
                        ist.push_str("  (keine fenster)\n");
                    }
                    return format!(
                        "timeout: {was} {ziel:?} nicht binnen {frist:.1}s — ist-zustand:\n{ist}"
                    );
                }
                
                
                phantom::sehwerk::puls_warte(std::time::Duration::from_millis(200));
            }
        }
        "delta" => {
            
            
            let Some(seit) = it.next().and_then(|s| s.parse::<u64>().ok()) else {
                return "usage: delta <seit_seq>   (seq 0 = alles im ring)\n".into();
            };
            let (zeilen, aelteste, neueste) = crate::winbus::seit(seit);
            let mut out = String::new();
            if seit > 0 && aelteste > seit + 1 {
                out.push_str(&format!(
                    "luecke: ereignisse seq {}–{} nicht mehr im ring\n",
                    seit + 1,
                    aelteste.saturating_sub(1)
                ));
            }
            const DECKEL: usize = 200;
            for (i, (_, z)) in zeilen.iter().enumerate() {
                if i >= DECKEL {
                    out.push_str(&format!("… +{} weitere (ring bis seq {neueste})\n", zeilen.len() - DECKEL));
                    break;
                }
                out.push_str(z);
            }
            if out.is_empty() {
                out.push_str(&format!("(nichts neues seit seq {seit}; ring bis seq {neueste})\n"));
            }
            out
        }
        "cap" | "cap-status" => match GATE.get() {
            Some(g) => format!("{}\n", g.status()),
            None => "policy=off (gate not initialised)\n".into(),
        },
        "xwin" => {

            let rest = line.strip_prefix("xwin ").unwrap_or("");
            let (ser, title) = rest.split_once(' ').unwrap_or((rest, ""));
            match ser.parse::<u64>() {
                Ok(serial) if xwl_set_title(shared, serial, title) => "ok\n".into(),
                Ok(_) => "ok: no window for that serial (yet)\n".into(),
                Err(_) => "usage: xwin <serial> <title>\n".into(),
            }
        }
        "xmeta" => {
            
            
            
            
            let rest = line.strip_prefix("xmeta ").unwrap_or("");
            let mut sp = rest.splitn(3, ' ');
            let ser = sp.next().unwrap_or("").parse::<u64>();
            let pid = sp.next().unwrap_or("").parse::<i32>().ok();
            let klasse = sp.next().unwrap_or("").trim();
            match ser {
                Ok(serial) if crate::xwl::xwl_set_meta(shared, serial, pid, klasse) => "ok\n".into(),
                Ok(_) => "ok: no window for that serial (yet)\n".into(),
                Err(_) => "usage: xmeta <serial> <pid> <klasse>\n".into(),
            }
        }
        "zoom" => {
            const HILFE: &str = "usage: zoom <cid|@titel> <x> <y> <w> <h> [pfad]\n\
                \x20 ausschnitt des fensterbilds, ganzzahlig hochgezogen (ziel ~512px breite, faktor 1-4)\n\
                \x20 default JPEG q85; .png-endung = verlustfrei\n";
            let toks: Vec<&str> = line.split_whitespace().collect();
            if toks.len() < 6 {
                return HILFE.into();
            }
            let Some(cid) = resolve_target(shared, toks[1]) else {
                return format!("error: kein Fenster {:?}\n", toks[1]);
            };
            let mut z = [0i64; 4];
            for (i, t) in toks[2..6].iter().enumerate() {
                match t.parse() {
                    Ok(v) => z[i] = v,
                    Err(_) => return HILFE.into(),
                }
            }
            match crate::act::do_zoom(shared, cid, z[0], z[1], z[2], z[3], toks.get(6).map(|s| s.to_string())) {
                Ok(p) => format!("ok: {p}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "som" => {
            
            
            const HILFE: &str = "usage: som <cid|@titel> <x> <y> <w> <h> [x y w h ...] [pfad]\n\
                \x20 vollbild mit markierten zielen (2px-rahmen + nummer); default JPEG q85\n";
            let toks: Vec<&str> = line.split_whitespace().collect();
            if toks.len() < 6 {
                return HILFE.into();
            }
            let Some(cid) = resolve_target(shared, toks[1]) else {
                return format!("error: kein Fenster {:?}\n", toks[1]);
            };
            let mut zahlen: Vec<i64> = Vec::new();
            let mut pfad: Option<String> = None;
            for t in &toks[2..] {
                match t.parse::<i64>() {
                    Ok(v) => zahlen.push(v),
                    Err(_) => {
                        if pfad.is_some() {
                            return HILFE.into();
                        }
                        pfad = Some(t.to_string());
                    }
                }
            }
            if zahlen.is_empty() || zahlen.len() % 4 != 0 {
                return HILFE.into();
            }
            let ziele: Vec<(i64, i64, i64, i64)> = zahlen
                .chunks_exact(4)
                .map(|c| (c[0], c[1], c[2], c[3]))
                .collect();
            match crate::act::do_som(shared, cid, &ziele, pfad) {
                Ok(p) => format!("ok: {p}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "notiz" => {
            
            
            
            
            
            if !vertraut {
                return "error: notiz braucht autorisierung (token oder gleiche uid)\n".into();
            }
            let text = line.strip_prefix("notiz").unwrap_or("").trim();
            if text.is_empty() {
                return "usage: notiz <text>   (eine zeile, gedeckelt 400 zeichen)\n".into();
            }
            let kurz: String = text.chars().take(400).collect();
            crate::winbus::melde(&format!(
                "\"ereignis\":\"notiz\",\"text\":\"{}\"",
                crate::winbus::json_str(&kurz)
            ));
            "ok: notiert\n".into()
        }
        "inject" => {
            let Some(cid) = it.next().and_then(|s| s.parse::<u64>().ok()) else {
                return "usage: inject <cid> text <s> | enter | key <code>\n".into();
            };
            let keys = match parse_keys(it.next().unwrap_or(""), it.next()) {
                Ok(k) => k,
                Err(e) => return format!("usage: inject <cid> {e}\n"),
            };
            match do_inject(shared, cid, &keys) {
                Ok(n) => format!("ok: injected {n} into cid={cid}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "inject-title" => {
            let substr = it.next().unwrap_or("");
            if substr.is_empty() {
                return "usage: inject-title <substr> text <s> | enter | key <code>\n".into();
            }
            let keys = match parse_keys(it.next().unwrap_or(""), it.next()) {
                Ok(k) => k,
                Err(e) => return format!("usage: inject-title <substr> {e}\n"),
            };
            let Some(cid) = cid_by_title(shared, substr) else {
                return format!("error: no ready client with title containing {substr:?}\n");
            };
            match do_inject(shared, cid, &keys) {
                Ok(n) => format!("ok: injected {n} into cid={cid} (title~{substr:?})\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }
        "snapshot" => {
            let Some(cid) = it.next().and_then(|s| s.parse::<u64>().ok()) else {
                return "usage: snapshot <cid> [path.png]\n".into();
            };
            let path = it.next().map(|s| s.to_string());
            match do_snapshot(shared, cid, path) {
                Ok(p) => format!("ok: {p}\n"),
                Err(e) => format!("error: {e}\n"),
            }
        }

        "act" => {
            let Some(cid) = it.next().and_then(|t| resolve_target(shared, t)) else {
                return "usage: act <cid|@title> type <s>|enter|key <code>|move <x> <y>|click <x> <y> [btn]|drag <x1> <y1> <x2> <y2> [btn]|scroll <x> <y> <n>\n".into();
            };
            let sub = it.next().unwrap_or("");
            
            
            
            let basis = {
                let g = shared.lock().unwrap();
                commits_von(&g, cid)
            };
            let basis_schaden = phantom::sehwerk::aktuelle_seq();
            let ausgang = match sub {
                "move" | "click" | "drag" | "scroll" => {
                    let args: Vec<&str> = it.next().unwrap_or("").split_whitespace().collect();
                    compositor::act_pointer(shared, cid, sub, &args)
                }
                _ => {
                    let keys = match parse_keys(sub, it.next()) {
                        Ok(k) => k,
                        Err(e) => return format!("usage: act <target> {e}\n"),
                    };
                    do_inject(shared, cid, &keys)
                        .map(|n| format!("ok: acted {n} on cid={cid}\n"))
                }
            };
            match ausgang {
                Err(e) => format!("error: {e}\n"),
                Ok(mut s) => {
                    if !s.ends_with('\n') {
                        s.push('\n');
                    }
                    s.push_str(&echo_warten(shared, cid, basis, basis_schaden, 800));
                    s
                }
            }
        }

        "focus" => match it.next() {
            None => match compositor::focused_get() {
                None => "focus: auto (highest renderable cid)\n".into(),
                Some(f) => format!("focus: cid={f}\n"),
            },
            Some("auto") => {
                compositor::set_focused_auto();
                "ok: focus -> auto\n".into()
            }
            Some(tok) => match resolve_target(shared, tok) {
                Some(cid) => {
                    compositor::set_focused(cid);
                    format!("ok: focus -> cid={cid}\n")
                }
                None => format!("error: no such target {tok:?} (cid, @title, or 'auto')\n"),
            },
        },

        "popup-zu" => match it.next().and_then(|t| resolve_target(shared, t)) {
            None => "usage: popup-zu <cid|@titel> — schliesst offene xdg_popups der \
                     verbindung PROTOKOLLSEITIG (popup_done); escape wird von \
                     menue-leichen gefressen (roulette 3/3)\n"
                .into(),
            Some(cid) => match crate::act::do_popup_done(shared, cid) {
                Ok(0) => format!("ok: keine offenen popups an cid={cid}\n"),
                Ok(n) => format!("ok: {n} popup(s) geschlossen (popup_done) an cid={cid}\n"),
                Err(e) => format!("error: {e}\n"),
            },
        },

        "dock" => match it.next() {
            Some("show") => {
                compositor::set_dock_mode(1);
                "ok: dock -> show\n".into()
            }
            Some("hide") => {
                compositor::set_dock_mode(2);
                "ok: dock -> hide\n".into()
            }
            Some("auto") | None => {
                compositor::set_dock_mode(0);
                "ok: dock -> auto (cursor-revealed)\n".into()
            }
            Some(other) => format!("usage: dock show|hide|auto (got {other:?})\n"),
        },

        "spielwiese" | "foreground" | "fg" => match it.next() {
            None => format!(
                "spielwiese: {}\n",
                if compositor::show_clients() { "on" } else { "off" }
            ),
            Some("off") | Some("hide") => {
                compositor::set_show_clients(false);
                "ok: spielwiese off (shell + rooms only)\n".into()
            }
            Some("on") | Some("show") => {
                compositor::set_show_clients(true);
                "ok: spielwiese on (clients composited)\n".into()
            }
            Some("auto") => {
                compositor::set_show_clients(true);
                compositor::set_focused_auto();
                "ok: spielwiese on -> focus auto\n".into()
            }
            Some(tok) => match resolve_target(shared, tok) {
                Some(cid) => {
                    compositor::set_show_clients(true);
                    compositor::set_focused(cid);
                    format!("ok: spielwiese on -> focus cid={cid}\n")
                }
                None => format!("error: no such target {tok:?} (cid, @title, on, off, auto)\n"),
            },
        },

        "room" => match it.next() {
            Some("type") => {
                let text: Vec<&str> = it.collect();
                let text = text.join(" ");
                compositor::room_input_push(&text);
                format!("ok: room type (+{} chars)\n", text.len())
            }
            Some("key") => match it.next() {
                Some("back") | Some("backspace") => {
                    compositor::room_input_backspace();
                    "ok: room backspace\n".into()
                }
                Some("enter") => {
                    compositor::room_input_submit();
                    "ok: room submit\n".into()
                }
                other => format!("usage: room key back|enter (got {other:?})\n"),
            },
            Some("submit") => {
                compositor::room_input_submit();
                "ok: room submit\n".into()
            }
            Some("clear") => {
                compositor::room_input_clear();
                "ok: room clear\n".into()
            }
            Some("get") | None => format!("room input: {:?}\n", compositor::room_input_snapshot()),
            Some(other) => {
                format!("usage: room type <text> | key back|enter | submit | clear | get (got {other:?})\n")
            }
        },

        "launch" => {
            let prog = it.next().unwrap_or("").to_ascii_uppercase();
            if prog.is_empty() {
                return "usage: launch <TERMINAL|FILES|BROWSER|CODE|CLAUDE|SETTINGS|CALENDAR>\n"
                    .into();
            }
            compositor::launch_program(shared, &prog);
            format!("ok: launching {prog}\n")
        }

        "spawn" => {
            let cmd = line.strip_prefix("spawn ").unwrap_or("").trim();
            if cmd.is_empty() {
                return "usage: spawn <command line>  (e.g. spawn gnome-calculator | spawn snap-run firefox)\n".into();
            }
            compositor::spawn_command(shared, cmd);
            format!("ok: spawning {cmd}\n")
        }
        "sense" => {
            let cid = match it.next() {
                Some("welt") => crate::act::WELT_CID,
                Some(t) => match resolve_target(shared, t) {
                    Some(c) => c,
                    None => return "usage: sense <cid|@title|welt> shot [path] | text | intent\n".into(),
                },
                None => return "usage: sense <cid|@title|welt> shot [path] | text | intent\n".into(),
            };
            match it.next().unwrap_or("shot") {
                "shot" => match do_snapshot(shared, cid, it.next().map(|s| s.to_string())) {
                    Ok(p) => format!("ok: {p}\n"),
                    Err(e) => format!("error: {e}\n"),
                },

                "text" | "intent" => sense_text(shared, cid),
                other => format!("unknown sense: {other} (shot|text|intent)\n"),
            }
        }

        "record" => {
            
            
            
            
            const HILFE: &str =
                "usage: record <cid|@title|welt> <pfad> [fps] [sekunden] | record stop | record status\n\
                 \x20 welt = ALLE fenster inkl. dialoge als stapel (A3-kamera-luecke)\n\
                 \x20 <pfad> darf ein FIFO sein: mkfifo /work/take.raw, dann ffmpeg daranhaengen\n";
            match it.next().unwrap_or("") {
                "" => HILFE.into(),
                "stop" => do_record_stop(),
                "status" => do_record_status(),
                ziel => {
                    
                    
                    let cid = if ziel == "welt" {
                        crate::act::WELT_CID
                    } else {
                        let Some(c) = resolve_target(shared, ziel) else {
                            return format!("error: kein Fenster {ziel:?}\n");
                        };
                        c
                    };
                    let pfad = it.next().unwrap_or("");
                    if pfad.is_empty() {
                        return HILFE.into();
                    }
                    let rest: Vec<&str> = it.next().unwrap_or("").split_whitespace().collect();
                    let fps = rest.first().and_then(|s| s.parse::<u32>().ok()).unwrap_or(30);
                    let sek = rest.get(1).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                    match do_record(shared, cid, pfad.to_string(), fps, sek) {
                        Ok(s) => format!("ok: {s}\n"),
                        Err(e) => format!("error: {e}\n"),
                    }
                }
            }
        }

        "keys" => {
            let Some(cid) = it.next().and_then(|t| resolve_target(shared, t)) else {
                return "usage: keys <cid|@title> [clear]\n".into();
            };
            let clear = it.next() == Some("clear");
            let mut g = shared.lock().unwrap();
            let Some(st) = g.get_mut(&cid) else { return "error: no such client\n".into() };
            let out = st.keylog.clone();
            if clear {
                st.keylog.clear();
            }
            format!("{out}\n")
        }

        "kbd" => {

            match line.split_whitespace().nth(1).unwrap_or("") {
                "type" => {
                    let text = line.strip_prefix("kbd type ").unwrap_or("");
                    with_kbd(|vi| vi.type_str(text))
                }
                "enter" => with_kbd(|vi| vi.enter()),
                "key" => {
                    let s = line.split_whitespace().nth(2).unwrap_or("");
                    
                    
                    
                    
                    if let Ok(code) = s.parse::<u16>() {
                        with_kbd(|vi| vi.key(code))
                    } else {
                        match parse_key_spec(s) {
                            Ok(keys) => with_kbd(|vi| {
                                for &(code, maske) in &keys {
                                    vi.kombi(code, maske)?;
                                }
                                Ok(())
                            }),
                            Err(e) => format!("err: {e}\n"),
                        }
                    }
                }

                "map" => {
                    let chars: String = line.strip_prefix("kbd map ").unwrap_or("").into();
                    let layout = active_layout();
                    let mut out = format!("layout={layout:?}\n");
                    for ch in chars.chars() {
                        match char_to_key_layout(ch, layout) {
                            Some((code, modi)) => {
                                out.push_str(&format!("{ch:?} -> ({code},{modi:?})\n"))
                            }
                            None => out.push_str(&format!("{ch:?} -> (skip)\n")),
                        }
                    }
                    out
                }
                _ => "err: kbd type <text> | kbd enter | kbd key <code> | kbd map <chars>\n".into(),
            }
        }

        "scene" => {
            let toks: Vec<&str> = line.split_whitespace().collect();
            match toks.get(1).copied() {
                None | Some("get") | Some("status") => compositor::scene_describe(),
                Some("clear") | Some("off") => {
                    compositor::scene_clear();
                    "ok: scene cleared (default single-foreground compositing)\n".into()
                }
                Some("set") | Some("add") => {
                    if toks[1] == "set" {
                        compositor::scene_clear();
                    }
                    match parse_scene_add(shared, &toks[2..]) {
                        Ok(spec) => {
                            compositor::scene_add(spec);
                            format!("ok: scene now {} placement(s)\n", compositor::scene_len())
                        }
                        Err(e) => format!(
                            "usage: scene add <cid|@title|#RRGGBB> <dx> <dy> <dw> <dh> [crop <sx> <sy> <sw> <sh>] [fit stretch|contain|cover|none] [z <n>] [opacity <0-255>]\n  ({e})\n"
                        ),
                    }
                }
                Some(other) => format!(
                    "usage: scene [get] | clear | set|add <target> <dx> <dy> <dw> <dh> [crop ...] [fit ...] [z ...] [opacity ...] (got {other:?})\n"
                ),
            }
        }

        "screen" => {
            let toks: Vec<&str> = line.split_whitespace().collect();
            let action = toks.get(1).copied().unwrap_or("");
            if action.is_empty() {
                return "usage: screen move|click|drag|scroll <x> <y> [..] | text <s> | key <code> | keycode <code> [modmask] | enter\n".into();
            }
            match compositor::screen_input(shared, action, &toks[2..]) {
                Ok(s) => s,
                Err(e) => format!("error: {e}\n"),
            }
        }
        "" => "phantom hub. targets = <cid>|@<title>. commands: list/clients | act <t> type|enter|key | sense <t> shot|text | scene set <t> <x> <y> <w> <h> | screen click <x> <y> | kbd type|enter|key (system-wide uinput) | (inject, inject-title, snapshot aliases)\n".into(),
        other => format!("unknown command: {other}\n"),
    }
}
