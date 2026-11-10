//! The two themes' colors (from the old page's screen.css) and brightness dimming.
use crate::view::Theme;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rgb(pub u8, pub u8, pub u8);

pub const BLACK: Rgb = Rgb(0, 0, 0);

impl Rgb {
    const fn hex(v: u32) -> Self {
        Rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
    }

    fn map(self, other: Rgb, f: impl Fn(f32, f32) -> f32) -> Rgb {
        let c = |a: u8, b: u8| f(a as f32, b as f32).round().clamp(0.0, 255.0) as u8;
        Rgb(c(self.0, other.0), c(self.1, other.1), c(self.2, other.2))
    }

    pub fn to_u32(self) -> u32 {
        (self.0 as u32) << 16 | (self.1 as u32) << 8 | self.2 as u32
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Palette {
    pub bg: Rgb,
    pub panel: Rgb,
    pub line: Rgb,
    pub text: Rgb,
    pub muted: Rgb,
    pub cpu: Rgb,
    pub ram: Rgb,
    pub temp: Rgb,
    pub ok: Rgb,
    pub warn: Rgb,
    pub bad: Rgb,
    pub banner_bad_bg: Rgb,
    pub banner_warn_bg: Rgb,
}

pub fn palette(theme: Theme) -> Palette {
    let h = Rgb::hex;
    match theme {
        Theme::Dark => Palette {
            bg: h(0x0b0f14), panel: h(0x121821), line: h(0x1e2732), text: h(0xe6edf3), muted: h(0x8b98a5),
            cpu: h(0x4cc2ff), ram: h(0xb18cff), temp: h(0x5be49b), ok: h(0x5be49b), warn: h(0xffb347),
            bad: h(0xff5d5d), banner_bad_bg: h(0x3a1216), banner_warn_bg: h(0x3a2a10),
        },
        Theme::Light => Palette {
            bg: h(0xeef1f4), panel: h(0xffffff), line: h(0xe1e6ec), text: h(0x14202b), muted: h(0x5d6b78),
            cpu: h(0x1f7fd1), ram: h(0x7a4fd6), temp: h(0x138a55), ok: h(0x138a55), warn: h(0xc27100),
            bad: h(0xd12f2f), banner_bad_bg: h(0xfde4e4), banner_warn_bg: h(0xfff1d6),
        },
    }
}

impl Palette {
    fn zip(&self, other: &Palette, f: impl Fn(f32, f32) -> f32 + Copy) -> Palette {
        Palette {
            bg: self.bg.map(other.bg, f), panel: self.panel.map(other.panel, f), line: self.line.map(other.line, f),
            text: self.text.map(other.text, f), muted: self.muted.map(other.muted, f),
            cpu: self.cpu.map(other.cpu, f), ram: self.ram.map(other.ram, f), temp: self.temp.map(other.temp, f),
            ok: self.ok.map(other.ok, f), warn: self.warn.map(other.warn, f), bad: self.bad.map(other.bad, f),
            banner_bad_bg: self.banner_bad_bg.map(other.banner_bad_bg, f),
            banner_warn_bg: self.banner_warn_bg.map(other.banner_warn_bg, f),
        }
    }

    /// The panel has no DDC/CI, so brightness is every color mixed toward black: `color-mix(in srgb, C keep, #000)`.
    pub fn dim(&self, keep: f32) -> Palette {
        self.zip(self, |a, _| a * keep)
    }
}
