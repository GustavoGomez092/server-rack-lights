//! Server-sent events, fed chunk by chunk as they arrive off the socket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SseEvent {
    pub event: String,
    pub data: String,
}

#[derive(Debug, Default)]
pub struct SseParser {
    line: String,
    event: String,
    data: Vec<String>,
}

impl SseParser {
    pub fn feed(&mut self, chunk: &str) -> Vec<SseEvent> {
        let mut out = Vec::new();
        for c in chunk.chars() {
            if c != '\n' {
                self.line.push(c);
                continue;
            }
            let line = std::mem::take(&mut self.line);
            let line = line.strip_suffix('\r').unwrap_or(&line);
            if line.is_empty() {
                let event = std::mem::take(&mut self.event);
                if !self.data.is_empty() {
                    out.push(SseEvent {
                        event: if event.is_empty() { "message".into() } else { event },
                        data: std::mem::take(&mut self.data).join("\n"),
                    });
                }
                continue;
            }
            let (field, value) = line.split_once(':').unwrap_or((line, ""));
            let value = value.strip_prefix(' ').unwrap_or(value);
            match field {
                "event" => self.event = value.into(),
                "data" => self.data.push(value.into()),
                _ => {} // comments (empty field) and fields we don't use
            }
        }
        out
    }
}
