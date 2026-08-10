pub(crate) fn local_clock() -> Option<(String, String)> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs() as i64;

    let off = local_utc_offset_secs();
    let t = secs + off;
    let days = t.div_euclid(86_400);
    let tod = t.rem_euclid(86_400);
    let hh = tod / 3600;
    let mm = (tod % 3600) / 60;
    let (y, mo, d) = civil_from_days(days);
    let wd = weekday_from_days(days);
    const WD: [&str; 7] = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"];
    const MON: [&str; 12] = [
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    ];
    let _ = y;
    let date = format!("{} {:02} {}", WD[wd as usize], d, MON[(mo - 1) as usize]);
    let time = format!("{:02}:{:02}", hh, mm);
    Some((date, time))
}

pub fn hostname() -> String {
    std::fs::read_to_string("/etc/hostname")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOSTNAME").ok().filter(|s| !s.is_empty()))
        .unwrap_or_else(|| "localhost".to_string())
}

pub fn default_room_title() -> String {
    format!("ROOM \u{00b7} {} \u{00b7} claude", hostname())
}

fn local_utc_offset_secs() -> i64 {
    if let Ok(v) = std::env::var("PHANTOM_TZ_OFFSET") {
        if let Ok(n) = v.trim().parse::<i64>() {
            return n.clamp(-50_400, 50_400);
        }
    }
    0
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn weekday_from_days(z: i64) -> i64 {

    (z.rem_euclid(7) + 3) % 7
}
