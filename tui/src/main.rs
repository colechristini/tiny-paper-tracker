use crossterm::{
    cursor::Show,
    event::{
        self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers,
    },
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use lit_tui::completion;
use lit_tui::{bridge::Bridge, state::App, ui};
use ratatui::{Terminal, backend::CrosstermBackend, layout::Rect};
use std::io;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut bridge = Bridge::spawn()?;
    enable_raw_mode()?;
    let _terminal_guard = TerminalGuard;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableBracketedPaste)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    let result = run(&mut bridge, &mut terminal);
    result.map_err(Into::into)
}

struct TerminalGuard;
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let mut stdout = io::stdout();
        let _ = execute!(stdout, Show, DisableBracketedPaste, LeaveAlternateScreen);
    }
}

fn run(
    bridge: &mut Bridge,
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
) -> io::Result<()> {
    let mut app = App::default();
    app.refresh(bridge);
    let mut redraw = true;
    while !app.should_quit {
        if redraw {
            terminal.draw(|f| ui::draw(f, &app))?;
            redraw = false;
        }
        if event::poll(std::time::Duration::from_millis(250))?
            && let event = event::read()?
        {
            match event {
                Event::Key(key) => {
                    let size = terminal.size()?;
                    handle_key(
                        &mut app,
                        bridge,
                        key,
                        Rect {
                            x: 0,
                            y: 0,
                            width: size.width,
                            height: size.height,
                        },
                    );
                    redraw = true;
                }
                Event::Paste(text) => {
                    if app.editor.as_ref().is_some_and(|e| !e.preview) {
                        app.insert_editor_text(&text);
                        if let Some(editor) = app.editor.as_mut() {
                            update_completion(editor, bridge);
                        }
                    }
                    redraw = true;
                }
                Event::Resize(_, _) => redraw = true,
                _ => {}
            }
        }
        if app.autosave_due() {
            let _ = app.save_editor(bridge);
            redraw = true;
        }
    }
    Ok(())
}
fn handle_key(app: &mut App, bridge: &mut Bridge, key: KeyEvent, area: Rect) {
    if key.kind == KeyEventKind::Release {
        return;
    }
    if app.editor.is_some() {
        handle_editor_key(app, bridge, key, area);
        return;
    }
    if app.help {
        if matches!(key.code, KeyCode::Char('?') | KeyCode::Esc) {
            app.help = false;
        }
        return;
    }
    if app.searching {
        match key.code {
            KeyCode::Esc => app.searching = false,
            KeyCode::Enter => {
                app.query = app.search_input.clone();
                app.searching = false;
                app.refresh(bridge);
            }
            KeyCode::Backspace => {
                app.search_input.pop();
            }
            KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                app.search_input.push(c)
            }
            _ => {}
        }
        return;
    }
    match key.code {
        KeyCode::Enter => app.open_editor(bridge),
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,
        KeyCode::Up | KeyCode::Char('k') => app.move_selection(-1),
        KeyCode::Down | KeyCode::Char('j') => app.move_selection(1),
        KeyCode::PageUp => app.metadata_scroll = app.metadata_scroll.saturating_sub(8),
        KeyCode::PageDown => app.metadata_scroll = app.metadata_scroll.saturating_add(8),
        KeyCode::Char('u') => app.set_status(bridge, "unread"),
        KeyCode::Char('c') => app.set_status(bridge, "reading"),
        KeyCode::Char('r') => app.set_status(bridge, "read"),
        KeyCode::Char('1') => {
            app.filter = lit_tui::state::StatusFilter::All;
            app.refresh(bridge);
        }
        KeyCode::Char('2') => {
            app.filter = lit_tui::state::StatusFilter::Unread;
            app.refresh(bridge);
        }
        KeyCode::Char('3') => {
            app.filter = lit_tui::state::StatusFilter::Reading;
            app.refresh(bridge);
        }
        KeyCode::Char('4') => {
            app.filter = lit_tui::state::StatusFilter::Read;
            app.refresh(bridge);
        }
        KeyCode::Char('g') => {
            app.cycle_group(1);
            app.refresh(bridge);
        }
        KeyCode::Char('h') => {
            app.cycle_group(-1);
            app.refresh(bridge);
        }
        KeyCode::Delete | KeyCode::Backspace => app.delete_selected(bridge),
        KeyCode::Char('/') => {
            app.searching = true;
            app.search_input = app.query.clone();
        }
        KeyCode::Char('?') => app.help = true,
        KeyCode::Char('f') => app.refresh(bridge),
        _ => {}
    }
}

