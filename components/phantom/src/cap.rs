use std::io::Read;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Policy {
    Off,
    SameUid,
    Cold,
}

pub struct Gate {
    policy: Policy,
    our_uid: u32,
    token: String,
    token_path: String,
}

const ALWAYS_OK: &[&str] = &["", "list", "clients", "help", "cap", "cap-status", "xwin", "xmeta"];


const CONTENT_VERBS: &[&str] =
    &["keys", "sense", "snapshot", "record", "delta", "sicht", "such", "shot", "zoom", "som"];


pub fn immer_ok_verb(zeile: &str) -> bool {
    let verb = zeile.split([' ', '\t']).next().unwrap_or("");
    ALWAYS_OK.contains(&verb)
}

impl Gate {
    pub fn init(runtime: &str) -> Gate {
        let policy = match std::env::var("PHANTOM_CAP").as_deref() {
            Ok("cold") => Policy::Cold,
            Ok("off") => Policy::Off,
            _ => Policy::SameUid,
        };
        let our_uid = crate::sys::own_uid();
        let dir = format!("{runtime}/phantom");
        let _ = std::fs::create_dir_all(&dir);

        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
        let token_path = format!("{dir}/cap.token");
        let token = ensure_token(&token_path);
        Gate { policy, our_uid, token, token_path }
    }

    pub fn policy_name(&self) -> &'static str {
        match self.policy {
            Policy::Off => "off",
            Policy::SameUid => "same-uid",
            Policy::Cold => "cold",
        }
    }

    pub fn token_path(&self) -> &str {
        &self.token_path
    }

    pub fn status(&self) -> String {
        format!(
            "policy={} owner_uid={} token_file={}",
            self.policy_name(),
            self.our_uid,
            self.token_path
        )
    }

    
    
    
    
    pub fn authorize(&self, line: &str, peer_uid: Option<u32>) -> Result<(String, bool), String> {
        let (token_ok, eff) = match line.strip_prefix("tok:") {
            Some(rest) => {
                let mut sp = rest.splitn(2, ' ');
                let presented = sp.next().unwrap_or("");
                let cmd = sp.next().unwrap_or("").trim_start().to_string();
                (ct_eq(presented.as_bytes(), self.token.as_bytes()), cmd)
            }
            None => (false, line.to_string()),
        };

        
        let ohne_sitz = eff.strip_prefix("sitz:").and_then(|r| r.split_once(' ').map(|(_, c)| c)).unwrap_or(&eff);
        let verb = ohne_sitz.trim_start().split([' ', '\t']).next().unwrap_or("");

        let vertraut = match self.policy {
            Policy::Off => true,
            _ => token_ok || peer_uid.map_or(false, |u| u == self.our_uid),
        };

        if CONTENT_VERBS.contains(&verb) {
            
            let allow = match self.policy {
                Policy::Off => true,
                Policy::SameUid => token_ok || peer_uid.map_or(false, |u| u == self.our_uid),
                Policy::Cold => token_ok,
            };
            return if allow {
                Ok((eff, vertraut))
            } else {
                Err(format!(
                    "capability denied: '{verb}' liefert inhalte und braucht autorisierung \
                     (PHANTOM_CAP={}). tok:<token> {verb} … — token: {} (0600).",
                    self.policy_name(),
                    self.token_path
                ))
            };
        }

        if ALWAYS_OK.contains(&verb) {
            return Ok((eff, vertraut));
        }

        let allow = match self.policy {
            Policy::Off => true,

            Policy::SameUid => token_ok || peer_uid.map_or(true, |u| u == self.our_uid),

            Policy::Cold => token_ok,
        };

        if allow {
            Ok((eff, vertraut))
        } else {
            Err(format!(
                "capability denied: '{verb}' requires authorization (PHANTOM_CAP={}). \
                 Present the token:  tok:<token> {verb} …   — read it from {} (0600, owner-only).",
                self.policy_name(),
                self.token_path
            ))
        }
    }
}

fn ensure_token(path: &str) -> String {
    if let Ok(s) = std::fs::read_to_string(path) {
        let t = s.trim().to_string();
        if t.len() >= 32 {
            return t;
        }
    }
    let tok = mint_token();

    if let Ok(mut f) = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
    {
        use std::io::Write;
        let _ = f.write_all(tok.as_bytes());
    }
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    tok
}

fn mint_token() -> String {
    let mut buf = [0u8; 32];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .is_ok();
    if !ok {

        let seed = (std::process::id() as u64)
            ^ (&buf as *const _ as u64)
            ^ std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0);
        let mut x = seed | 1;
        for b in buf.iter_mut() {

            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            *b = (x & 0xff) as u8;
        }
    }
    let mut s = String::with_capacity(64);
    for b in buf {
        s.push(char::from_digit((b >> 4) as u32, 16).unwrap());
        s.push(char::from_digit((b & 0xf) as u32, 16).unwrap());
    }
    s
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}
