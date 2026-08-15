use super::state::set_focused_auto;
use crate::Shared;

pub(crate) fn session_user(shared: &Shared) -> (String, u32) {
    use std::os::unix::fs::MetadataExt;
    let uid = {
        let g = shared.lock().unwrap();
        g.values()
            .filter_map(|st| st.pid)
            .filter_map(|p| std::fs::metadata(format!("/proc/{p}")).ok())
            .map(|m| m.uid())
            .find(|&u| u != 0)
    };
    let uid = uid
        .or_else(|| std::env::var("PHANTOM_UID").ok().and_then(|s| s.parse().ok()))
        .unwrap_or(1000);
    (crate::uid_to_name(uid).unwrap_or_else(|| "user".into()), uid)
}

pub fn launch_program(shared: &Shared, label: &str) {
    let cmd = match label {
        "TERMINAL" => "foot",
        "FILES" => "nautilus --new-window",

        "BROWSER" => {
            "sh -c 'for b in epiphany-browser epiphany gnome-www-browser chromium chromium-browser; do command -v \"$b\" >/dev/null 2>&1 && exec \"$b\"; done; exec firefox'"
        }
        "CODE" | "CLAUDE" => {
            "/usr/share/code/code --ozone-platform=wayland --no-sandbox --disable-gpu --password-store=basic --force-renderer-accessibility"
        }
        "SETTINGS" => "gnome-control-center",
        "CALENDAR" => "gnome-calendar",
        _ => return,
    };
    spawn_in_seat(shared, label, cmd);
}

pub fn spawn_command(shared: &Shared, cmd: &str) {
    let cmd = cmd.trim();
    if cmd.is_empty() {
        return;
    }
    spawn_in_seat(shared, "spawn", cmd);
}

fn spawn_in_seat(shared: &Shared, label: &str, cmd: &str) {
    let (user, uid) = session_user(shared);

    let sock = super::state::seat_socket();
    let env = format!(
        "WAYLAND_DISPLAY={sock} XDG_RUNTIME_DIR=/run/user/{uid} \
         DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
         GDK_BACKEND=wayland MOZ_ENABLE_WAYLAND=1 MOZ_DISABLE_WAYLAND_DMABUF=1 QT_QPA_PLATFORM=wayland \
         XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland \
         LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe GSK_RENDERER=cairo GDK_GL=disable \
         ACCESSIBILITY_ENABLED=1 GTK_MODULES=gail:atk-bridge QT_ACCESSIBILITY=1"
    );
    let full = format!("{env} {cmd} >/dev/null 2>&1 &");
    use std::os::unix::fs::MetadataExt;
    let my_uid = std::fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(0);
    let mut c = if my_uid == 0 {
        let mut cc = std::process::Command::new("su");
        cc.arg(&user).arg("-c").arg(full);
        cc
    } else {
        let mut cc = std::process::Command::new("sh");
        cc.arg("-c").arg(full);
        cc
    };

    set_focused_auto();
    match c.spawn() {
        Ok(_) => eprintln!("[shell] {label} as {user}: {cmd}"),
        Err(e) => eprintln!("[shell] {label} failed: {e}"),
    }
}

pub(crate) fn warm_session(shared: &Shared) {
    let (user, uid) = session_user(shared);

    let sock = super::state::seat_socket();
    let inner = format!("dbus-update-activation-environment --systemd \
                 WAYLAND_DISPLAY={sock} XDG_CURRENT_DESKTOP=GNOME \
                 XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland >/dev/null 2>&1 || true; \
                 gsettings set org.gnome.desktop.interface toolkit-accessibility true \
                 >/dev/null 2>&1 || true");
    let full = format!(
        "XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus {inner}"
    );
    use std::os::unix::fs::MetadataExt;
    let my_uid = std::fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(0);
    let mut c = if my_uid == 0 {
        let mut cc = std::process::Command::new("su");
        cc.arg(&user).arg("-c").arg(full);
        cc
    } else {
        let mut cc = std::process::Command::new("sh");
        cc.arg("-c").arg(full);
        cc
    };
    match c.spawn() {
        Ok(mut ch) => {
            let _ = ch.wait();
            eprintln!("[shell] warmed session activation env for {user}");
        }
        Err(e) => eprintln!("[shell] warm_session failed: {e}"),
    }
}
