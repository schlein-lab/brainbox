use super::font::{
    text, text_styled, text_tracked, text_width_styled, FontStyle, STYLE_METRICS,
};
use super::palette::{ACCENT, INK, INK_FAINT, INK_SOFT, OK_GREEN, PAPER, PAPER_ALT, PAPER_HI, WARN_AMBER};
use super::primitives::stroke_rrect;
use super::raster::{Canvas, Rgb};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum RoomSpeaker {
    User,
    Agent(u8),

    System,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum RoomKind {
    Text,
    Thinking,
    Tool,
    Result,
}

pub struct RoomItem {
    pub speaker: RoomSpeaker,
    pub kind: RoomKind,
    pub text: String,
}

fn speaker_color(s: RoomSpeaker) -> Rgb {
    match s {
        RoomSpeaker::User => ACCENT,
        RoomSpeaker::Agent(0) => INK,
        RoomSpeaker::Agent(1) => OK_GREEN,
        RoomSpeaker::Agent(_) => WARN_AMBER,
        RoomSpeaker::System => INK_SOFT,
    }
}

fn speaker_label(s: RoomSpeaker) -> String {
    match s {
        RoomSpeaker::User => "DU".to_string(),
        RoomSpeaker::Agent(0) => "CLAUDE".to_string(),
        RoomSpeaker::Agent(n) => format!("CLAUDE {}", n as u32 + 1),
        RoomSpeaker::System => "·".to_string(),
    }
}

fn wrap(s: &str, style: FontStyle, max_w: i32) -> Vec<String> {
    let mut out = Vec::new();
    if max_w <= 0 {
        out.push(s.to_string());
        return out;
    }
    let mut cur = String::new();
    for word in s.split(' ') {
        let trial = if cur.is_empty() { word.to_string() } else { format!("{cur} {word}") };
        if text_width_styled(&trial, style) <= max_w {
            cur = trial;
            continue;
        }
        if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
        if text_width_styled(word, style) > max_w {
            let mut chunk = String::new();
            for ch in word.chars() {
                let t = format!("{chunk}{ch}");
                if !chunk.is_empty() && text_width_styled(&t, style) > max_w {
                    out.push(std::mem::take(&mut chunk));
                    chunk.push(ch);
                } else {
                    chunk = t;
                }
            }
            cur = chunk;
        } else {
            cur = word.to_string();
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

fn sanitize_line(s: &str) -> String {
    s.replace('\t', "    ").replace('\r', "")
}

pub fn render_room(buf: &mut [u8], w: u32, h: u32, items: &[RoomItem], title: &str, input: &str) {
    if w == 0 || h == 0 {
        return;
    }
    let mut c = Canvas::wrap(buf, w as usize, h as usize);
    let (wf, hf) = (w as f64, h as f64);

    for y in 0..h as usize {
        for x in 0..w as usize {
            c.put(x, y, PAPER);
        }
    }
    stroke_rrect(&mut c, 1.0, 1.0, wf - 2.0, hf - 2.0, 9.0, 2.0, INK);

    let hdr_h: i32 = 26;
    for y in 1..hdr_h {
        for x in 1..(w as i32 - 1) {
            c.blend_i(x, y, PAPER_HI, 1.0);
        }
    }
    text_tracked(&mut c, 12, 5, title, FontStyle::Heading, INK, 1);
    for x in 1..(w as i32 - 1) {
        c.blend_i(x, hdr_h, ACCENT, 0.7);
    }

    let in_h: i32 = 26;
    let in_top = h as i32 - in_h;
    for y in (in_top + 1)..(h as i32 - 1) {
        for x in 1..(w as i32 - 1) {
            c.blend_i(x, y, PAPER_ALT, 1.0);
        }
    }
    for x in 1..(w as i32 - 1) {
        c.blend_i(x, in_top, INK, 0.4);
    }
    text(&mut c, 12, in_top + 5, "\u{203a}", 2, ACCENT);
    let (cue, cue_col) = if input.is_empty() {
        ("Nachricht an den Room \u{2026}", INK_FAINT)
    } else {
        (input, INK)
    };
    text(&mut c, 30, in_top + 5, cue, 2, cue_col);

    let pad_x: i32 = 14;
    let body_top = hdr_h + 6;
    let body_bot = in_top - 6;
    let max_w = w as i32 - pad_x * 2;
    if max_w <= 0 || body_bot <= body_top {
        return;
    }

    struct RL {
        col: Rgb,
        style: FontStyle,
        s: String,
        bar: Option<Rgb>,
        gap_above: bool,
    }
    let mut rls: Vec<RL> = Vec::new();
    let mut prev: Option<RoomSpeaker> = None;
    for it in items {
        let sc = speaker_color(it.speaker);
        if prev != Some(it.speaker) {
            rls.push(RL { col: sc, style: FontStyle::Heading, s: speaker_label(it.speaker), bar: None, gap_above: prev.is_some() });
            prev = Some(it.speaker);
        }
        let (body_col, style, prefix) = match it.kind {
            RoomKind::Text => (INK, FontStyle::Body, ""),
            RoomKind::Thinking => (INK_FAINT, FontStyle::Caption, "\u{00bb} "),
            RoomKind::Tool => (ACCENT, FontStyle::Caption, "\u{25b6} "),
            RoomKind::Result => (INK_SOFT, FontStyle::Caption, ""),
        };
        for (i, raw) in it.text.split('\n').enumerate() {
            let line = sanitize_line(raw);
            let pfx = if i == 0 { prefix } else { "" };
            for wl in wrap(&format!("{pfx}{line}"), style, max_w - 8) {
                rls.push(RL { col: body_col, style, s: wl, bar: Some(sc), gap_above: false });
            }
        }
    }

    let mut y = body_bot;
    for rl in rls.iter().rev() {
        let lh = STYLE_METRICS[rl.style as usize].line_height + if rl.gap_above { 6 } else { 2 };
        y -= lh;
        if y < body_top {
            break;
        }
        if let Some(bar) = rl.bar {
            for by in y..(y + STYLE_METRICS[rl.style as usize].line_height) {
                c.blend_i(pad_x - 8, by, bar, 0.85);
                c.blend_i(pad_x - 7, by, bar, 0.85);
            }
        }
        text_styled(&mut c, pad_x, y, &rl.s, rl.style, rl.col);
    }
}
