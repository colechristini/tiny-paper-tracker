use crate::{
    bridge::Bridge,
    editor::TextBuffer,
    model::{Group, Item},
};
use std::time::{Duration, Instant};

pub struct EditorState {
    pub item_id: String,
    pub buffer: TextBuffer,
    pub revision: String,
    pub path: String,
    pub created_at: Option<String>,
    pub modified_at: String,
    pub original: String,
    pub dirty_since: Option<Instant>,
    pub scroll: u16,
    pub preview: bool,
    pub rendered: Option<ratatui::text::Text<'static>>,
}
impl EditorState {
    pub fn dirty(&self) -> bool {
        self.buffer.text() != self.original
    }
    pub fn mark_changed(&mut self) {
        self.dirty_since = Some(Instant::now());
        self.rendered = None;
    }
    pub fn toggle_preview(&mut self) {
        self.preview = !self.preview;
        if self.preview { self.rendered = Some(crate::markdown::render(&self.buffer.text())); }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StatusFilter {
    All,
    Unread,
    Reading,
    Read,
}
impl StatusFilter {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::All => "all",
            Self::Unread => "unread",
            Self::Reading => "reading",
            Self::Read => "read",
        }
    }
    pub fn next(self) -> Self {
        match self {
            Self::All => Self::Unread,
            Self::Unread => Self::Reading,
            Self::Reading => Self::Read,
            Self::Read => Self::All,
        }
    }
}

pub struct App {
    pub items: Vec<Item>,
    pub groups: Vec<Group>,
    pub selected: usize,
    pub filter: StatusFilter,
    pub group: Option<String>,
    pub query: String,
    pub searching: bool,
    pub search_input: String,
    pub error: Option<String>,
    pub should_quit: bool,
    pub help: bool,
    pub editor: Option<EditorState>,
    pub metadata_scroll: u16,
}
impl Default for App {
    fn default() -> Self {
        Self {
            items: vec![],
            groups: vec![],
            selected: 0,
            filter: StatusFilter::All,
            group: None,
            query: String::new(),
            searching: false,
            search_input: String::new(),
            error: None,
            should_quit: false,
            help: false,
            editor: None,
            metadata_scroll: 0,
        }
    }
}
impl App {
    pub fn refresh(&mut self, bridge: &mut Bridge) {
        match bridge.list(self.filter.as_str(), self.group.as_deref(), &self.query) {
            Ok((items, groups)) => {
                self.items = items;
                self.groups = groups;
                self.selected = self.selected.min(self.items.len().saturating_sub(1));
                self.error = None;
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    pub fn move_selection(&mut self, delta: i32) {
        if self.items.is_empty() {
            return;
        }
        let n = self.items.len() as i32;
        self.selected = ((self.selected as i32 + delta).rem_euclid(n)) as usize;
        self.metadata_scroll = 0;
    }
    pub fn cycle_group(&mut self) {
        self.group = match self.group.as_deref() {
            None => self.groups.first().map(|g| g.id.clone()),
            Some(id) => self
                .groups
                .iter()
                .position(|g| g.id == id)
                .and_then(|i| self.groups.get(i + 1))
                .map(|g| g.id.clone()),
        };
        self.selected = 0;
    }
    pub fn selected_item(&self) -> Option<&Item> {
        self.items.get(self.selected)
    }
    pub fn set_status(&mut self, bridge: &mut Bridge, status: &str) {
        if let Some(id) = self.selected_item().map(|x| x.id.clone()) {
            if let Err(e) = bridge.set_status(&id, status) {
                self.error = Some(e.to_string());
            } else {
                self.refresh(bridge);
            }
        }
    }
    pub fn open_editor(&mut self, bridge: &mut Bridge) {
        let Some(id) = self.selected_item().map(|i| i.id.clone()) else {
            return;
        };
        match bridge.note_open(&id) {
            Ok(note) => {
                self.editor = Some(EditorState {
                    item_id: id,
                    buffer: TextBuffer::new(note.text.clone()),
                    revision: note.revision,
                    path: note.path,
                    created_at: note.created_at,
                    modified_at: note.modified_at,
                    original: note.text,
                    dirty_since: None,
                    scroll: 0,
                    preview: false,
                    rendered: None,
                })
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    pub fn save_editor(&mut self, bridge: &mut Bridge) -> Result<(), String> {
        let Some(editor) = self.editor.as_mut() else {
            return Ok(());
        };
        if !editor.dirty() {
            editor.dirty_since = None;
            return Ok(());
        }
        let text = editor.buffer.text();
        let result = bridge.note_save(&editor.item_id, &text, &editor.revision);
        match result {
            Ok(note) => {
                editor.revision = note.revision;
                editor.path = note.path;
                editor.created_at = note.created_at;
                editor.modified_at = note.modified_at;
                editor.original = text;
                editor.dirty_since = None;
                self.error = None;
                Ok(())
            }
            Err(e) => {
                editor.dirty_since = None;
                let message = format!(
                    "Save failed: {e}. Ctrl-S retries; Ctrl-E saves a recovery copy; Esc retries save."
                );
                self.error = Some(message);
                Err(e.to_string())
            }
        }
    }
    pub fn autosave_due(&self) -> bool {
        self.editor
            .as_ref()
            .and_then(|e| e.dirty_since)
            .is_some_and(|t| t.elapsed() >= Duration::from_millis(750))
    }
    pub fn recover_editor(&mut self, bridge: &mut Bridge) {
        let Some(editor) = self.editor.as_ref() else {
            return;
        };
        let id = editor.item_id.clone();
        let text = editor.buffer.text();
        match bridge.note_recover(&id, &text) {
            Ok(path) => {
                self.editor = None;
                self.error = Some(format!("Recovery saved: {path}"));
            }
            Err(e) => self.error = Some(format!("Recovery failed: {e}. Buffer retained.")),
        }
    }
    pub fn insert_editor_text(&mut self, text: &str) {
        if let Some(editor) = self.editor.as_mut() {
            editor.buffer.insert(text);
            editor.mark_changed();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selection_wraps() {
        let mut app = App {
            items: (0..3)
                .map(|i| Item {
                    id: i.to_string(),
                    title: i.to_string(),
                    url: String::new(),
                    kind: String::new(),
                    authors: vec![],
                    venue: None,
                    published_at: None,
                    status: String::new(),
                    added_at: None,
                    read_at: None,
                    note_path: None,
                    tags: vec![],
                    groups: vec![],
                    identifiers: vec![],
                    metadata: serde_json::Value::Null,
                })
                .collect(),
            ..Default::default()
        };
        app.move_selection(-1);
        assert_eq!(app.selected, 2);
        app.move_selection(1);
        assert_eq!(app.selected, 0);
    }
    #[test]
    fn filter_cycles() {
        assert_eq!(StatusFilter::All.next(), StatusFilter::Unread);
        assert_eq!(StatusFilter::Read.next(), StatusFilter::All);
    }
}
