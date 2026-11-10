//! The rack status screen: led-notify's `/api/screen/stream` drawn natively with GPUI (no browser).
//! RACK_SCREEN_FIXTURE=<payload.json> draws one fixed payload instead (for comparing with the reference shots);
//! RACK_SCREEN_WINDOW=800x480 opens a window of that size at the top-left (previewing off the rack).
mod feed;
mod ui;

use std::time::Duration;

use gpui::{point, prelude::*, px, size, App, Application, Bounds, WindowBackgroundAppearance, WindowBounds, WindowOptions};
use rack_screen_core::payload::ScreenData;

use crate::ui::Screen;

const DEFAULT_URL: &str = "http://127.0.0.1:8787/api/screen/stream";

fn preview_size() -> Option<(f32, f32)> {
    let v = std::env::var("RACK_SCREEN_WINDOW").ok()?;
    let (w, h) = v.split_once('x')?;
    Some((w.parse().ok()?, h.parse().ok()?))
}

fn main() {
    let fixture = std::env::var("RACK_SCREEN_FIXTURE").ok();
    let url = std::env::var("RACK_SCREEN_URL").unwrap_or_else(|_| DEFAULT_URL.into());
    Application::new().run(move |cx: &mut App| {
        ui::load_fonts(cx);
        let options = WindowOptions {
            window_background: WindowBackgroundAppearance::Opaque,
            app_id: Some("rack-screen".into()),
            window_bounds: preview_size().map(|(w, h)| {
                WindowBounds::Windowed(Bounds::new(point(px(0.), px(0.)), size(px(w), px(h))))
            }),
            ..Default::default()
        };
        cx.open_window(options, |_, cx| {
            cx.new(|cx| {
                let mut screen = Screen::new();
                if let Some(path) = fixture {
                    let data: ScreenData = std::fs::read_to_string(&path)
                        .map_err(|e| e.to_string())
                        .and_then(|s| serde_json::from_str(&s).map_err(|e| e.to_string()))
                        .unwrap_or_else(|e| panic!("fixture {path}: {e}"));
                    screen.show_fixture(&data);
                } else {
                    let rx = feed::spawn(url);
                    cx.spawn(async move |this, cx| {
                        while let Ok(data) = rx.recv().await {
                            if this.update(cx, |s: &mut Screen, cx| { s.apply(&data); cx.notify(); }).is_err() {
                                break;
                            }
                        }
                    })
                    .detach();
                }
                cx.spawn(async move |this, cx| loop {
                    cx.background_executor().timer(Duration::from_secs(2)).await;
                    let alive = this.update(cx, |s: &mut Screen, cx| if s.check_stale() { cx.notify() });
                    if alive.is_err() {
                        break;
                    }
                })
                .detach();
                screen
            })
        })
        .expect("open the screen window");
    });
}
