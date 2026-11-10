//! Number and time formatting, identical to the old page's screen.js.
use crate::view::Level;

const GIB: f64 = 1024.0 * 1024.0 * 1024.0;

/// GiB with one decimal below 100, whole from 100 up.
pub fn gb(bytes: u64) -> String {
    let v = bytes as f64 / GIB;
    if v >= 100.0 { format!("{v:.0}") } else { format!("{v:.1}") }
}

pub fn uptime(s: f64) -> String {
    let d = (s / 86400.0).floor();
    let h = ((s % 86400.0) / 3600.0).floor();
    if d >= 1.0 {
        format!("up {d} day{}", if d == 1.0 { "" } else { "s" })
    } else if h >= 1.0 {
        format!("up {h} h")
    } else {
        format!("up {} min", (s / 60.0).floor().max(1.0))
    }
}

/// Server-local wall time read straight from the ISO string, so the program's own timezone never matters.
pub fn clock(iso: &str) -> String {
    let Some(t) = iso.find('T') else { return String::new() };
    let b = iso.as_bytes();
    let digits = |i: usize| b.get(i).filter(|c| c.is_ascii_digit()).map(|c| (c - b'0') as u32);
    let (Some(h1), Some(h2), Some(b':'), Some(m1), Some(m2)) =
        (digits(t + 1), digits(t + 2), b.get(t + 3).copied(), digits(t + 4), digits(t + 5))
    else {
        return String::new();
    };
    let h = h1 * 10 + h2;
    format!("{}:{m1}{m2} {}", if h % 12 == 0 { 12 } else { h % 12 }, if h < 12 { "AM" } else { "PM" })
}

pub fn level(v: Option<f64>, warn: f64, bad: f64) -> Level {
    match v {
        Some(v) if v >= bad => Level::Bad,
        Some(v) if v >= warn => Level::Warn,
        _ => Level::Ok,
    }
}

/// One decimal under 10 %, whole above.
pub fn app_cpu(pct: f64) -> String {
    if pct < 10.0 { format!("{pct:.1}%") } else { format!("{}%", pct.round()) }
}
