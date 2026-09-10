use crate::{
    model::{Item, group_name},
    state::{App, StatusFilter},
};
use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, List, ListItem, Paragraph, Wrap},
};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub fn draw(frame: &mut Frame, app: &App) {
    if frame.area().width < 60 || frame.area().height < 12 {
        frame.render_widget(
            Paragraph::new("Terminal too small for lit-tui. Resize to at least 60x12.")
                .block(Block::default().borders(Borders::ALL)),
            frame.area(),
        );
        return;
    }
    if app.help {
        let help = "lit-tui stage 1\n\n↑/↓ or j/k   move selection\nu              mark unread\nc              mark currently reading\nr              mark read\n1-4            status filter\ng / h          next / previous group\nF2 / R         rename selected title\nDelete/Backspace delete selected item\n/              search titles\nf              refresh\nq or Esc       quit / close help\n\nPress ? or Esc to close this help.";
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
        .constraints([Constraint::Min(4), Constraint::Length(4)])
        .split(frame.area());
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(48), Constraint::Percentage(52)])
        .split(chunks[0]);
    let list_items = list_rows(app, false);
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
        format!("Search: {}", sanitize(&app.search_input))
    } else {
        let query_label = if app.query.is_empty() {
            "no search".to_string()
        } else {
            sanitize(&app.query)
        };
        format!(
            "{} · {} · {}",
            filter_label(app.filter),
            sanitize(group),
            query_label,
        )
    };
    let footer_rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(2), Constraint::Min(1)])
        .split(chunks[1]);
    frame.render_widget(
        Paragraph::new(status).block(Block::default().borders(Borders::TOP)),
        footer_rows[0],
    );
    frame.render_widget(
        hint_footer(
            String::new(),
            &[
                ("↑↓/jk", " move"),
                ("Enter", " edit"),
                ("F2/R", " rename"),
                ("u/c/r", " status"),
                ("1-4", " filter"),
                ("g/h", " group"),
                ("/", " search"),
                ("f", " refresh"),
                ("?", " help"),
                ("Del", " delete"),
                ("q", " quit"),
            ],
        ),
        footer_rows[1],
    );
    if let Some(rename) = &app.rename {
        let area = centered_rect(70, 5, frame.area());
        frame.render_widget(Clear, area);
        let max = area.width.saturating_sub(4) as usize;
        frame.render_widget(
            Paragraph::new(rename_input(&rename.buffer, max)).block(
                Block::default()
                    .title(" Rename title (Enter save, Esc cancel) ")
                    .borders(Borders::ALL),
            ),
            area,
        );
    }
}

fn rename_input(buffer: &crate::editor::TextBuffer, max_width: usize) -> String {
    let mut tokens = buffer
        .text()
        .chars()
        .map(|c| c.to_string())
        .collect::<Vec<_>>();
    let cursor = buffer.cursor.min(tokens.len());
    tokens.insert(cursor, "▌".into());
    let width = |text: &str| text.chars().map(|c| c.width().unwrap_or(0)).sum::<usize>();
    let mut start = 0;
    let mut end = tokens.len();
    while start < end && tokens[start..end].iter().map(|s| width(s)).sum::<usize>() > max_width {
        if start < cursor {
            start += 1;
        } else {
            end -= 1;
        }
    }
    tokens[start..end].concat()
}

fn centered_rect(width_percent: u16, height: u16, area: Rect) -> Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length((area.height.saturating_sub(height)) / 2),
            Constraint::Length(height),
            Constraint::Min(0),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - width_percent) / 2),
            Constraint::Percentage(width_percent),
            Constraint::Min(0),
        ])
        .split(vertical[1])[1]
}

fn hint_footer<'a>(status: String, hints: &[(&'a str, &'a str)]) -> Paragraph<'a> {
    let mut spans = vec![Span::styled(status, Style::default().fg(Color::Cyan))];
    for (key, description) in hints {
        spans.push(Span::raw("   "));
        spans.push(Span::styled(
            *key,
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ));
        spans.push(Span::raw(*description));
    }
    Paragraph::new(Line::from(spans)).wrap(Wrap { trim: true })
}

