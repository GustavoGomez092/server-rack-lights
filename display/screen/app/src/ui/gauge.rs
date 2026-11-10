//! The three ring gauges (CPU, RAM, CPU temp), drawn like the old SVG: r = 40 %, stroke 9 %, from 12 o'clock.
use std::f32::consts::{PI, TAU};

use gpui::{canvas, div, point, prelude::*, px, Bounds, Div, PathBuilder, PathStyle, Pixels, Window};
use lyon::path::LineCap;
use lyon::tessellation::StrokeOptions;
use rack_screen_core::theme::{Palette, Rgb};
use rack_screen_core::view::{GaugeVm, Level, ViewModel};

use super::{baseline_drop, color, Vu};

fn ring(bounds: Bounds<Pixels>, fill_pct: f32, track: Rgb, arc: Rgb, window: &mut Window) {
    let size = bounds.size.width.min(bounds.size.height);
    let stroke = size * 0.09;
    let r = size * 0.40;
    let c = bounds.center();
    let top = point(c.x, c.y - r);
    let mut t = PathBuilder::stroke(stroke);
    t.move_to(top);
    t.arc_to(point(r, r), px(0.), false, true, point(c.x, c.y + r));
    t.arc_to(point(r, r), px(0.), false, true, top);
    if let Ok(path) = t.build() {
        window.paint_path(path, color(track));
    }
    if fill_pct < 0.5 {
        return;
    }
    let a = fill_pct.min(99.9) / 100.0 * TAU;
    let opts = StrokeOptions::default().with_line_width(f32::from(stroke)).with_line_cap(LineCap::Round);
    let mut b = PathBuilder::stroke(stroke).with_style(PathStyle::Stroke(opts));
    b.move_to(top);
    b.arc_to(point(r, r), px(0.), a > PI, true, point(c.x + r * a.sin(), c.y - r * a.cos()));
    if let Ok(path) = b.build() {
        window.paint_path(path, color(arc));
    }
}

fn gauge(g: &GaugeVm, fill: f32, base: Rgb, caption: &'static str, p: &Palette, u: Vu, max_ring: Pixels) -> Div {
    let arc = match g.level {
        Level::Ok => base,
        Level::Warn => p.warn,
        Level::Bad => p.bad,
    };
    let track = p.line;
    let (num, unit) = (u.w(4.6), u.w(1.8));
    let value = div()
        .absolute()
        .inset_0()
        .flex()
        .justify_center()
        .pt(max_ring * 0.32) // as the old page: 32 % of the ring's width, so the number sits a bit low
        .child(
            div()
                .flex()
                .items_start()
                .child(div().text_size(num).font_weight(gpui::FontWeight::BOLD).child(g.text.clone()))
                .child(
                    div()
                        .mt(baseline_drop(num, unit))
                        .ml(u.w(0.2))
                        .text_size(unit)
                        .text_color(color(p.muted))
                        .child(g.unit),
                ),
        );
    let mut cap = div()
        .flex_none()
        .flex()
        .justify_center()
        .mt(u.w(0.8))
        .text_size(u.w(1.6))
        .text_color(color(p.muted))
        .child(caption);
    if !g.detail.is_empty() {
        cap = cap.child(div().text_color(color(p.text)).child(format!("\u{a0}{}", g.detail)));
    }
    // The ring may shrink below its full size when banners take the height, so nothing ever overlaps.
    div()
        .h_full()
        .flex()
        .flex_col()
        .justify_center()
        .items_center()
        .child(
            div()
                .relative()
                .w(max_ring)
                .h(max_ring)
                .min_h_0()
                .flex_shrink()
                .child(canvas(|_, _, _| {}, move |b, _, w, _| ring(b, fill, track, arc, w)).size_full())
                .child(value),
        )
        .child(cap)
}

/// Three equal columns, 1.4vw apart, each ring as wide as its column but at most 38vh.
pub fn row(vm: &ViewModel, fills: [f32; 3], p: &Palette, u: Vu, width: Pixels) -> Div {
    let col = (width - u.w(1.4) * 2.0) / 3.0;
    let ring_px = col.min(u.h(38.0));
    let cell = |g| div().w(col).h_full().child(g);
    div()
        .flex_1()
        .min_h_0()
        .flex()
        .gap(u.w(1.4))
        .child(cell(gauge(&vm.cpu, fills[0], p.cpu, "CPU", p, u, ring_px)))
        .child(cell(gauge(&vm.ram, fills[1], p.ram, "RAM", p, u, ring_px)))
        .child(cell(gauge(&vm.temp, fills[2], p.temp, "CPU temp", p, u, ring_px)))
}
