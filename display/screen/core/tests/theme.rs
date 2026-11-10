use rack_screen_core::theme::{palette, Rgb};
use rack_screen_core::tween::{ease_out, Tween, DURATION_MS};
use rack_screen_core::view::Theme;

#[test]
fn palettes_match_the_old_css() {
    let d = palette(Theme::Dark);
    assert_eq!([d.bg, d.panel, d.line, d.text, d.muted], [Rgb(0x0b, 0x0f, 0x14), Rgb(0x12, 0x18, 0x21), Rgb(0x1e, 0x27, 0x32), Rgb(0xe6, 0xed, 0xf3), Rgb(0x8b, 0x98, 0xa5)]);
    assert_eq!([d.cpu, d.ram, d.temp, d.ok, d.warn, d.bad], [Rgb(0x4c, 0xc2, 0xff), Rgb(0xb1, 0x8c, 0xff), Rgb(0x5b, 0xe4, 0x9b), Rgb(0x5b, 0xe4, 0x9b), Rgb(0xff, 0xb3, 0x47), Rgb(0xff, 0x5d, 0x5d)]);
    assert_eq!([d.banner_bad_bg, d.banner_warn_bg], [Rgb(0x3a, 0x12, 0x16), Rgb(0x3a, 0x2a, 0x10)]);
    let l = palette(Theme::Light);
    assert_eq!([l.bg, l.panel, l.line, l.text, l.muted], [Rgb(0xee, 0xf1, 0xf4), Rgb(0xff, 0xff, 0xff), Rgb(0xe1, 0xe6, 0xec), Rgb(0x14, 0x20, 0x2b), Rgb(0x5d, 0x6b, 0x78)]);
    assert_eq!([l.cpu, l.ram, l.temp, l.ok, l.warn, l.bad], [Rgb(0x1f, 0x7f, 0xd1), Rgb(0x7a, 0x4f, 0xd6), Rgb(0x13, 0x8a, 0x55), Rgb(0x13, 0x8a, 0x55), Rgb(0xc2, 0x71, 0x00), Rgb(0xd1, 0x2f, 0x2f)]);
    assert_eq!([l.banner_bad_bg, l.banner_warn_bg], [Rgb(0xfd, 0xe4, 0xe4), Rgb(0xff, 0xf1, 0xd6)]);
}

#[test]
fn brightness_mixes_toward_black() {
    let d = palette(Theme::Dark);
    assert_eq!(d.dim(0.5).cpu, Rgb(38, 97, 128));
    assert_eq!(d.dim(1.0), d);
}

#[test]
fn theme_change_blends() {
    let (d, l) = (palette(Theme::Dark), palette(Theme::Light));
    assert_eq!(d.mix(&l, 0.0), d);
    assert_eq!(d.mix(&l, 1.0), l);
    let half = d.mix(&l, 0.5).bg;
    assert!(half.0 > d.bg.0 && half.0 < l.bg.0);
}

#[test]
fn tween_eases_out_over_300ms() {
    let t = Tween::new(10.0, 50.0, 1000.0);
    assert_eq!(t.value(1000.0), 10.0);
    assert_eq!(t.value(1000.0 + DURATION_MS), 50.0);
    assert!(t.value(1150.0) > 30.0); // ease-out: past the linear midpoint halfway through
    assert!(!t.done(1299.0) && t.done(1300.0));
    assert!(ease_out(0.0).abs() < 1e-4 && (ease_out(1.0) - 1.0).abs() < 1e-4);
}

#[test]
fn retarget_starts_from_where_it_is() {
    let mut t = Tween::new(0.0, 100.0, 0.0);
    let mid = t.value(100.0);
    t.retarget(20.0, 100.0);
    assert!((t.value(100.0) - mid).abs() < 1e-4);
    assert_eq!(t.value(400.0), 20.0);
    let mut s = Tween::settled(42.0);
    assert!(s.done(0.0) && s.value(0.0) == 42.0);
    s.retarget(42.0, 5.0);
    assert!(s.done(5.0)); // same target: nothing to animate
}
