//! No push for a while (led-notify restarting): keep the last numbers but show they are old.
pub const STALE_MS: f64 = 10_000.0;

#[derive(Debug, Clone, Copy)]
pub struct Staleness {
    last_ms: f64,
}

impl Staleness {
    pub fn new(start_ms: f64) -> Self {
        Staleness { last_ms: start_ms }
    }

    pub fn touch(&mut self, now_ms: f64) {
        self.last_ms = now_ms;
    }

    pub fn is_stale(&self, now_ms: f64) -> bool {
        now_ms - self.last_ms > STALE_MS
    }
}
