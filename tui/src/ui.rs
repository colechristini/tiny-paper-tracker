use crate::{
    model::{Item, group_name},
    state::{App, StatusFilter},
};
use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, List, ListItem, Paragraph, Wrap},
};
use unicode_width::UnicodeWidthStr;

pub fn draw(frame: &mut Frame, app: &App) {
    if app.help {
        let help = "lit-tui stage 1\n\n↑/↓ or j/k   move selection\nu              mark unread\nc              mark currently reading\nr              mark read\n1-4            status filter\ng              cycle groups\n/              search titles\nf              refresh\nq or Esc       quit / close help\n\nPress ? or Esc to close this help.";
        frame.render_widget(
            Paragraph::new(help)
                .block(Block::default().title(" Help ").borders(Borders::ALL))
                .wrap(Wrap { trim: true }),
            frame.area(),
        );
        return;
    }
    if app.editor.is_some() {
        draw_editor(frame, app);
        return;
    }
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(4), Constraint::Length(3)])
        .split(frame.area());
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(48), Constraint::Percentage(52)])
        .split(chunks[0]);
    let list_items = app
        .items
        .iter()
        .map(|item| {
            let marker = match item.status.as_str() {
                "read" => "✓",
                "reading" => "▸",
                _ => "·",
            };
            ListItem::new(Line::from(vec![
                Span::styled(format!("{marker} "), Style::default().fg(Color::DarkGray)),
                Span::raw(sanitize(&item.title)),
            ]))
        })
        .collect::<Vec<_>>();
    let list = List::new(list_items)
        .block(
            Block::default()
                .title(format!(" Reading ({}) ", app.items.len()))
                .borders(Borders::ALL),
        )
        .highlight_style(Style::default().add_modifier(Modifier::REVERSED));
    frame.render_stateful_widget(list, body[0], &mut list_state(app));
    let detail = if let Some(item) = app.selected_item() {
        metadata(item, app.metadata_scroll)
    } else if let Some(error) = &app.error {
        Paragraph::new(sanitize(error))
            .block(Block::default().title(" Error ").borders(Borders::ALL))
            .wrap(Wrap { trim: true })
    } else {
        Paragraph::new("No items match the current filters.")
            .block(Block::default().title(" Details ").borders(Borders::ALL))
    };
    frame.render_widget(detail, body[1]);
    let group = app
        .group
        .as_deref()
        .and_then(|id| app.groups.iter().find(|g| g.id == id))
        .map(|g| g.name.as_str())
        .unwrap_or("all groups");
    let status = if let Some(error) = &app.error {
        format!("Error: {}", sanitize(error))
    } else if app.searching {
        format!("Search: {}", app.search_input)
    } else {
        format!(
            "{} · {} · {}",
            filter_label(app.filter),
            group,
            if app.query.is_empty() {
                "no search"
            } else {
                app.query.as_str()
            }
        )
    };
    frame.render_widget(Paragraph::new(Line::from(vec![Span::styled(status, Style::default().fg(Color::Cyan)), Span::raw("   ↑↓/jk move  Enter edit  u/c/r status  1-4 filter  g group  / search  f refresh  ? help  q quit")])).block(Block::default().borders(Borders::TOP)), chunks[1]);
}
fn draw_editor(frame: &mut Frame, app: &App) {
    let editor = app.editor.as_ref().expect("editor checked by caller");
    let area = frame.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(3), Constraint::Length(3)])
        .split(area);
    let panes = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(chunks[0]);
    let list_items = app
        .items
        .iter()
        .map(|item| ListItem::new(sanitize(&item.title)))
        .collect::<Vec<_>>();
    let list = List::new(list_items)
        .block(
            Block::default()
                .title(format!(" Reading ({}) ", app.items.len()))
                .borders(Borders::ALL),
        )
        .highlight_style(Style::default().add_modifier(Modifier::REVERSED));
    frame.render_stateful_widget(list, panes[0], &mut list_state(app));
    let editor_area = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(4), Constraint::Min(1)])
        .split(panes[1]);
    let header = format!(
        "{}\nCreated: {}\nEdited: {}{}",
        sanitize(&editor.path),
        editor.created_at.as_deref().unwrap_or("created unknown"),
        editor.modified_at,
        if editor.dirty() { " · unsaved" } else { "" }
    );
    frame.render_widget(
        Paragraph::new(header).block(Block::default().title(" Note ").borders(Borders::ALL)),
        editor_area[0],
    );
    let text = editor.buffer.text();
    let lines = text
        .split('\n')
        .map(sanitize)
        .collect::<Vec<_>>()
        .join("\n");
    let inner_width = editor_area[1].width.saturating_sub(2) as usize;
    let visual_col = editor.buffer.line_prefix().width();
    let hscroll = visual_col.saturating_sub(inner_width.saturating_sub(1));
    frame.render_widget(
        Paragraph::new(lines)
            .scroll((editor.scroll, hscroll as u16))
            .block(Block::default().borders(Borders::ALL)),
        editor_area[1],
    );
    let (line, _) = editor.buffer.line_col();
    let visible_height = editor_area[1].height.saturating_sub(2) as usize;
    let vscroll = editor
        .scroll
        .max(line.saturating_sub(visible_height.saturating_sub(1)) as u16);
    let cursor_y = editor_area[1]
        .y
        .saturating_add(1)
        .saturating_add(line as u16)
        .saturating_sub(vscroll);
    if cursor_y < editor_area[1].bottom().saturating_sub(1) && editor_area[1].width > 2 {
        frame.set_cursor_position((
            editor_area[1].x + 1 + visual_col.saturating_sub(hscroll) as u16,
            cursor_y,
        ));
    }
    let footer = app.error.as_deref().map(sanitize).unwrap_or_else(|| "Ctrl-S save · Esc save and return · Ctrl-Q save and quit · Ctrl-E recovery copy · arrows/Home/End/PageUp/PageDown edit".to_string());
    frame.render_widget(
        Paragraph::new(footer).block(Block::default().borders(Borders::TOP)),
        chunks[1],
    );
}
fn list_state(app: &App) -> ratatui::widgets::ListState {
    let mut state = ratatui::widgets::ListState::default();
    if !app.items.is_empty() {
        state.select(Some(app.selected));
    }
    state
}
fn metadata(item: &Item, scroll: u16) -> Paragraph<'static> {
    let authors = if item.authors.is_empty() {
        "".into()
    } else {
        item.authors.join(", ")
    };
    let groups = item
        .groups
        .iter()
        .map(group_name)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(", ");
    let abstract_text = item
        .metadata
        .get("abstract")
        .or_else(|| item.metadata.get("summary"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let identifiers = item
        .identifiers
        .iter()
        .filter_map(|v| {
            Some(format!(
                "{}: {}",
                v.get("scheme")
                    .and_then(|x| x.as_str())
                    .or_else(|| v.get(0)?.as_str())?,
                v.get("value")
                    .and_then(|x| x.as_str())
                    .or_else(|| v.get(1)?.as_str())?
            ))
        })
        .collect::<Vec<_>>()
        .join(", ");
    let text = vec![
        Line::from(Span::styled(
            sanitize(&item.title),
            Style::default().add_modifier(Modifier::BOLD),
        )),
        Line::from(sanitize(&authors)),
        Line::from(format!("Status: {}", sanitize(&item.status))),
        Line::from(format!("Kind: {}", sanitize(&item.kind))),
        Line::from(format!(
            "Venue: {}",
            sanitize(item.venue.as_deref().unwrap_or(""))
        )),
        Line::from(format!(
            "Published: {}",
            sanitize(item.published_at.as_deref().unwrap_or(""))
        )),
        Line::from(format!(
            "Added: {}",
            sanitize(item.added_at.as_deref().unwrap_or(""))
        )),
        Line::from(format!(
            "Read: {}",
            sanitize(item.read_at.as_deref().unwrap_or(""))
        )),
        Line::from(format!("Groups: {}", sanitize(&groups))),
        Line::from(format!("Tags: {}", sanitize(&item.tags.join(", ")))),
        Line::from(format!("Identifiers: {}", sanitize(&identifiers))),
        Line::from(format!(
            "Note: {}",
            sanitize(item.note_path.as_deref().unwrap_or(""))
        )),
        Line::from(""),
        Line::from(sanitize(abstract_text)),
        Line::from(""),
        Line::from(sanitize(&item.url)),
    ];
    Paragraph::new(text)
        .scroll((scroll, 0))
        .block(Block::default().title(" Metadata ").borders(Borders::ALL))
        .wrap(Wrap { trim: true })
}
fn filter_label(filter: StatusFilter) -> &'static str {
    match filter {
        StatusFilter::All => "all",
        StatusFilter::Unread => "unread",
        StatusFilter::Reading => "reading",
        StatusFilter::Read => "read",
    }
}
pub fn sanitize(value: &str) -> String {
    value
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{draw, sanitize};
    use crate::{
        editor::TextBuffer,
        state::{App, EditorState},
    };
    use ratatui::{Terminal, backend::TestBackend};
    #[test]
    fn removes_control_chars() {
        assert_eq!(sanitize("a\u{1b}[31mb\n"), "a[31mb\n");
    }
    #[test]
    fn tiny_editor_render_handles_unicode_and_metadata() {
        let app = App {
            error: Some("save failed: conflict; Ctrl-E recovery".into()),
            editor: Some(EditorState {
                item_id: "x".into(),
                buffer: TextBuffer::new("世界\nlong line 🙂\n".into()),
                revision: "r".into(),
                path: "note.md".into(),
                created_at: Some("Created".into()),
                modified_at: "Edited".into(),
                original: String::new(),
                dirty_since: None,
                scroll: 0,
            }),
            ..Default::default()
        };
        let backend = TestBackend::new(30, 10);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
    }
}