fn handle_editor_key(app: &mut App, bridge: &mut Bridge, key: KeyEvent, area: Rect) {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    if ctrl && key.code == KeyCode::Char('v') {
        if let Some(editor) = app.editor.as_mut() {
            editor.completion = None;
            editor.toggle_preview();
        }
        return;
    }
    if ctrl && matches!(key.code, KeyCode::Char('s') | KeyCode::Char('r')) {
        let _ = app.save_editor(bridge);
        return;
    }
    if ctrl && key.code == KeyCode::Char('e') {
        app.recover_editor(bridge);
        return;
    }
    if ctrl && key.code == KeyCode::Char('q') {
        if app.save_editor(bridge).is_ok() {
            app.should_quit = true;
        }
        return;
    }
    if key.code == KeyCode::Esc
        && app
            .editor
            .as_ref()
            .is_some_and(|e| !e.preview && e.completion.is_some())
    {
        if let Some(editor) = app.editor.as_mut() {
            editor.completion = None;
        }
        return;
    }
    if key.code == KeyCode::Esc {
        if app.save_editor(bridge).is_ok() {
            app.editor = None;
            app.error = None;
            app.refresh(bridge);
        }
        return;
    }
    let Some(editor) = app.editor.as_mut() else {
        return;
    };
    if editor.preview {
        let width = (area.width * 62 / 100).saturating_sub(2);
        let height = area
            .height
            .saturating_sub(4)
            .saturating_sub(5)
            .saturating_sub(2);
        let max = editor
            .rendered
            .as_ref()
            .map(|text| wrapped_lines(text, width).saturating_sub(height as usize) as u16)
            .unwrap_or(0);
        match key.code {
            KeyCode::Up => editor.preview_scroll = editor.preview_scroll.saturating_sub(1),
            KeyCode::Down => {
                editor.preview_scroll = editor.preview_scroll.saturating_add(1).min(max)
            }
            KeyCode::PageUp => editor.preview_scroll = editor.preview_scroll.saturating_sub(10),
            KeyCode::PageDown => {
                editor.preview_scroll = editor.preview_scroll.saturating_add(10).min(max)
            }
            KeyCode::Home => editor.preview_scroll = 0,
            KeyCode::End => editor.preview_scroll = max,
            _ => {}
        }
        return;
    }
    if let Some(completion) = editor.completion.as_mut() {
        match key.code {
            KeyCode::Esc => {
                editor.completion = None;
                return;
            }
            KeyCode::Up | KeyCode::Char('p') if key.code == KeyCode::Up || ctrl => {
                completion.selected = completion.selected.saturating_sub(1);
                return;
            }
            KeyCode::Down | KeyCode::Char('n') if key.code == KeyCode::Down || ctrl => {
                if !completion.candidates.is_empty() {
                    completion.selected =
                        (completion.selected + 1).min(completion.candidates.len() - 1);
                }
                return;
            }
            KeyCode::Tab => {
                let current = completion::extract(&editor.buffer);
                if current.as_ref().map(|(s, e, q)| (*s, *e, q))
                    != Some((completion.start, completion.end, &completion.query))
                {
                    editor.completion = None;
                    return;
                }
                if let Some(target) = completion.candidates.get(completion.selected).cloned() {
                    let id = editor.item_id.clone();
                    match bridge.note_link(&id, &target.id) {
                        Ok(link) => {
                            editor
                                .buffer
                                .replace_range(completion.start, completion.end, &link);
                            editor.mark_changed();
                            editor.completion = None;
                        }
                        Err(e) => app.error = Some(format!("Link failed: {e}. Text unchanged.")),
                    }
                }
                return;
            }
            _ => {}
        }
    }
    let mut changed = false;
    let mut moved = false;
    match key.code {
        KeyCode::Char(c) if !ctrl => {
            editor.buffer.insert(&c.to_string());
            changed = true;
        }
        KeyCode::Enter => {
            editor.buffer.insert("\n");
            changed = true;
        }
        KeyCode::Backspace => {
            let before = editor.buffer.text();
            editor.buffer.backspace();
            changed = before != editor.buffer.text();
        }
        KeyCode::Delete => {
            let before = editor.buffer.text();
            editor.buffer.delete();
            changed = before != editor.buffer.text();
        }
        KeyCode::Left => {
            editor.buffer.left();
            moved = true;
        }
        KeyCode::Right => {
            editor.buffer.right();
            moved = true;
        }
        KeyCode::Up => {
            editor.buffer.up();
            moved = true;
        }
        KeyCode::Down => {
            editor.buffer.down();
            moved = true;
        }
        KeyCode::Home => {
            editor.buffer.home();
            moved = true;
        }
        KeyCode::End => {
            editor.buffer.end();
            moved = true;
        }
        KeyCode::PageUp => {
            editor.buffer.page_up(10);
            editor.scroll = editor.scroll.saturating_sub(10);
            moved = true;
        }
        KeyCode::PageDown => {
            editor.buffer.page_down(10);
            editor.scroll = editor.scroll.saturating_add(10);
            moved = true;
        }
        _ => {}
    }
    if changed {
        editor.mark_changed();
        update_completion(editor, bridge);
    } else if moved {
        update_completion(editor, bridge);
    }
}

fn update_completion(editor: &mut lit_tui::state::EditorState, bridge: &mut Bridge) {
    let Some((start, end, query)) = completion::extract(&editor.buffer) else {
        editor.completion = None;
        return;
    };
    if query.is_empty() {
        editor.completion = Some(completion::Completion {
            start,
            end,
            query,
            candidates: vec![],
            selected: 0,
        });
        return;
    }
    if editor
        .completion
        .as_ref()
        .is_some_and(|c| c.start == start && c.end == end && c.query == query)
    {
        return;
    }
    match bridge.link_search(&query) {
        Ok(candidates) => {
            editor.completion = Some(completion::Completion {
                start,
                end,
                query,
                candidates,
                selected: 0,
            })
        }
        Err(_) => editor.completion = None,
    }
}

fn wrapped_lines(text: &ratatui::text::Text<'static>, width: u16) -> usize {
    ratatui::widgets::Paragraph::new(text.clone())
        .wrap(ratatui::widgets::Wrap { trim: true })
        .line_count(width.max(1))
}

#[cfg(test)]
mod tests {
    use super::wrapped_lines;
    use ratatui::text::Text;
    #[test]
    fn wrapped_preview_scroll_counts_long_paragraphs() {
        let text = Text::raw("x".repeat(2000));
        assert!(wrapped_lines(&text, 38) > 50);
        assert_eq!(wrapped_lines(&Text::raw("short"), 38), 1);
        assert_eq!(wrapped_lines(&Text::raw("123456 123456 123456"), 10), 3);
    }
}
