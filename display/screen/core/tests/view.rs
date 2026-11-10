use rack_screen_core::format::{app_cpu, clock, gb, level, uptime};
use rack_screen_core::payload::ScreenData;
use rack_screen_core::view::{view_model, Level, Theme, ViewModel};

const GIB: u64 = 1024 * 1024 * 1024;

fn load(name: &str) -> ScreenData {
    let path = format!("{}/../fixtures/{name}", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn parse(json: &str) -> ScreenData {
    serde_json::from_str(json).unwrap()
}

#[test]
fn reference_values() {
    let vm = view_model(&load("dark.json"));
    assert_eq!((vm.cpu.text.as_str(), vm.cpu.level), ("8", Level::Ok));
    assert_eq!((vm.ram.text.as_str(), vm.ram.detail.as_str()), ("57", "8.4 / 14.6 GB"));
    assert_eq!((vm.temp.text.as_str(), vm.temp.detail.as_str()), ("55", "· drive 48°"));
    assert!((vm.temp.fill_pct - 35.0).abs() < 0.01); // ring spans 30–100 °C
    assert_eq!(
        vm.disks.iter().map(|d| d.used_text.as_str()).collect::<Vec<_>>(),
        ["459 / 904 GB", "689 / 916 GB", "73.6 / 234 GB"]
    );
    assert_eq!(
        (vm.apps[0].name.as_str(), vm.apps[0].cpu_text.as_str(), vm.apps[0].mem_text.as_str()),
        ("Paperclip", "2.3%", "2.2 GB")
    );
    assert_eq!(vm.apps[2].mem_text, "0.1 GB");
    assert!(vm.meta.ends_with("5:12 PM") && vm.meta.starts_with("up "), "{}", vm.meta);
    assert_eq!((vm.theme, vm.keep, vm.blank), (Theme::Dark, 1.0, false));
    assert_eq!(vm.host, "GomezPalacios");
    assert_eq!(view_model(&load("light.json")).theme, Theme::Light);
    let b = view_model(&load("banner.json")).banners;
    assert_eq!((b[0].text.as_str(), b[0].time.as_str(), b[0].bad, b[1].bad), ("Internet down", "5:12 PM", true, false));
}

#[test]
fn formatting() {
    assert_eq!(
        [uptime(30.0), uptime(600.0), uptime(5.0 * 3600.0), uptime(86400.0), uptime(2.0 * 86400.0)],
        ["up 1 min", "up 10 min", "up 5 h", "up 1 day", "up 2 days"]
    );
    assert_eq!(
        [clock("2026-11-09T00:05:00-08:00"), clock("2026-11-09T12:00:00-08:00"), clock("bad")],
        ["12:05 AM", "12:00 PM", ""]
    );
    assert_eq!([gb(100 * GIB), gb(GIB * 3 / 2)], ["100", "1.5"]);
    assert_eq!([app_cpu(2.34), app_cpu(12.5)], ["2.3%", "13%"]);
    assert_eq!(
        [level(Some(79.9), 80.0, 95.0), level(Some(80.0), 80.0, 95.0), level(Some(95.0), 80.0, 95.0), level(None, 80.0, 95.0)],
        [Level::Ok, Level::Warn, Level::Bad, Level::Ok]
    );
}

const MINIMAL: &str = r#"{"screen":{"power":"on","theme":"dark"},"host":{"name":""},"now":"2026-11-09T17:12:30-08:00"}"#;

#[test]
fn no_stats_yet() {
    let vm = view_model(&parse(MINIMAL));
    for g in [&vm.cpu, &vm.ram, &vm.temp] {
        assert_eq!((g.text.as_str(), g.fill_pct), ("–", 0.0));
    }
    assert!(vm.disks.is_empty() && vm.apps.is_empty() && vm.banners.is_empty());
    assert_eq!(vm.keep, 1.0); // brightness missing
    assert_eq!(vm.host, "Command Center");
    assert_eq!(vm.meta, "5:12 PM");
    let vm = view_model(&parse(&MINIMAL.replace(r#""host""#, r#""stats":null,"host""#)));
    assert_eq!(vm.cpu.text, "–");
}

#[test]
fn partial_stats() {
    let json = r#"{"screen":{"power":"on","theme":"light","brightness":40},
      "stats":{"cpu_pct":97.0,"temps":{"cpu_c":null,"drive_c":41.2},"apps":[],
        "disks":[{"label":"Backup","mounted":false,"used_bytes":null,"total_bytes":null,"pct":null}]},
      "host":{"name":"x"},"now":"2026-11-09T07:00:00-08:00","extra":1}"#;
    let vm = view_model(&parse(json));
    assert_eq!((vm.cpu.level, vm.keep), (Level::Bad, 0.4));
    assert_eq!((vm.temp.text.as_str(), vm.temp.detail.as_str(), vm.temp.fill_pct), ("–", "· drive 41°", 0.0));
    assert_eq!(vm.ram.text, "–");
    let d = &vm.disks[0];
    assert_eq!((d.used_text.as_str(), d.mounted, d.fill_pct), ("not connected", false, 0.0));
    assert!(vm.apps.is_empty());
}

#[test]
fn power_off_is_blank() {
    assert!(view_model(&parse(&MINIMAL.replace(r#""power":"on""#, r#""power":"off""#))).blank);
    assert!(ViewModel::empty().cpu.text == "–" && !ViewModel::empty().blank);
}

#[test]
fn nulls_led_notify_sends_on_purpose() {
    // The sampler sends `apps: null` when app sampling fails and a null disk when one read raises
    // (led_notify/stats/sampler.py); the rest of the screen must stay live.
    let json = r#"{"screen":{"power":"on","theme":null,"brightness":null},
      "stats":{"cpu_pct":12.0,"apps":null,"temps":null,"memory":null,"uptime_s":null,
        "disks":[null,{"label":"Main","mounted":true,"used_bytes":1073741824,"total_bytes":2147483648,"pct":50.0}]},
      "banner":null,"host":null,"now":null}"#;
    let vm = view_model(&parse(json));
    assert_eq!(vm.cpu.text, "12");
    assert!(vm.apps.is_empty() && vm.banners.is_empty());
    assert_eq!(vm.disks.iter().map(|d| d.label.as_str()).collect::<Vec<_>>(), ["Main"]);
    assert_eq!((vm.host.as_str(), vm.theme, vm.keep), ("Command Center", Theme::Dark, 1.0));
}
