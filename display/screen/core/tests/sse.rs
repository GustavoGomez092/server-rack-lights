use rack_screen_core::sse::{SseEvent, SseParser};
use rack_screen_core::stale::Staleness;

const ONE: &str = "event: screen\ndata: {\"a\":1}\n\n";

fn ev(event: &str, data: &str) -> SseEvent {
    SseEvent { event: event.into(), data: data.into() }
}

#[test]
fn one_event() {
    assert_eq!(SseParser::default().feed(ONE), [ev("screen", "{\"a\":1}")]);
}

#[test]
fn split_anywhere() {
    for cut in 1..ONE.len() {
        let mut p = SseParser::default();
        let mut got = p.feed(&ONE[..cut]);
        if cut < ONE.len() - 1 {
            assert!(got.is_empty(), "event before the blank line (cut {cut})");
        }
        got.extend(p.feed(&ONE[cut..]));
        assert_eq!(got, [ev("screen", "{\"a\":1}")], "cut {cut}");
    }
}

#[test]
fn crlf_multiline_comments_default_name() {
    let mut p = SseParser::default();
    let got = p.feed(": keepalive\r\n\r\ndata: a\r\ndata: b\r\n\r\nevent: screen\ndata:x\n\n");
    assert_eq!(got, [ev("message", "a\nb"), ev("screen", "x")]);
}

#[test]
fn stale_after_ten_seconds() {
    let mut s = Staleness::new(0.0);
    assert!(!s.is_stale(9_900.0));
    assert!(s.is_stale(10_100.0)); // nothing since start counts too
    s.touch(10_100.0);
    assert!(!s.is_stale(20_000.0));
    assert!(s.is_stale(20_200.0));
}
