use crate::{
    VERSION,
    model::{Item, ListResponse, MutationResponse},
};
use serde::Serialize;
use std::{
    env,
    io::{self, BufRead, BufReader, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
};

#[derive(Debug)]
pub enum BridgeError {
    Io(io::Error),
    Json(serde_json::Error),
    Backend(String),
}
impl std::fmt::Display for BridgeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "bridge I/O: {e}"),
            Self::Json(e) => write!(f, "bridge JSON: {e}"),
            Self::Backend(e) => f.write_str(e),
        }
    }
}
impl std::error::Error for BridgeError {}
impl From<io::Error> for BridgeError {
    fn from(e: io::Error) -> Self {
        Self::Io(e)
    }
}
impl From<serde_json::Error> for BridgeError {
    fn from(e: serde_json::Error) -> Self {
        Self::Json(e)
    }
}

#[derive(Debug, Serialize)]
struct ListRequest<'a> {
    version: u64,
    op: &'static str,
    status: &'a str,
    group: Option<&'a str>,
    query: &'a str,
}
#[derive(Debug, Serialize)]
struct StatusRequest<'a> {
    version: u64,
    op: &'static str,
    id: &'a str,
    status: &'a str,
}

pub struct Bridge {
    child: Child,
    input: Option<ChildStdin>,
    output: BufReader<ChildStdout>,
}
impl Bridge {
    pub fn spawn() -> Result<Self, BridgeError> {
        let python = env::var("LIT_TUI_PYTHON")
            .map_err(|_| BridgeError::Backend("LIT_TUI_PYTHON is not set".into()))?;
        let mut child = Command::new(python)
            .args(["-m", "tiny_reading_tracker.tui_bridge"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| BridgeError::Backend("bridge stdin unavailable".into()))?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| BridgeError::Backend("bridge stdout unavailable".into()))?;
        Ok(Self {
            child,
            input: Some(input),
            output: BufReader::new(output),
        })
    }
    fn request<T: Serialize, R: serde::de::DeserializeOwned>(
        &mut self,
        request: &T,
    ) -> Result<R, BridgeError> {
        let input = self
            .input
            .as_mut()
            .ok_or_else(|| BridgeError::Backend("bridge stdin unavailable".into()))?;
        serde_json::to_writer(&mut *input, request)?;
        input.write_all(b"\n")?;
        input.flush()?;
        let mut line = String::new();
        self.output.read_line(&mut line)?;
        if line.is_empty() {
            return Err(BridgeError::Backend("bridge exited unexpectedly".into()));
        }
        Ok(serde_json::from_str(&line)?)
    }
    pub fn list(
        &mut self,
        status: &str,
        group: Option<&str>,
        query: &str,
    ) -> Result<(Vec<Item>, Vec<crate::model::Group>), BridgeError> {
        let response: ListResponse = self.request(&ListRequest {
            version: VERSION,
            op: "list",
            status,
            group,
            query,
        })?;
        if !response.ok {
            return Err(BridgeError::Backend(
                response
                    .error
                    .map(|e| e.message)
                    .unwrap_or_else(|| "backend request failed".into()),
            ));
        }
        if response.version != VERSION {
            return Err(BridgeError::Backend(
                "unsupported bridge protocol version".into(),
            ));
        }
        Ok((response.items, response.groups))
    }
    pub fn set_status(&mut self, id: &str, status: &str) -> Result<Item, BridgeError> {
        let response: MutationResponse = self.request(&StatusRequest {
            version: VERSION,
            op: "set_status",
            id,
            status,
        })?;
        if !response.ok {
            return Err(BridgeError::Backend(
                response
                    .error
                    .map(|e| e.message)
                    .unwrap_or_else(|| "backend request failed".into()),
            ));
        }
        response
            .item
            .ok_or_else(|| BridgeError::Backend("backend returned no item".into()))
    }
}
impl Drop for Bridge {
    fn drop(&mut self) {
        self.input.take();
        let _ = self.child.wait();
    }
}
