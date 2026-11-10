//! The screen view: the old page's layout ("Control room"), sized from the window like its vw/vh CSS.
mod gauge;
mod panels;

use std::time::Instant;

use gpui::{div, prelude::*, px, rgb, Context, FontWeight, Hsla, Pixels, Rgba, SharedString, Window};
use rack_screen_core::payload::ScreenData;
use rack_screen_core::stale::Staleness;
use rack_screen_core::theme::{palette, Rgb};
use rack_screen_core::view::{view_model, ViewModel};

/// Values and themes change at once: the old page's 0.3 s glides cost GPUI ~2.5 % of a core at 60 fps
/// (owner chose no motion, 2026-11-09).
pub struct Screen {
    vm: ViewModel,
    start: Instant,
    stale: Staleness,
    shown_stale: bool,
    fixture: bool,
}

/// One CSS viewport unit of the current window, in pixels.
#[derive(Clone, Copy)]
pub struct Vu {
    pub vw: f32,
    pub vh: f32,
}

impl Vu {
    pub fn w(self, n: f32) -> Pixels {
        px(self.vw * n)
    }
    pub fn h(self, n: f32) -> Pixels {
        px(self.vh * n)
    }
}

pub fn color(c: Rgb) -> Rgba {
    rgb(c.to_u32())
}

pub fn faded(c: Rgb, opacity: f32) -> Hsla {
    let mut h: Hsla = color(c).into();
    h.a = opacity;
    h
}

/// Inter with tabular figures, so numbers don't jiggle as they change.
pub fn inter() -> gpui::Font {
    let mut f = gpui::font("Inter");
    f.features = gpui::FontFeatures(std::sync::Arc::new(vec![("tnum".into(), 1)]));
    f
}

/// The old CSS asked for 650; with static Inter faces the browser drew it as Bold (next heavier face).
pub const SEMIBOLD_650: FontWeight = FontWeight::BOLD;

/// Inter's ascent and ascent + descent, in em. CSS `line-height: normal` for Inter is the latter.
const INTER_ASCENT: f32 = 0.96875;
pub const INTER_LINE: f32 = 1.2109;

/// GPUI can't align flex items on their text baselines, so a smaller text sharing a row with a bigger one is
/// top-aligned and pushed down by this much (both use Inter's normal line height).
pub fn baseline_drop(big: Pixels, small: Pixels) -> Pixels {
    (big - small) * INTER_ASCENT
}

/// The Inter faces the page used, built in so the screen never depends on installed fonts.
pub fn load_fonts(cx: &mut gpui::App) {
    let fonts = [
        &include_bytes!("../../fonts/Inter-Regular.otf")[..],
        include_bytes!("../../fonts/Inter-Italic.otf"),
        include_bytes!("../../fonts/Inter-Medium.otf"),
        include_bytes!("../../fonts/Inter-SemiBold.otf"),
        include_bytes!("../../fonts/Inter-Bold.otf"),
    ];
    cx.text_system()
        .add_fonts(fonts.into_iter().map(std::borrow::Cow::Borrowed).collect())
        .expect("load the built-in Inter fonts");
}

/// The old page's uppercase headings had .14em letter spacing; GPUI has none, so hair spaces stand in.
pub fn spaced(s: &str) -> SharedString {
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 {
            out.push('\u{200A}');
        }
        out.push(c);
    }
    out.into()
}

impl Screen {
    pub fn new() -> Self {
        Screen {
            vm: ViewModel::empty(),
            start: Instant::now(),
            stale: Staleness::new(0.0),
            shown_stale: false,
            fixture: false,
        }
    }

    fn now(&self) -> f64 {
        self.start.elapsed().as_secs_f64() * 1000.0
    }

    /// A push from led-notify.
    pub fn apply(&mut self, data: &ScreenData) {
        self.stale.touch(self.now());
        self.shown_stale = false;
        self.vm = view_model(data);
    }

    pub fn show_fixture(&mut self, data: &ScreenData) {
        self.fixture = true;
        self.apply(data);
    }

    /// True when the stale look changed and the screen needs a redraw.
    pub fn check_stale(&mut self) -> bool {
        let stale = !self.fixture && self.stale.is_stale(self.now());
        std::mem::replace(&mut self.shown_stale, stale) != stale
    }
}

impl Render for Screen {
    fn render(&mut self, window: &mut Window, _cx: &mut Context<Self>) -> impl IntoElement {
        // Quiet hours: pure black, nothing else. The output stays on (the monitor won't wake from a lost signal).
        if self.vm.blank {
            return div().size_full().bg(rgb(0x000000));
        }
        let vp = window.viewport_size();
        let u = Vu { vw: f32::from(vp.width) / 100.0, vh: f32::from(vp.height) / 100.0 };
        let p = palette(self.vm.theme).dim(self.vm.keep);
        let stale = self.shown_stale;
        let dim = if stale { 0.45 } else { 1.0 };

        // Grid "1.3fr 1fr" with a 1.6vw gap inside 2.6vw side padding.
        let inner = f32::from(vp.width) - 2.0 * u.vw * 2.6 - u.vw * 1.6;
        let (left_w, right_w) = (px(inner * 1.3 / 2.3), px(inner / 2.3));

        let mut meta = div()
            .flex()
            .mt(baseline_drop(u.w(2.4), u.w(1.7)))
            .text_size(u.w(1.7))
            .text_color(color(p.muted))
            .child(self.vm.meta.clone());
        if stale {
            meta = meta.child(div().text_color(color(p.warn)).child("\u{a0}· reconnecting"));
        }
        let header = div()
            .flex()
            .justify_between()
            .items_start()
            .child(div().text_size(u.w(2.4)).font_weight(SEMIBOLD_650).child(self.vm.host.clone()))
            .child(meta);

        let gauges = gauge::row(&self.vm, &p, u, left_w).opacity(dim);
        let left = div()
            .w(left_w)
            .flex()
            .flex_col()
            .gap(u.w(1.6))
            .child(gauges)
            .child(panels::disks(&self.vm.disks, &p, u).opacity(dim));

        div()
            .size_full()
            .bg(color(p.bg))
            .text_color(color(p.text))
            .font(inter())
            .line_height(gpui::relative(INTER_LINE))
            .pt(u.w(2.4))
            .pb(u.w(2.4))
            .px(u.w(2.6))
            .flex()
            .flex_col()
            .gap(u.w(1.6))
            .child(header)
            .when(!self.vm.banners.is_empty(), |d| d.child(panels::banners(&self.vm.banners, &p, u)))
            .child(
                div()
                    .flex_1()
                    .min_h_0()
                    .flex()
                    .gap(u.w(1.6))
                    .child(left)
                    .child(panels::apps(&self.vm.apps, &p, u).w(right_w).opacity(dim)),
            )
    }
}
