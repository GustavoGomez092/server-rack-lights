//! The old page's CSS transitions: 0.3 s ease-out toward each new value.
pub const DURATION_MS: f64 = 300.0;

/// CSS `ease-out` = cubic-bezier(0, 0, 0.58, 1), solved for x by bisection.
pub fn ease_out(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0) as f64;
    let bez = |u: f64, p1: f64, p2: f64| 3.0 * (1.0 - u).powi(2) * u * p1 + 3.0 * (1.0 - u) * u * u * p2 + u.powi(3);
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..30 {
        let mid = (lo + hi) / 2.0;
        if bez(mid, 0.0, 0.58) < x { lo = mid } else { hi = mid }
    }
    bez((lo + hi) / 2.0, 0.0, 1.0) as f32
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Tween {
    pub from: f32,
    pub to: f32,
    pub start_ms: f64,
}

impl Tween {
    pub fn new(from: f32, to: f32, start_ms: f64) -> Self {
        Tween { from, to, start_ms }
    }

    /// Already at `v`, nothing to animate (first frame, no fade).
    pub fn settled(v: f32) -> Self {
        Tween { from: v, to: v, start_ms: f64::NEG_INFINITY }
    }

    pub fn value(&self, now_ms: f64) -> f32 {
        let t = ((now_ms - self.start_ms) / DURATION_MS).clamp(0.0, 1.0) as f32;
        if t >= 1.0 { self.to } else { self.from + (self.to - self.from) * ease_out(t) }
    }

    pub fn done(&self, now_ms: f64) -> bool {
        self.from == self.to || now_ms - self.start_ms >= DURATION_MS
    }

    /// Head for `to` from wherever the value is now, so a new push mid-glide never jumps.
    pub fn retarget(&mut self, to: f32, now_ms: f64) {
        if to == self.to {
            return;
        }
        *self = Tween { from: self.value(now_ms), to, start_ms: now_ms };
    }
}
