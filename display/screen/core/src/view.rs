//! What the screen shows, already formatted: the drawing code only lays these out.
use crate::format::{app_cpu, clock, gb, level, uptime};
use crate::payload::ScreenData;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Ok,
    Warn,
    Bad,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Theme {
    Light,
    Dark,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GaugeVm {
    /// How much of the ring is filled, 0–100.
    pub fill_pct: f32,
    pub text: String,
    pub unit: &'static str,
    pub level: Level,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DiskVm {
    pub label: String,
    pub used_text: String,
    pub fill_pct: f32,
    pub level: Level,
    pub mounted: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppVm {
    pub name: String,
    pub cpu_text: String,
    pub mem_text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BannerVm {
    pub text: String,
    pub time: String,
    pub bad: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ViewModel {
    /// Quiet hours: draw pure black and nothing else (the output stays on; see display/run.sh).
    pub blank: bool,
    pub theme: Theme,
    /// Brightness as a factor 0.1–1: every color is mixed toward black by it.
    pub keep: f32,
    pub host: String,
    pub meta: String,
    pub banners: Vec<BannerVm>,
    pub cpu: GaugeVm,
    pub ram: GaugeVm,
    pub temp: GaugeVm,
    pub disks: Vec<DiskVm>,
    /// Empty means "No app data yet".
    pub apps: Vec<AppVm>,
}

fn gauge(fill: Option<f64>, value: Option<f64>, unit: &'static str, level: Level, detail: String) -> GaugeVm {
    GaugeVm {
        fill_pct: fill.unwrap_or(0.0).clamp(0.0, 100.0) as f32,
        text: value.map_or("–".into(), |v| format!("{}", v.round())),
        unit,
        level,
        detail,
    }
}

impl ViewModel {
    /// Before the first push: the layout with dashes.
    pub fn empty() -> Self {
        view_model(&ScreenData::default())
    }
}

pub fn view_model(d: &ScreenData) -> ViewModel {
    let stats = d.stats.clone().unwrap_or_default();
    let cpu = stats.cpu_pct;
    let ram_pct = stats.memory.as_ref().filter(|m| m.total_bytes > 0)
        .map(|m| 100.0 * m.used_bytes as f64 / m.total_bytes as f64);
    let ram_detail = stats.memory.as_ref().filter(|m| m.total_bytes > 0)
        .map_or(String::new(), |m| format!("{} / {} GB", gb(m.used_bytes), gb(m.total_bytes)));
    let temps = stats.temps.clone().unwrap_or_default();
    let temp_detail = temps.drive_c.map_or(String::new(), |c| format!("· drive {}°", c.round()));
    let meta = [stats.uptime_s.filter(|_| d.stats.is_some()).map(uptime), Some(clock(&d.now))]
        .into_iter()
        .flatten()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" · ");

    ViewModel {
        blank: d.screen.power == "off",
        theme: if d.screen.theme == "light" { Theme::Light } else { Theme::Dark },
        keep: (d.screen.brightness.unwrap_or(100.0) / 100.0) as f32,
        host: if d.host.name.is_empty() { "Command Center".into() } else { d.host.name.clone() },
        meta,
        banners: d.banner.iter().map(|b| BannerVm {
            text: b.text.clone(),
            time: clock(&b.since),
            bad: matches!(b.priority.as_str(), "critical" | "high"),
        }).collect(),
        cpu: gauge(cpu, cpu, "%", level(cpu, 80.0, 95.0), String::new()),
        ram: gauge(ram_pct, ram_pct, "%", level(ram_pct, 85.0, 95.0), ram_detail),
        // The ring spans 30–100 °C, so a resting CPU sits low and a hot one fills it.
        temp: gauge(temps.cpu_c.map(|c| (c - 30.0) / 70.0 * 100.0), temps.cpu_c, "°C",
                    level(temps.cpu_c, 75.0, 85.0), temp_detail),
        disks: stats.disks.iter().map(|k| {
            let sizes = k.used_bytes.zip(k.total_bytes).filter(|_| k.mounted);
            DiskVm {
                label: k.label.clone(),
                used_text: sizes.map_or("not connected".into(), |(u, t)| format!("{} / {} GB", gb(u), gb(t))),
                fill_pct: sizes.and(k.pct).unwrap_or(0.0).clamp(0.0, 100.0) as f32,
                level: if sizes.is_some() { level(k.pct, 85.0, 95.0) } else { Level::Ok },
                mounted: sizes.is_some(),
            }
        }).collect(),
        apps: stats.apps.iter().map(|a| AppVm {
            name: a.name.clone(),
            cpu_text: app_cpu(a.cpu_pct),
            mem_text: format!("{} GB", gb(a.mem_bytes)),
        }).collect(),
    }
}