fn error_or_hints<'a>(error: Option<&str>, hints: &[(&'a str, &'a str)]) -> Paragraph<'a> {
    match error {
        Some(message) => Paragraph::new(sanitize(message)).wrap(Wrap { trim: true }),
        None => hint_footer(String::new(), hints),
    }
}
fn draw_editor(frame: &mut Frame, app: &App) {
    let editor = app.editor.as_ref().expect("editor checked by caller");
    let area = frame.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(3), Constraint::Length(4)])
        .split(area);
    let panes = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(chunks[0]);
    let list_items = list_rows(app, true);
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
        .constraints([Constraint::Length(5), Constraint::Min(1)])
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
    let (line, _) = editor.buffer.line_col();
    let visible_height = editor_area[1].height.saturating_sub(2) as usize;
    let vscroll = editor
        .scroll
        .min(line as u16)
        .max(line.saturating_sub(visible_height.saturating_sub(1)) as u16);
    if editor.preview {
        let vscroll = editor.preview_scroll;
        let preview = editor
            .rendered
            .as_ref()
            .expect("preview is rendered on entry");
        let paragraph = Paragraph::new(preview.clone())
            .scroll((vscroll, 0))
            .wrap(Wrap { trim: false })
            .block(
                Block::default()
                    .title(" Preview (Ctrl-V to edit) ")
                    .borders(Borders::ALL),
            );
        frame.render_widget(paragraph, editor_area[1]);
        frame.render_widget(
            error_or_hints(
                app.error.as_deref(),
                &[
                    ("Ctrl-V", " preview/edit"),
                    ("Ctrl-S", " save"),
                    ("Esc", " save and return"),
                    ("Ctrl-E", " recovery"),
                    ("Ctrl-Q", " save+quit"),
                    ("arrows/PageUp/PageDown", " scroll"),
                ],
            )
            .block(Block::default().borders(Borders::TOP)),
            chunks[1],
        );
        return;
    }
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
    if let Some(completion) = &editor.completion {
        let entries = if completion.candidates.is_empty() {
            vec![Line::from(if completion.query.is_empty() {
                "Type a title…"
            } else {
                "No matching titles"
            })]
        } else {
            let max_rows = editor_area[1].height.saturating_sub(3).max(1) as usize;
            let offset = completion
                .selected
                .saturating_sub(max_rows.saturating_sub(1));
            completion.candidates[offset..completion.candidates.len().min(offset + max_rows)]
                .iter()
                .enumerate()
                .map(|(i, c)| {
                    let actual = i + offset;
                    Line::from(format!(
                        "{}{}",
                        if actual == completion.selected {
                            "› "
                        } else {
                            "  "
                        },
                        sanitize(&c.title)
                    ))
                })
                .collect()
        };
        let popup_height =
            (entries.len() as u16 + 2).min(editor_area[1].height.saturating_sub(1).max(1));
        let popup = Rect {
            x: editor_area[1].x + 1,
            y: editor_area[1].y + 1,
            width: editor_area[1].width.saturating_sub(2),
            height: popup_height,
        };
        frame.render_widget(
            Paragraph::new(entries)
                .block(
                    Block::default()
                        .title(" Links · Tab insert · Esc cancel ")
                        .borders(Borders::ALL),
                )
                .style(Style::default().bg(Color::Black)),
            popup,
        );
    }
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
    frame.render_widget(
        error_or_hints(
            app.error.as_deref(),
            &[
                ("Ctrl-V", " preview"),
                ("Ctrl-S", " save"),
                ("Esc", " save and return"),
                ("Ctrl-Q", " save and quit"),
                ("Ctrl-E", " recovery copy"),
                ("arrows/Home/End/PageUp/PageDown", " edit"),
            ],
        )
        .block(Block::default().borders(Borders::TOP)),
        chunks[1],
    );
}
fn list_state(app: &App) -> ratatui::widgets::ListState {
    let mut state = ratatui::widgets::ListState::default();
    if matches!(
        app.display_rows.get(app.selected),
        Some(crate::state::DisplayRow::Item(_))
    ) {
        state.select(Some(app.selected));
    }
    state
}

