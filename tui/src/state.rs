use crate::{
    bridge::{AddResult, Bridge},
    completion::Completion,
    editor::TextBuffer,
    model::{Group, Item, Section},
};
use std::{
    collections::HashSet,
    sync::mpsc::{self, Receiver, TryRecvError},
    time::{Duration, Instant},
};

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
pub struct RenameState {
    pub item_id: String,
    pub buffer: TextBuffer,
}
pub struct AddState {
    pub buffer: TextBuffer,
    pub(crate) result: Option<Receiver<Result<AddResult, String>>>,
}
impl AddState {
    pub fn busy(&self) -> bool {
        self.result.is_some()
    }
}
pub struct MembershipState {
    pub item_id: String,
    pub choices: Vec<(String, String)>,
    pub checked: Vec<bool>,
    pub selected: usize,
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
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum NoteFilter {
    #[default]
    All,
    HasNote,
    NoNote,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DisplayRow {
    Header(Option<String>),
    Item(usize),
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct SelectionOccurrence {
    id: String,
    section: Option<String>,
}
struct SelectionSnapshot {
    selected: SelectionOccurrence,
    selected_index: usize,
    ordered: Vec<SelectionOccurrence>,
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
impl NoteFilter {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::All => "all",
            Self::HasNote => "has_note",
            Self::NoNote => "no_note",
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
    pub note_filter: NoteFilter,
    pub group: Option<String>,
    pub query: String,
    pub searching: bool,
    pub search_input: TextBuffer,
    pub error: Option<String>,
    pub should_quit: bool,
    pub help: bool,
    pub editor: Option<EditorState>,
    pub rename: Option<RenameState>,
    pub membership: Option<MembershipState>,
    pub add: Option<AddState>,
    pub notice: Option<String>,
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
            note_filter: NoteFilter::All,
            group: None,
            query: String::new(),
            searching: false,
            search_input: TextBuffer::new(String::new()),
            error: None,
            should_quit: false,
            help: false,
            editor: None,
            rename: None,
            membership: None,
            add: None,
            notice: None,
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
            let visible_items = section
                .item_ids
                .iter()
                .filter_map(|id| index_by_id.get(id.as_str()).copied())
                .collect::<Vec<_>>();
            if visible_items.is_empty() {
                continue;
            }
            if !section.title.is_empty() {
                self.display_rows
                    .push(DisplayRow::Header(section.id.clone()));
            }
            self.display_rows
                .extend(visible_items.into_iter().map(DisplayRow::Item));
        }
    }
    fn display_occurrences(&self) -> Vec<(usize, SelectionOccurrence)> {
        if self.display_rows.is_empty() {
            return self
                .items
                .iter()
                .enumerate()
                .map(|(row, item)| {
                    (
                        row,
                        SelectionOccurrence {
                            id: item.id.clone(),
                            section: None,
                        },
                    )
                })
                .collect();
        }
        let mut section = None;
        let mut occurrences = Vec::new();
        for (row, display_row) in self.display_rows.iter().enumerate() {
            match display_row {
                DisplayRow::Header(id) => section = id.clone(),
                DisplayRow::Item(index) => {
                    if let Some(item) = self.items.get(*index) {
                        occurrences.push((
                            row,
                            SelectionOccurrence {
                                id: item.id.clone(),
                                section: section.clone(),
                            },
                        ));
                    }
                }
            }
        }
        occurrences
    }
    fn selection_snapshot(&self) -> Option<SelectionSnapshot> {
        let displayed = self.display_occurrences();
        let selected_index = displayed
            .iter()
            .position(|(row, _)| *row == self.selected)?;
        Some(SelectionSnapshot {
            selected: displayed[selected_index].1.clone(),
            selected_index,
            ordered: displayed
                .into_iter()
                .map(|(_, occurrence)| occurrence)
                .collect(),
        })
    }
    fn restore_selection(&mut self, snapshot: Option<SelectionSnapshot>) {
        self.selected = 0;
        let current = self.display_occurrences();
        let Some(snapshot) = snapshot else {
            self.selected = current.first().map(|(row, _)| *row).unwrap_or(0);
            return;
        };
        let find = |target: &SelectionOccurrence, exact_section: bool| {
            current.iter().find_map(|(row, occurrence)| {
                (occurrence.id == target.id
                    && (!exact_section || occurrence.section == target.section))
                    .then_some(*row)
            })
        };
        if let Some(row) =
            find(&snapshot.selected, true).or_else(|| find(&snapshot.selected, false))
        {
            self.selected = row;
            return;
        }
        let mut skipped_ids = HashSet::from([snapshot.selected.id.clone()]);
        for occurrence in snapshot.ordered[..snapshot.selected_index].iter().rev() {
            if skipped_ids.insert(occurrence.id.clone())
                && let Some(row) = find(occurrence, true).or_else(|| find(occurrence, false))
            {
                self.selected = row;
                return;
            }
        }
        for occurrence in &snapshot.ordered[snapshot.selected_index + 1..] {
            if skipped_ids.insert(occurrence.id.clone())
                && let Some(row) = find(occurrence, true).or_else(|| find(occurrence, false))
            {
                self.selected = row;
                return;
            }
        }
        self.selected = current.first().map(|(row, _)| *row).unwrap_or(0);
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
        let snapshot = self.selection_snapshot();
        match bridge.list(
            self.filter.as_str(),
            self.group.as_deref(),
            &self.query,
            self.note_filter.as_str(),
        ) {
            Ok((items, groups, sections)) => {
                self.items = items;
                self.groups = groups;
                self.sections = sections;
                self.rebuild_display_rows();
                self.restore_selection(snapshot);
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
    pub fn toggle_note_filter(&mut self, requested: NoteFilter) {
        self.note_filter = if self.note_filter == requested {
            NoteFilter::All
        } else {
            requested
        };
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
    pub fn begin_rename(&mut self) {
        let Some(item) = self.selected_item() else {
            return;
        };
        let mut buffer = TextBuffer::new(item.title.clone());
        buffer.end();
        self.rename = Some(RenameState {
            item_id: item.id.clone(),
            buffer,
        });
        self.error = None;
    }
    pub fn cancel_rename(&mut self) {
        self.rename = None;
        self.error = None;
    }
    pub fn insert_rename_text(&mut self, text: &str) {
        if let Some(rename) = self.rename.as_mut() {
            rename.buffer.insert(&text.replace(['\r', '\n'], " "));
        }
    }
    pub fn submit_rename(&mut self, bridge: &mut Bridge) {
        let Some(rename) = self.rename.as_ref() else {
            return;
        };
        let title = rename.buffer.text().trim().to_string();
        if title.is_empty() {
            self.error = Some("Title cannot be blank".into());
            return;
        }
        let id = rename.item_id.clone();
        match bridge.rename_item(&id, &title) {
            Ok(_) => {
                self.rename = None;
                self.error = None;
                self.refresh(bridge);
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    pub fn begin_add(&mut self) {
        self.add = Some(AddState {
            buffer: TextBuffer::new(String::new()),
            result: None,
        });
        self.error = None;
        self.notice = None;
    }
    pub fn insert_add_text(&mut self, text: &str) {
        if let Some(add) = self.add.as_mut()
            && !add.busy()
        {
            add.buffer.insert(&text.replace(['\r', '\n'], " "));
        }
    }
    pub fn cancel_add(&mut self) {
        if self.add.as_ref().is_some_and(|add| !add.busy()) {
            self.add = None;
            self.error = None;
        }
    }
    pub fn submit_add(&mut self) {
        let Some(add) = self.add.as_mut() else {
            return;
        };
        if add.busy() {
            return;
        }
        let value = add.buffer.text().trim().to_string();
        if value.is_empty() {
            self.error = Some("Enter a URL or identifier".into());
            return;
        }
        let group_id = self.group.as_ref().and_then(|selected| {
            let group = self.groups.iter().find(|group| group.id == *selected)?;
            Some(group.parent_id.clone().unwrap_or_else(|| group.id.clone()))
        });
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let result = Bridge::spawn()
                .and_then(|mut bridge| bridge.add_item(&value, group_id.as_deref()))
                .map_err(|error| error.to_string());
            let _ = sender.send(result);
        });
        add.result = Some(receiver);
        self.error = None;
    }
    pub fn poll_add(&mut self, bridge: &mut Bridge) -> bool {
        let outcome = match self.add.as_ref().and_then(|add| add.result.as_ref()) {
            Some(receiver) => match receiver.try_recv() {
                Ok(result) => Some(result),
                Err(TryRecvError::Empty) => return false,
                Err(TryRecvError::Disconnected) => {
                    Some(Err("add worker stopped unexpectedly".into()))
                }
            },
            None => return false,
        };
        match outcome.expect("outcome set for active receiver") {
            Ok(result) => {
                let item_id = result.item.id.clone();
                self.focus_added_item(&result.item);
                self.add = None;
                self.refresh(bridge);
                if let Some(row) = self
                    .display_occurrences()
                    .iter()
                    .find(|(_, occurrence)| occurrence.id == item_id)
                    .map(|(row, _)| *row)
                {
                    self.selected = row;
                }
                self.notice = Some(format!(
                    "{}: {}",
                    if result.created {
                        "Added"
                    } else {
                        "Already saved"
                    },
                    result.item.title
                ));
            }
            Err(message) => {
                if let Some(add) = self.add.as_mut() {
                    add.result = None;
                }
                self.error = Some(format!("Add failed: {message}"));
            }
        }
        true
    }
    fn focus_added_item(&mut self, item: &Item) {
        let destination = self.group.clone().and_then(|selected| {
            self.groups
                .iter()
                .find(|group| group.id == selected)
                .map(|group| group.parent_id.clone().unwrap_or_else(|| group.id.clone()))
        });
        if let Some(destination) = destination {
            self.group = Some(destination);
        }
        if self.filter != StatusFilter::All && self.filter.as_str() != item.status {
            self.filter = StatusFilter::All;
        }
        let has_note = item
            .note_path
            .as_deref()
            .is_some_and(|path| !path.is_empty());
        if (self.note_filter == NoteFilter::HasNote && !has_note)
            || (self.note_filter == NoteFilter::NoNote && has_note)
        {
            self.note_filter = NoteFilter::All;
        }
        if !self.query.is_empty() {
            let query = self.query.to_lowercase();
            if !item.title.to_lowercase().contains(&query) {
                self.query.clear();
            }
        }
    }
    pub fn begin_membership(&mut self) {
        let Some(item) = self.selected_item() else {
            return;
        };
        let mut groups = Vec::new();
        let mut roots = self
            .groups
            .iter()
            .filter(|g| g.parent_id.is_none())
            .collect::<Vec<_>>();
        roots.sort_by_key(|g| g.name.to_lowercase());
        for root in roots {
            groups.push(root.clone());
            let mut children = self
                .groups
                .iter()
                .filter(|g| g.parent_id.as_deref() == Some(root.id.as_str()))
                .collect::<Vec<_>>();
            children.sort_by_key(|g| g.name.to_lowercase());
            groups.extend(children.into_iter().cloned());
        }
        let choices = groups
            .iter()
            .map(|g| {
                let label = if let Some(pid) = &g.parent_id {
                    let parent = groups
                        .iter()
                        .find(|p| p.id == *pid)
                        .map(|p| p.name.as_str())
                        .unwrap_or("");
                    format!("  {parent}/{}", g.name)
                } else {
                    g.name.clone()
                };
                (g.id.clone(), label)
            })
            .collect::<Vec<_>>();
        let mut checked: Vec<bool> = choices
            .iter()
            .map(|(id, _)| {
                item.groups
                    .iter()
                    .any(|v| v.get("id").and_then(|x| x.as_str()) == Some(id))
            })
            .collect();
        for (index, (id, _)) in choices.iter().enumerate() {
            if checked[index]
                && let Some(parent_id) = self
                    .groups
                    .iter()
                    .find(|group| group.id == *id)
                    .and_then(|group| group.parent_id.as_ref())
                && let Some(parent_index) =
                    choices.iter().position(|(choice, _)| choice == parent_id)
            {
                checked[parent_index] = true;
            }
        }
        self.membership = Some(MembershipState {
            item_id: item.id.clone(),
            choices,
            checked,
            selected: 0,
        });
        self.error = None;
    }
    pub fn membership_key(&mut self, key: crossterm::event::KeyCode) {
        let Some(picker) = self.membership.as_ref() else {
            return;
        };
        let selected = picker.selected;
        let selected_id = picker.choices.get(selected).map(|(id, _)| id.clone());
        match key {
            crossterm::event::KeyCode::Up | crossterm::event::KeyCode::Char('k') => {
                if let Some(picker) = self.membership.as_mut() {
                    picker.selected = picker.selected.saturating_sub(1)
                }
            }
            crossterm::event::KeyCode::Down | crossterm::event::KeyCode::Char('j') => {
                if let Some(picker) = self.membership.as_mut() {
                    picker.selected =
                        (picker.selected + 1).min(picker.choices.len().saturating_sub(1))
                }
            }
            crossterm::event::KeyCode::Char(' ') if !picker.choices.is_empty() => {
                let parent_id = selected_id.as_deref().and_then(|id| {
                    self.groups
                        .iter()
                        .find(|group| group.id == id)
                        .and_then(|group| group.parent_id.as_deref())
                });
                let child_ids = selected_id.as_deref().filter(|id| {
                    self.groups
                        .iter()
                        .any(|group| group.parent_id.as_deref() == Some(id))
                });
                let child_group_ids = child_ids.map(|root_id| {
                    self.groups
                        .iter()
                        .filter(|group| group.parent_id.as_deref() == Some(root_id))
                        .map(|group| group.id.clone())
                        .collect::<HashSet<_>>()
                });
                if let Some(picker) = self.membership.as_mut() {
                    let was_checked = picker.checked[selected];
                    picker.checked[selected] = !was_checked;
                    if !was_checked {
                        if let Some(parent_id) = parent_id
                            && let Some(parent_index) =
                                picker.choices.iter().position(|(id, _)| id == parent_id)
                        {
                            picker.checked[parent_index] = true;
                        }
                    } else if let Some(root_id) = child_ids
                        && let Some(root_index) =
                            picker.choices.iter().position(|(id, _)| id == root_id)
                    {
                        for (index, (id, _)) in picker.choices.iter().enumerate() {
                            if child_group_ids
                                .as_ref()
                                .is_some_and(|children| children.contains(id))
                            {
                                picker.checked[index] = false;
                            }
                        }
                        picker.checked[root_index] = false;
                    }
                }
            }
            _ => {}
        }
    }
    pub fn cancel_membership(&mut self) {
        self.membership = None;
        self.error = None;
    }
    pub fn submit_membership(&mut self, bridge: &mut Bridge) {
        let Some(picker) = self.membership.as_ref() else {
            return;
        };
        let id = picker.item_id.clone();
        let ids = picker
            .choices
            .iter()
            .zip(&picker.checked)
            .filter_map(|((gid, _), checked)| checked.then_some(gid.clone()))
            .collect::<Vec<_>>();
        match bridge.set_item_groups(&id, &ids) {
            Ok(_) => {
                self.membership = None;
                self.error = None;
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
                    "Save failed: {e}. Ctrl-S retries; Ctrl-G saves a recovery copy; Esc retries save."
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

    fn item(id: &str) -> Item {
        Item {
            id: id.into(),
            title: format!("Paper {id}"),
            url: String::new(),
            kind: String::new(),
            authors: vec![],
            venue: None,
            published_at: None,
            status: "unread".into(),
            added_at: None,
            read_at: None,
            note_path: None,
            tags: vec![],
            groups: vec![],
            identifiers: vec![],
            metadata: serde_json::Value::Null,
        }
    }
    fn group(id: &str, name: &str, parent_id: Option<&str>) -> Group {
        Group {
            id: id.into(),
            name: name.into(),
            parent_id: parent_id.map(str::to_string),
        }
    }
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
    fn note_filters_toggle_independently_without_resetting_selection() {
        let mut app = App {
            selected: 3,
            filter: StatusFilter::Unread,
            ..Default::default()
        };
        app.toggle_note_filter(NoteFilter::HasNote);
        assert_eq!(app.note_filter, NoteFilter::HasNote);
        assert_eq!(app.filter, StatusFilter::Unread);
        assert_eq!(app.selected, 3);
        app.toggle_note_filter(NoteFilter::NoNote);
        assert_eq!(app.note_filter, NoteFilter::NoNote);
        app.toggle_note_filter(NoteFilter::NoNote);
        assert_eq!(app.note_filter, NoteFilter::All);
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
        app.selected = 4;
        app.cycle_group(1);
        assert_eq!(app.group.as_deref(), Some("one"));
        assert_eq!(app.selected, 4);
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

    #[test]
    fn subgroup_rows_omit_empty_sections_and_keep_loose_item_first() {
        let mut app = App {
            items: vec![item("loose"), item("child-a")],
            sections: vec![
                Section {
                    id: None,
                    title: String::new(),
                    item_ids: vec!["loose".into()],
                },
                Section {
                    id: Some("one".into()),
                    title: "One".into(),
                    item_ids: vec!["child-a".into()],
                },
                Section {
                    id: Some("two".into()),
                    title: "Two".into(),
                    item_ids: vec![],
                },
            ],
            ..Default::default()
        };
        app.rebuild_display_rows();
        assert_eq!(
            app.display_rows,
            vec![
                DisplayRow::Item(0),
                DisplayRow::Header(Some("one".into())),
                DisplayRow::Item(1),
            ]
        );
        assert_eq!(app.selected, 0);
        assert_eq!(app.selected_item().unwrap().id, "loose");
        app.move_selection(1);
        assert_eq!(app.selected, 2);
        app.move_selection(1);
        assert_eq!(app.selected, 0);
        app.move_selection(-1);
        assert_eq!(app.selected, 2);
    }

    #[test]
    fn filtering_out_last_child_hides_its_section_and_selects_previous_paper() {
        let mut app = App {
            items: vec![item("loose"), item("child")],
            sections: vec![
                Section {
                    id: None,
                    title: String::new(),
                    item_ids: vec!["loose".into()],
                },
                Section {
                    id: Some("child-group".into()),
                    title: "Child".into(),
                    item_ids: vec!["child".into()],
                },
            ],
            ..Default::default()
        };
        app.rebuild_display_rows();
        app.selected = 2;
        let snapshot = app.selection_snapshot();

        app.items.retain(|paper| paper.id != "child");
        app.sections[1].item_ids.clear();
        app.rebuild_display_rows();
        app.restore_selection(snapshot);

        assert_eq!(app.display_rows, vec![DisplayRow::Item(0)]);
        assert_eq!(app.selected_item().unwrap().id, "loose");
    }

    #[test]
    fn no_visible_items_produce_no_section_rows() {
        let mut app = App {
            sections: vec![Section {
                id: Some("empty".into()),
                title: "Empty subgroup".into(),
                item_ids: vec!["missing".into()],
            }],
            ..Default::default()
        };

        app.rebuild_display_rows();

        assert!(app.display_rows.is_empty());
        assert!(app.selected_item().is_none());
    }

    #[test]
    fn duplicate_occurrence_restores_by_item_and_section() {
        let mut app = App {
            items: vec![item("paper")],
            sections: vec![
                Section {
                    id: Some("one".into()),
                    title: "One".into(),
                    item_ids: vec!["paper".into()],
                },
                Section {
                    id: Some("two".into()),
                    title: "Two".into(),
                    item_ids: vec!["paper".into()],
                },
            ],
            ..Default::default()
        };
        app.rebuild_display_rows();
        app.selected = 3;
        assert_eq!(app.selected_item().unwrap().id, "paper");
        let key = app.selection_snapshot();
        app.sections.reverse();
        app.rebuild_display_rows();
        app.restore_selection(key);
        assert_eq!(app.selected, 1);
        assert_eq!(app.selected_item().unwrap().id, "paper");
    }

    #[test]
    fn deleting_a_duplicated_selection_chooses_the_nearest_previous_paper() {
        let mut app = App {
            items: vec![item("previous"), item("deleted"), item("next")],
            sections: vec![
                Section {
                    id: Some("one".into()),
                    title: "One".into(),
                    item_ids: vec!["previous".into(), "deleted".into()],
                },
                Section {
                    id: Some("two".into()),
                    title: "Two".into(),
                    item_ids: vec!["deleted".into(), "next".into()],
                },
            ],
            ..Default::default()
        };
        app.rebuild_display_rows();
        app.selected = 4;
        let snapshot = app.selection_snapshot();

        app.items.retain(|paper| paper.id != "deleted");
        app.sections[0].item_ids = vec!["previous".into()];
        app.sections[1].item_ids = vec!["next".into()];
        app.rebuild_display_rows();
        app.restore_selection(snapshot);

        assert_eq!(app.selected_item().unwrap().id, "previous");
    }

    #[test]
    fn membership_section_move_keeps_the_same_paper_selected() {
        let mut app = App {
            items: vec![item("paper")],
            sections: vec![
                Section {
                    id: Some("root".into()),
                    title: "Root".into(),
                    item_ids: vec!["paper".into()],
                },
                Section {
                    id: Some("child".into()),
                    title: "Child".into(),
                    item_ids: vec![],
                },
            ],
            ..Default::default()
        };
        app.rebuild_display_rows();
        app.selected = 1;
        let snapshot = app.selection_snapshot();

        app.sections[0].item_ids.clear();
        app.sections[1].item_ids.push("paper".into());
        app.rebuild_display_rows();
        app.restore_selection(snapshot);

        assert_eq!(app.selected_item().unwrap().id, "paper");
        assert_eq!(
            app.display_occurrences()[0].1.section.as_deref(),
            Some("child")
        );
    }

    #[test]
    fn filtered_out_selection_prefers_previous_then_next() {
        let mut app = App {
            items: vec![item("a"), item("b"), item("c"), item("d")],
            ..Default::default()
        };
        app.selected = 2;
        let snapshot = app.selection_snapshot();
        app.items.remove(2);
        app.restore_selection(snapshot);
        assert_eq!(app.selected_item().unwrap().id, "b");

        app.items = vec![item("a"), item("b"), item("c")];
        app.selected = 0;
        let snapshot = app.selection_snapshot();
        app.items.remove(0);
        app.restore_selection(snapshot);
        assert_eq!(app.selected_item().unwrap().id, "b");
    }

    #[test]
    fn add_buffer_edits_unicode_and_cancel_discards_the_draft() {
        let mut app = App::default();
        app.begin_add();
        app.insert_add_text("doi:世界🙂\n");
        let add = app.add.as_mut().unwrap();
        add.buffer.left();
        add.buffer.backspace();
        assert_eq!(add.buffer.text(), "doi:世界 ");

        app.cancel_add();
        assert!(app.add.is_none());
    }

    #[test]
    fn pending_add_rejects_edits_resubmission_and_cancel() {
        let (_sender, receiver) = mpsc::channel();
        let mut app = App {
            add: Some(AddState {
                buffer: TextBuffer::new("doi:10/example".into()),
                result: Some(receiver),
            }),
            ..Default::default()
        };

        app.insert_add_text("changed");
        app.submit_add();
        app.cancel_add();

        let add = app.add.as_ref().unwrap();
        assert!(add.busy());
        assert_eq!(add.buffer.text(), "doi:10/example");
    }

    #[test]
    fn add_focus_clears_only_filters_that_hide_the_result() {
        let mut app = App {
            groups: vec![
                group("root", "Root", None),
                group("child", "Child", Some("root")),
                group("other", "Other", None),
            ],
            group: Some("child".into()),
            filter: StatusFilter::Unread,
            note_filter: NoteFilter::NoNote,
            query: "paper".into(),
            ..Default::default()
        };
        let mut added = item("Paper");
        added.status = "read".into();
        added.note_path = Some("note.md".into());
        added.authors = vec!["Author".into()];
        app.focus_added_item(&added);
        assert_eq!(app.group.as_deref(), Some("root"));
        assert_eq!(app.filter, StatusFilter::All);
        assert_eq!(app.note_filter, NoteFilter::All);
        assert_eq!(app.query, "paper");

        app.group = Some("other".into());
        app.filter = StatusFilter::Read;
        app.note_filter = NoteFilter::HasNote;
        app.query = "missing".into();
        app.focus_added_item(&added);
        assert_eq!(app.group.as_deref(), Some("other"));
        assert_eq!(app.filter, StatusFilter::Read);
        assert_eq!(app.note_filter, NoteFilter::HasNote);
        assert!(app.query.is_empty());
    }

    #[test]
    fn group_cycle_ignores_child_groups() {
        let mut app = App {
            groups: vec![
                Group {
                    id: "root".into(),
                    name: "Root".into(),
                    parent_id: None,
                },
                Group {
                    id: "child".into(),
                    name: "Child".into(),
                    parent_id: Some("root".into()),
                },
            ],
            ..Default::default()
        };
        app.cycle_group(1);
        assert_eq!(app.group.as_deref(), Some("root"));
        app.cycle_group(1);
        assert_eq!(app.group, None);
    }

    #[test]
    fn rename_buffer_is_prefilled_and_single_line() {
        let mut app = App {
            items: vec![item("x")],
            ..Default::default()
        };
        app.begin_rename();
        assert_eq!(app.rename.as_ref().unwrap().buffer.text(), "Paper x");
        assert_eq!(app.rename.as_ref().unwrap().buffer.cursor, 7);
        app.insert_rename_text(" 新\n題");
        assert_eq!(app.rename.as_ref().unwrap().buffer.text(), "Paper x 新 題");
        app.cancel_rename();
        assert!(app.rename.is_none());
    }

    #[test]
    fn membership_choices_keep_each_child_after_its_sorted_root() {
        let mut app = App {
            items: vec![item("x")],
            groups: vec![
                group("z", "Zeta", None),
                group("child", "Child", Some("a")),
                group("a", "Alpha", None),
            ],
            ..Default::default()
        };

        app.begin_membership();

        assert_eq!(
            app.membership.as_ref().unwrap().choices,
            vec![
                ("a".into(), "Alpha".into()),
                ("child".into(), "  Alpha/Child".into()),
                ("z".into(), "Zeta".into()),
            ]
        );
    }

    #[test]
    fn membership_draft_checks_legacy_child_parent_without_mutating_item() {
        let mut paper = item("x");
        paper.groups = vec![serde_json::json!({"id": "child", "name": "Child"})];
        let mut app = App {
            items: vec![paper],
            groups: vec![
                group("root", "Root", None),
                group("child", "Child", Some("root")),
            ],
            ..Default::default()
        };

        app.begin_membership();

        assert_eq!(app.membership.as_ref().unwrap().checked, vec![true, true]);
    }

    #[test]
    fn membership_toggle_cascades_only_in_the_required_direction() {
        let mut app = App {
            items: vec![item("x")],
            groups: vec![
                group("root", "Root", None),
                group("child", "Child", Some("root")),
            ],
            ..Default::default()
        };
        app.begin_membership();

        // Selecting a child checks its parent.
        app.membership_key(crossterm::event::KeyCode::Down);
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        assert_eq!(app.membership.as_ref().unwrap().checked, vec![true, true]);

        // Clearing the parent clears children; selecting the parent does not
        // select them again.
        app.membership_key(crossterm::event::KeyCode::Up);
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        assert_eq!(app.membership.as_ref().unwrap().checked, vec![false, false]);
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        assert_eq!(app.membership.as_ref().unwrap().checked, vec![true, false]);

        // Clearing a child leaves its parent checked.
        app.membership_key(crossterm::event::KeyCode::Down);
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        assert_eq!(app.membership.as_ref().unwrap().checked, vec![true, false]);
    }

    #[test]
    fn membership_parent_clear_does_not_touch_sibling_or_other_root() {
        let mut app = App {
            items: vec![item("x")],
            groups: vec![
                group("root", "Root", None),
                group("child-a", "A", Some("root")),
                group("child-b", "B", Some("root")),
                group("other", "Other", None),
                group("other-child", "A", Some("other")),
            ],
            ..Default::default()
        };
        app.begin_membership();
        let root = app
            .membership
            .as_ref()
            .unwrap()
            .choices
            .iter()
            .position(|(id, _)| id == "root")
            .unwrap();
        let child_a = app
            .membership
            .as_ref()
            .unwrap()
            .choices
            .iter()
            .position(|(id, _)| id == "child-a")
            .unwrap();
        let child_b = app
            .membership
            .as_ref()
            .unwrap()
            .choices
            .iter()
            .position(|(id, _)| id == "child-b")
            .unwrap();
        let other_child = app
            .membership
            .as_ref()
            .unwrap()
            .choices
            .iter()
            .position(|(id, _)| id == "other-child")
            .unwrap();
        app.membership.as_mut().unwrap().selected = child_a;
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        app.membership.as_mut().unwrap().selected = child_b;
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        app.membership.as_mut().unwrap().selected = other_child;
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        app.membership.as_mut().unwrap().selected = root;
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        let checked = &app.membership.as_ref().unwrap().checked;
        assert!(!checked[child_a] && !checked[child_b] && !checked[root]);
        assert!(checked[other_child]);
    }

    #[test]
    fn membership_draft_and_cancel_do_not_mutate_the_item() {
        let mut paper = item("x");
        paper.groups = vec![serde_json::json!({"id": "root", "name": "Root"})];
        let original_groups = paper.groups.clone();
        let mut app = App {
            items: vec![paper],
            groups: vec![group("root", "Root", None)],
            ..Default::default()
        };

        app.begin_membership();
        app.membership_key(crossterm::event::KeyCode::Char(' '));
        assert_eq!(app.items[0].groups, original_groups);
        app.cancel_membership();

        assert!(app.membership.is_none());
        assert_eq!(app.items[0].groups, original_groups);
    }
}
