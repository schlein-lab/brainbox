use super::forge::act_pointer;
use super::pick::{focused_cid, focused_geom, foreground_cid, over_client};
use super::state::{seat_size, wake_present};
use crate::act::{do_inject, do_inject_keycode};
use crate::Shared;
use phantom::input::{active_layout, char_to_key_layout};

pub fn screen_input(shared: &Shared, action: &str, args: &[&str]) -> Result<String, String> {
    match action {
        "move" | "click" | "drag" | "scroll" => screen_pointer(shared, action, args),
        "text" | "type" => {
            let s = args.join(" ");
            if s.is_empty() {
                return Err("text <string>".into());
            }
            let layout = active_layout();
            let keys: Vec<(u16, u32)> = s
                .chars()
                .filter_map(|c| char_to_key_layout(c, layout))
                .map(|(code, m)| (code, phantom::input::modifier_maske(m)))
                .collect();
            let cid = focused_cid(shared).ok_or("no focused window")?;
            let n = do_inject(shared, cid, &keys)?;

            wake_present();
            Ok(format!("ok: typed {n} char(s) into cid={cid}\n"))
        }
        "key" => {
            let code: u16 = args.first().and_then(|s| s.parse().ok()).ok_or("key <evdev-code>")?;
            let cid = focused_cid(shared).ok_or("no focused window")?;
            do_inject(shared, cid, &[(code, 0)])?;
            wake_present();
            Ok(format!("ok: key {code} -> cid={cid}\n"))
        }

        "keycode" => {
            let code: u16 = args.first().and_then(|s| s.parse().ok()).ok_or("keycode <evdev-code> [modmask]")?;
            let mods: u32 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
            let cid = focused_cid(shared).ok_or("no focused window")?;
            do_inject_keycode(shared, cid, code, mods)?;
            wake_present();
            Ok(format!("ok: keycode {code} mods={mods} -> cid={cid}\n"))
        }
        "enter" => {
            let cid = focused_cid(shared).ok_or("no focused window")?;
            do_inject(shared, cid, &[(28, 0)])?;
            wake_present();
            Ok(format!("ok: enter -> cid={cid}\n"))
        }
        other => Err(format!(
            "unknown screen action {other:?} (move|click|drag|scroll|text|key|keycode|enter)"
        )),
    }
}

fn screen_pointer(shared: &Shared, action: &str, args: &[&str]) -> Result<String, String> {
    let (sw, sh) = seat_size();
    if sw == 0 || sh == 0 {
        return Err("seat size unknown (is the compositor running?)".into());
    }
    let cid = foreground_cid(shared).ok_or("no foreground window")?;
    let (ox, oy, w, h) = focused_geom(shared, sw, sh).ok_or("no foreground geometry")?;
    let num = |i: usize| -> Result<i64, String> {
        args.get(i)
            .ok_or_else(|| "missing coordinate".to_string())?
            .parse::<i64>()
            .map_err(|_| "bad coordinate".to_string())
    };
    let (sx, sy) = (num(0)?, num(1)?);
    if !over_client(Some((ox, oy, w, h)), sx as f64, sy as f64) {

        if action == "move" {
            return Ok("ok: move (outside window, ignored)\n".into());
        }
        return Err(format!("point {sx},{sy} is not over the foreground window ({ox},{oy} {w}x{h})"));
    }
    let (oxi, oyi) = (ox as i64, oy as i64);
    let (lx, ly) = (sx - oxi, sy - oyi);

    let local: Vec<String> = match action {
        "move" => vec![lx.to_string(), ly.to_string()],
        "click" => {
            let mut v = vec![lx.to_string(), ly.to_string()];
            if let Some(btn) = args.get(2) {
                v.push((*btn).to_string());
            }
            v
        }
        "scroll" => {
            let n = args.get(2).copied().unwrap_or("1");
            vec![lx.to_string(), ly.to_string(), n.to_string()]
        }
        "drag" => {
            let (sx2, sy2) = (num(2)?, num(3)?);
            let mut v = vec![lx.to_string(), ly.to_string(), (sx2 - oxi).to_string(), (sy2 - oyi).to_string()];
            if let Some(btn) = args.get(4) {
                v.push((*btn).to_string());
            }
            v
        }
        _ => return Err("bad pointer action".into()),
    };
    let refs: Vec<&str> = local.iter().map(|s| s.as_str()).collect();
    let r = act_pointer(shared, cid, action, &refs);

    wake_present();
    r
}
