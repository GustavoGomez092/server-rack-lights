//! Reads led-notify's SSE stream on its own thread and hands each payload to the UI. Reconnects forever:
//! led-notify restarts on every deploy, and the screen just shows "reconnecting" meanwhile.
use std::io::Read;
use std::time::Duration;

use async_channel::{Receiver, Sender};
use rack_screen_core::payload::ScreenData;
use rack_screen_core::sse::SseParser;

pub fn spawn(url: String) -> Receiver<ScreenData> {
    let (tx, rx) = async_channel::bounded(4);
    std::thread::spawn(move || {
        // Pushes come every 2 s, so 15 s of silence means a dead connection (led-notify killed without a FIN).
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(5))
            .timeout_read(Duration::from_secs(15))
            .build();
        loop {
            if let Err(e) = stream(&agent, &url, &tx) {
                eprintln!("feed: {e}");
            }
            if tx.is_closed() {
                return;
            }
            std::thread::sleep(Duration::from_secs(2));
        }
    });
    rx
}

fn stream(agent: &ureq::Agent, url: &str, tx: &Sender<ScreenData>) -> Result<(), Box<dyn std::error::Error>> {
    let mut reader = agent.get(url).set("Accept", "text/event-stream").call()?.into_reader();
    let mut parser = SseParser::default();
    let (mut buf, mut pending) = ([0u8; 8192], Vec::new());
    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            return Ok(());
        }
        pending.extend_from_slice(&buf[..n]);
        // A read can end inside a multi-byte character (°, ·); keep that tail for the next read.
        let valid = match std::str::from_utf8(&pending) {
            Ok(s) => s.len(),
            Err(e) => e.valid_up_to(),
        };
        let text = String::from_utf8(pending.drain(..valid).collect()).expect("valid prefix");
        for ev in parser.feed(&text) {
            if ev.event != "screen" {
                continue;
            }
            match serde_json::from_str(&ev.data) {
                Ok(data) => tx.send_blocking(data)?,
                Err(e) => eprintln!("feed: unreadable payload: {e}"),
            }
        }
    }
}
