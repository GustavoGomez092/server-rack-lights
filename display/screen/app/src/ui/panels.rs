//! Alert banners, the Disks panel and the Top apps panel.
use gpui::{div, prelude::*, px, relative, Div, FontWeight};
use rack_screen_core::theme::Palette;
use rack_screen_core::view::{AppVm, BannerVm, DiskVm, Level};

use super::{baseline_drop, color, faded, spaced, Vu, SEMIBOLD_650};

pub fn banners(items: &[BannerVm], p: &Palette, u: Vu) -> Div {
    div().flex().flex_col().gap(u.w(0.6)).children(items.iter().map(|b| {
        let (fg, bg) = if b.bad { (p.bad, p.banner_bad_bg) } else { (p.warn, p.banner_warn_bg) };
        div()
            .flex()
            .justify_between()
            .items_start()
            .px(u.w(1.4))
            .py(u.w(1.0))
            .rounded(u.w(0.8))
            .bg(color(bg))
            .text_color(color(fg))
            .text_size(u.w(2.2))
            .font_weight(SEMIBOLD_650)
            .child(b.text.clone())
            .child(div().mt(baseline_drop(u.w(2.2), u.w(1.6))).text_size(u.w(1.6)).font_weight(FontWeight::MEDIUM).text_color(faded(fg, 0.85)).child(b.time.clone()))
    }))
}

fn panel(title: &str, p: &Palette, u: Vu) -> Div {
    div()
        .bg(color(p.panel))
        .rounded(u.w(1.0))
        .py(u.w(1.6))
        .px(u.w(1.8))
        .child(
            div()
                .text_size(u.w(1.3))
                .font_weight(FontWeight::SEMIBOLD)
                .text_color(color(p.muted))
                .mb(u.w(1.1))
                .child(spaced(title)),
        )
}

pub fn disks(items: &[DiskVm], p: &Palette, u: Vu) -> Div {
    panel("DISKS", p, u).child(div().flex().flex_col().gap(u.w(1.3)).children(items.iter().map(|d| {
        let fill = match d.level {
            Level::Ok => p.ok,
            Level::Warn => p.warn,
            Level::Bad => p.bad,
        };
        let mut used = div().text_color(color(p.muted)).child(d.used_text.clone());
        if !d.mounted {
            used = used.italic();
        }
        div()
            .child(div().flex().justify_between().text_size(u.w(1.7)).child(d.label.clone()).child(used))
            .child(
                div()
                    .h(u.w(1.1))
                    .mt(u.w(0.5))
                    .rounded(u.w(1.0))
                    .bg(color(p.line))
                    .overflow_hidden()
                    .child(div().h_full().w(relative(d.fill_pct / 100.0)).rounded(u.w(1.0)).bg(color(fill))),
            )
    })))
}

pub fn apps(items: &[AppVm], p: &Palette, u: Vu) -> Div {
    let list = if items.is_empty() {
        div().text_size(u.w(1.7)).text_color(color(p.muted)).child("No app data yet")
    } else {
        let last = items.len() - 1;
        // A name too long for its column ends in "…" (one-line clamp: GPUI caches a no-wrap text's first measure).
        div().flex().flex_col().flex_1().justify_around().children(items.iter().enumerate().map(|(i, a)| {
            div()
                .flex()
                .items_start()
                .gap(u.w(1.6))
                .text_size(u.w(2.1))
                .py(u.w(0.9))
                .when(i != last, |d| d.border_b(px(1.0)).border_color(color(p.line)))
                .child(div().flex_1().min_w_0().overflow_hidden().line_clamp(1).text_ellipsis().child(a.name.clone()))
                .child(div().flex_none().whitespace_nowrap().min_w(u.w(6.0)).text_right().font_weight(SEMIBOLD_650).child(a.cpu_text.clone()))
                .child(div().flex_none().whitespace_nowrap().min_w(u.w(7.0)).text_right().text_color(color(p.muted)).child(a.mem_text.clone()))
        }))
    };
    panel("TOP APPS", p, u).flex().flex_col().child(list)
}
