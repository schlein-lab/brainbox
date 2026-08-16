


#![allow(dead_code)]

const WOERTER_ROH: &str = include_str!("../geheim_woerter.txt");

fn woerter() -> &'static Vec<String> {
    static W: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
    W.get_or_init(|| {
        WOERTER_ROH
            .lines()
            .map(|z| z.trim().to_lowercase())
            .filter(|z| !z.is_empty() && !z.starts_with('#'))
            .collect()
    })
}

pub fn traegt_geheimnis(text: &str) -> bool {
    let klein = text.to_lowercase();
    woerter().iter().any(|w| klein.contains(w.as_str()))
}


pub fn redigiere(zeile: &str) -> String {
    let b: Vec<char> = zeile.chars().collect();
    let mut out = String::with_capacity(zeile.len());
    let mut i = 0usize;
    let mut letztes_bedeutsam = ' ';
    while i < b.len() {
        let c = b[i];
        if c != '"' {
            if !c.is_whitespace() {
                letztes_bedeutsam = c;
            }
            out.push(c);
            i += 1;
            continue;
        }
        
        let ist_wert = letztes_bedeutsam == ':';
        let start = i + 1;
        let mut j = start;
        let mut esc = false;
        while j < b.len() {
            let d = b[j];
            if esc {
                esc = false;
            } else if d == '\\' {
                esc = true;
            } else if d == '"' {
                break;
            }
            j += 1;
        }
        let inhalt: String = b[start..j.min(b.len())].iter().collect();
        if ist_wert && traegt_geheimnis(&inhalt) {
            out.push('"');
            out.push_str(&format!("(geheim, {} zeichen)", inhalt.chars().count()));
            out.push('"');
        } else {
            out.push('"');
            out.push_str(&inhalt);
            out.push('"');
        }
        letztes_bedeutsam = '"';
        i = (j + 1).min(b.len());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wortliste_geladen_und_zweisprachig() {
        let w = woerter();
        assert!(w.len() >= 20, "Liste unerwartet kurz: {}", w.len());
        assert!(w.iter().any(|x| x == "password"));
        assert!(w.iter().any(|x| x == "passwort"));
        assert!(!w.iter().any(|x| x.starts_with('#')), "Kommentare duerfen nicht mitgeladen werden");
    }

    #[test]
    fn wert_mit_geheimwort_wird_ersetzt_schluessel_nicht() {
        let z = r#""ereignis":"title_changed","cid":4,"titel":"Passwort ändern - Mozilla Firefox""#;
        let r = redigiere(z);
        assert!(!r.contains("Passwort ändern"), "{r}");
        assert!(r.contains("(geheim,"), "{r}");
        assert!(r.contains("\"titel\""), "Schluessel muss bleiben: {r}");
        
        let z2 = r#""token_file":"/run/user/1000/phantom/cap.token","cid":1"#;
        let r2 = redigiere(z2);
        assert!(r2.contains("\"token_file\""), "{r2}");
        
        assert!(r2.contains("(geheim,"), "{r2}");
    }

    #[test]
    fn saubere_zeile_bleibt_byte_gleich() {
        let z = r#""ereignis":"window_added","cid":3,"traeger":1,"pid":4711,"titel":"START-data.csv — LibreOffice Calc""#;
        assert_eq!(redigiere(z), z);
    }

    #[test]
    fn escapes_brechen_den_lauf_nicht() {
        let z = r#""titel":"sag \"passwort\" laut","cid":2"#;
        let r = redigiere(z);
        assert!(r.contains("(geheim,"), "{r}");
        assert!(r.ends_with("\"cid\":2"), "{r}");
    }
}
