//! The JSON led-notify pushes on `/api/screen/stream` (see `screen_data()` in led_notify/web/api.py).
//! Missing fields default and unknown ones are ignored, so a newer led-notify never breaks the screen.
use serde::Deserialize;

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct ScreenData {
    pub screen: Screen,
    pub stats: Option<Stats>,
    pub banner: Vec<Banner>,
    pub host: Host,
    pub now: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Screen {
    pub power: String,
    pub theme: String,
    pub brightness: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Stats {
    pub cpu_pct: Option<f64>,
    pub memory: Option<Memory>,
    pub temps: Option<Temps>,
    pub disks: Vec<Disk>,
    pub apps: Vec<App>,
    pub uptime_s: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Memory {
    pub used_bytes: u64,
    pub total_bytes: u64,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Temps {
    pub cpu_c: Option<f64>,
    pub drive_c: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Disk {
    pub label: String,
    pub mounted: bool,
    pub used_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    pub pct: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct App {
    pub name: String,
    pub cpu_pct: f64,
    pub mem_bytes: u64,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Banner {
    pub text: String,
    pub since: String,
    pub priority: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Host {
    pub name: String,
}