fn list_rows(app: &App, editor: bool) -> Vec<ListItem<'static>> {
    app.display_rows
        .iter()
        .map(|row| match row {
            crate::state::DisplayRow::Header(id) => {
                let section = app.sections.iter().find(|s| s.id == *id);
                let title = section
                    .map(|s| sanitize(&s.title))
                    .unwrap_or_else(|| "Reading".into());
                let count = section.map(|s| s.item_ids.len()).unwrap_or(app.items.len());
                ListItem::new(Line::from(Span::styled(
                    format!("── {} ({count}) ──", title),
                    Style::default()
                        .fg(Color::Cyan)
                        .add_modifier(Modifier::BOLD),
                )))
            }
            crate::state::DisplayRow::Empty => ListItem::new(Span::styled(
                "(empty)",
                Style::default().fg(Color::DarkGray),
            )),
            crate::state::DisplayRow::Item(index) => {
                let item = &app.items[*index];
                if editor {
                    ListItem::new(sanitize(&item.title))
                } else {
                    let marker = match item.status.as_str() {
                        "read" => "✓",
                        "reading" => "▸",
                        _ => "·",
                    };
                    ListItem::new(Line::from(vec![
                        Span::styled(format!("{marker} "), Style::default().fg(Color::DarkGray)),
                        Span::raw(sanitize(&item.title)),
                    ]))
                }
            }
        })
        .collect()
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
    use super::{draw, rename_input, sanitize};
    use crate::{
        editor::TextBuffer,
        model::Group,
        model::{Item, Section},
        state::DisplayRow,
        state::{App, EditorState},
    };
    use ratatui::{
        Terminal,
        backend::TestBackend,
        style::{Color, Modifier},
    };
    use unicode_width::UnicodeWidthStr;
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
                created_at: Some("2026-09-10T08:00:00Z".into()),
                modified_at: "2026-09-10T09:30:00Z".into(),
                original: String::new(),
                dirty_since: None,
                scroll: 0,
                preview: false,
                rendered: None,
                preview_scroll: 0,
                completion: None,
            }),
            ..Default::default()
        };
        let backend = TestBackend::new(80, 16);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let rendered = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(rendered.contains("Created: 2026-09-10T08:00:00Z"));
        assert!(rendered.contains("Edited: 2026-09-10T09:30:00Z"));
    }

    #[test]
    fn list_footer_keeps_status_and_styled_hints_visible_at_long_group_name() {
        let app = App {
            groups: vec![Group {
                id: "g".into(),
                name: "x".repeat(500),
                parent_id: None,
            }],
            group: Some("g".into()),
            ..Default::default()
        };
        let backend = TestBackend::new(80, 16);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let buffer = terminal.backend().buffer();
        let text = buffer
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(text.contains("all · "));
        assert!(text.contains("g/h"));
        assert!(text.contains("Del delete"));
        let key = buffer
            .content()
            .iter()
            .find(|cell| cell.symbol() == "g" && cell.fg == Color::Yellow)
            .unwrap();
        assert_eq!(key.fg, Color::Yellow);
        assert!(key.modifier.contains(Modifier::BOLD));
        let description = buffer
            .content()
            .iter()
            .find(|cell| cell.symbol() == "s" && !cell.modifier.contains(Modifier::BOLD))
            .unwrap();
        assert!(!description.modifier.contains(Modifier::BOLD));

        let error_app = App {
            error: Some("visible error".into()),
            ..Default::default()
        };
        let backend = TestBackend::new(80, 16);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, &error_app)).unwrap();
        let text = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(text.contains("Error: visible error"));
    }

    #[test]
    fn stacked_sections_render_headers_counts_and_empty_marker() {
        let item = Item {
            id: "loose".into(),
            title: "Loose paper".into(),
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
        };
        let app = App {
            items: vec![item],
            sections: vec![
                Section {
                    id: None,
                    title: String::new(),
                    item_ids: vec!["loose".into()],
                },
                Section {
                    id: Some("child".into()),
                    title: "Child".into(),
                    item_ids: vec![],
                },
            ],
            display_rows: vec![
                DisplayRow::Item(0),
                DisplayRow::Header(Some("child".into())),
                DisplayRow::Empty,
            ],
            ..Default::default()
        };
        let backend = TestBackend::new(80, 16);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(text.contains("Loose paper"));
        assert!(text.contains("Child (0)"));
        assert!(text.contains("(empty)"));
        assert!(!text.contains("General"));
    }

    #[test]
    fn rename_input_handles_unicode_carets_and_wide_clipping() {
        let mut buffer = TextBuffer::new("世界é".into());
        buffer.cursor = 1;
        assert_eq!(rename_input(&buffer, 20), "世▌界é");
        buffer.end();
        assert_eq!(rename_input(&buffer, 20), "世界é▌");
        let long = TextBuffer::new("世界世界世界世界".into());
        let rendered = rename_input(&long, 8);
        assert!(rendered.contains('▌'));
        assert!(rendered.width() <= 8);
    }
}
