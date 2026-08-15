use super::state::PROGRAM_COUNT;

pub(crate) fn ring_center(w: f64, h: f64) -> (f64, f64, f64) {
    (w * 0.5, h * 0.46, 150.0)
}

pub(crate) fn icon_geom(i: usize, cx: f64, cy: f64, er: f64, rot: f64) -> (f64, f64, f64, f64) {
    let n = PROGRAM_COUNT;
    let orbit = er + 88.0;
    let front = std::f64::consts::FRAC_PI_2;
    let ang = rot + (i as f64) * std::f64::consts::TAU / n as f64;
    let ax0 = cx + orbit * ang.cos();
    let ay0 = cy + orbit * ang.sin();
    let mut da = (ang - front).rem_euclid(std::f64::consts::TAU);
    if da > std::f64::consts::PI {
        da = std::f64::consts::TAU - da;
    }
    let frontness = 1.0 - da / std::f64::consts::PI;
    let size = 46.0 + 64.0 * frontness;
    let pull = (1.0 - frontness) * 26.0;
    let ax = ax0 - (ax0 - cx) * (pull / orbit);
    let ay = ay0 - (ay0 - cy) * (pull / orbit);
    (ax, ay, size, frontness)
}
