use crate::{
    bridge::Bridge,
    completion::Completion,
    editor::TextBuffer,
    model::{Group, Item, Section},
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
    pub preview_scroll: u16,
    pub completion: Option<Completion>,
}
impl EditorState {
    pub fn dirty(&self) -> bool {
        self.buffer.text() != self.original
    }
    pub fn mark_changed(&mut self) {
        self.dirty_since = Some(Instant::now());
        self.rendered = None;
        self.completion = None;
    }
    pub fn toggle_preview(&mut self) {
        self.preview = !self.preview;
        if self.preview {
            self.rendered = Some(crate::markdown::render(&self.buffer.text()));
            self.preview_scroll = 0;
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StatusFilter {
    All,
    Unread,
    Reading,
    Read,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DisplayRow {
    Header(Option<String>),
    Empty,
    Item(usize),
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
    pub sections: Vec<Section>,
    pub display_rows: Vec<DisplayRow>,
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
            sections: vec![],
            display_rows: vec![],
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
    fn rebuild_display_rows(&mut self) {
        self.display_rows.clear();
        if self.sections.is_empty() {
            self.display_rows
                .extend((0..self.items.len()).map(DisplayRow::Item));
            return;
        }
        let index_by_id = self
            .items
            .iter()
            .enumerate()
            .map(|(i, item)| (item.id.as_str(), i))
            .collect::<std::collections::HashMap<_, _>>();
        for section in &self.sections {
            let show_header = !section.title.is_empty();
            if show_header {
                self.display_rows
                    .push(DisplayRow::Header(section.id.clone()));
            }
            let mut count = 0;
            for id in &section.item_ids {
                if let Some(&index) = index_by_id.get(id.as_str()) {
                    self.display_rows.push(DisplayRow::Item(index));
                    count += 1;
                }
            }
            if count == 0 && show_header {
                self.display_rows.push(DisplayRow::Empty);
            }
        }
    }
    fn selected_key(&self) -> Option<(String, Option<String>)> {
        let DisplayRow::Item(index) = self.display_rows.get(self.selected)? else {
            return None;
        };
        let item = self.items.get(*index)?;
        let section = self.display_rows[..=self.selected]
            .iter()
            .rev()
            .find_map(|row| {
                if let DisplayRow::Header(id) = row {
                    id.clone()
                } else {
                    None
                }
            });
        Some((item.id.clone(), section))
    }
    fn restore_selection(&mut self, key: Option<(String, Option<String>)>) {
        self.selected = 0;
        if let Some((id, section)) = key {
            if let Some(pos) = self.display_rows.iter().enumerate().find_map(|(pos, row)| {
                let DisplayRow::Item(index) = row else {
                    return None;
                };
                if self.items.get(*index).is_some_and(|item| item.id == id)
                    && self.display_rows[..=pos].iter().rev().find_map(|r| {
                        if let DisplayRow::Header(sid) = r {
                            sid.clone()
                        } else {
                            None
                        }
                    }) == section
                {
                    Some(pos)
                } else {
                    None
                }
            }) {
                self.selected = pos;
                return;
            }
        }
        self.selected = (0..self.display_rows.len())
            .find(|&pos| matches!(self.display_rows[pos], DisplayRow::Item(_)))
            .unwrap_or(0);
    }
    fn next_selectable(&self, from: usize, delta: i32) -> Option<usize> {
        if self.display_rows.is_empty() {
            return None;
        }
        let len = self.display_rows.len() as i32;
        for step in 1..=len {
            let pos = (from as i32 + delta * step).rem_euclid(len) as usize;
            if matches!(self.display_rows[pos], DisplayRow::Item(_)) {
                return Some(pos);
            }
        }
        None
    }
    pub fn refresh(&mut self, bridge: &mut Bridge) {
        let key = self.selected_key();
        match bridge.list(self.filter.as_str(), self.group.as_deref(), &self.query) {
            Ok((items, groups, sections)) => {
                self.items = items;
                self.groups = groups;
                self.sections = sections;
                self.rebuild_display_rows();
                self.restore_selection(key);
                self.error = None;
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    pub fn move_selection(&mut self, delta: i32) {
        if self.display_rows.is_empty() && !self.items.is_empty() {
            self.rebuild_display_rows();
        }
        if let Some(next) = self.next_selectable(self.selected, delta) {
            self.selected = next;
        }
        self.metadata_scroll = 0;
    }
    pub fn cycle_group(&mut self, delta: i32) {
        // Slot zero is the unfiltered view; group slots follow it. A stale ID
        // is treated as the unfiltered slot so both directions remain safe.
        let current = self
            .group
            .as_deref()
            .and_then(|id| {
                self.groups
                    .iter()
                    .filter(|g| g.parent_id.is_none())
                    .position(|g| g.id == id)
                    .map(|i| i + 1)
            })
            .unwrap_or(0) as i32;
        let roots = self
            .groups
            .iter()
            .filter(|g| g.parent_id.is_none())
            .collect::<Vec<_>>();
        let count = roots.len() as i32 + 1;
        let next = (current + delta).rem_euclid(count) as usize;
        self.group = next
            .checked_sub(1)
            .and_then(|i| roots.get(i))
            .map(|g| g.id.clone());
        self.selected = 0;
        self.metadata_scroll = 0;
    }
    pub fn delete_selected(&mut self, bridge: &mut Bridge) {
        let Some(id) = self.selected_item().map(|item| item.id.clone()) else {
            return;
        };
        match bridge.delete_item(&id) {
            Ok(()) => {
                self.metadata_scroll = 0;
                self.refresh(bridge);
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    pub fn selected_item(&self) -> Option<&Item> {
        if self.display_rows.is_empty() {
            return self.items.get(self.selected);
        }
        match self.display_rows.get(self.selected)? {
            DisplayRow::Item(index) => self.items.get(*index),
            _ => None,
        }
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
                if let Some(DisplayRow::Item(index)) = self.display_rows.get(self.selected) {
                    let index = *index;
                    if let Some(item) = self.items.get_mut(index) {
                        item.note_path = Some(note.path.clone());
                    }
                }
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
                    preview_scroll: 0,
                    completion: None,
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

    #[test]
    fn group_cycles_forward_and_backward_with_all_and_stale_ids() {
        let mut app = App {
            groups: vec![
                Group {
                    id: "one".into(),
                    name: "One".into(),
                    parent_id: None,
                },
                Group {
                    id: "two".into(),
                    name: "Two".into(),
                    parent_id: None,
                },
            ],
            ..Default::default()
        };
        app.cycle_group(1);
        assert_eq!(app.group.as_deref(), Some("one"));
        app.cycle_group(1);
        assert_eq!(app.group.as_deref(), Some("two"));
        app.cycle_group(1);
        assert_eq!(app.group, None);
        app.cycle_group(-1);
        assert_eq!(app.group.as_deref(), Some("two"));
        app.group = Some("stale".into());
        app.cycle_group(-1);
        assert_eq!(app.group.as_deref(), Some("two"));
    }
}
