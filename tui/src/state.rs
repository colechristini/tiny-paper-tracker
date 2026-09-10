use crate::{
    bridge::Bridge,
    model::{Group, Item},
};

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
