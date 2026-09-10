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
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(4), Constraint::Length(2)])
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
        metadata(item)
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
    frame.render_widget(Paragraph::new(Line::from(vec![Span::styled(status, Style::default().fg(Color::Cyan)), Span::raw("   ↑↓/jk move  u/c/r status  1-4 filter  g group  / search  f refresh  ? help  q quit")])).block(Block::default().borders(Borders::TOP)), chunks[1]);
}
fn list_state(app: &App) -> ratatui::widgets::ListState {
    let mut state = ratatui::widgets::ListState::default();
    if !app.items.is_empty() {
        state.select(Some(app.selected));
    }
    state
}
fn metadata(item: &Item) -> Paragraph<'static> {
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
                v.get("scheme")?.as_str()?,
                v.get("value")?.as_str()?
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
    use super::sanitize;
    #[test]
    fn removes_control_chars() {
        assert_eq!(sanitize("a\u{1b}[31mb\n"), "a[31mb\n");
    }
}
