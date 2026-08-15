use super::consts::WHEEL_STEP;
use super::signal::SharedShell;
use phantom::shell::ShellMode;

#[allow(dead_code)]
pub fn shell_command(shell: &SharedShell, cmd: &str) -> String {
    let mut it = cmd.split_whitespace();
    let verb = it.next().unwrap_or("");
    let mut s = match shell.lock() {
        Ok(g) => g,
        Err(p) => p.into_inner(),
    };
    match verb {
        "spin" => {
            let delta: f32 = it.next().and_then(|v| v.parse().ok()).unwrap_or(WHEEL_STEP);
            s.spin(delta);
            format!("ok spin rot={:.3} front={}", s.rotation, s.selected_app())
        }
        "select" => match it.next() {
            Some(name) if s.select_app(name) => format!("ok select front={}", s.selected_app()),
            Some(name) => format!("err no program '{name}'"),
            None => "err select needs a program".to_string(),
        },
        "mode" => match it.next() {
            Some("onsite") => { s.set_mode(ShellMode::Onsite); "ok mode onsite".to_string() }
            Some("auto") => { s.set_mode(ShellMode::Auto); "ok mode auto".to_string() }
            Some("remote") => { s.set_mode(ShellMode::Remote); "ok mode remote".to_string() }
            other => format!("err bad mode {other:?}"),
        },
        "launch" | "grant" | "revoke" => format!("err '{verb}' unimplemented"),
        other => format!("err unknown shell verb '{other}'"),
    }
}
