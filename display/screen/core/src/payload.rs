//! The JSON led-notify pushes on `/api/screen/stream` (see `screen_data()` in led_notify/web/api.py).
//! Missing or null fields default, null list entries are dropped and unknown fields are ignored: the sampler
//! sends `null` for any part that failed (led_notify/stats/sampler.py), and the rest must stay on screen.
use serde::{Deserialize, Deserializer};

fn or_default<'de, D: Deserializer<'de>, T: Deserialize<'de> + Default>(d: D) -> Result<T, D::Error> {
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

fn skip_nulls<'de, D: Deserializer<'de>, T: Deserialize<'de>>(d: D) -> Result<Vec<T>, D::Error> {
    Ok(Option::<Vec<Option<T>>>::deserialize(d)?.unwrap_or_default().into_iter().flatten().collect())
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct ScreenData {
    #[serde(deserialize_with = "or_default")]
    pub screen: Screen,
    pub stats: Option<Stats>,
    #[serde(deserialize_with = "skip_nulls")]
    pub banner: Vec<Banner>,
    #[serde(deserialize_with = "or_default")]
    pub host: Host,
    #[serde(deserialize_with = "or_default")]
    pub now: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Screen {
    #[serde(deserialize_with = "or_default")]
    pub power: String,
    #[serde(deserialize_with = "or_default")]
    pub theme: String,
    pub brightness: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Stats {
    pub cpu_pct: Option<f64>,
    pub memory: Option<Memory>,
    pub temps: Option<Temps>,
    #[serde(deserialize_with = "skip_nulls")]
    pub disks: Vec<Disk>,
    #[serde(deserialize_with = "skip_nulls")]
    pub apps: Vec<App>,
    pub uptime_s: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Memory {
    #[serde(deserialize_with = "or_default")]
    pub used_bytes: u64,
    #[serde(deserialize_with = "or_default")]
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
    #[serde(deserialize_with = "or_default")]
    pub label: String,
    #[serde(deserialize_with = "or_default")]
    pub mounted: bool,
    pub used_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    pub pct: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct App {
    #[serde(deserialize_with = "or_default")]
    pub name: String,
    #[serde(deserialize_with = "or_default")]
    pub cpu_pct: f64,
    #[serde(deserialize_with = "or_default")]
    pub mem_bytes: u64,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Banner {
    #[serde(deserialize_with = "or_default")]
    pub text: String,
    #[serde(deserialize_with = "or_default")]
    pub since: String,
    #[serde(deserialize_with = "or_default")]
    pub priority: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Host {
    #[serde(deserialize_with = "or_default")]
    pub name: String,
}
