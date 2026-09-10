use crossterm::{
    cursor::Show,
    event::{
        self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers,
    },
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use lit_tui::{bridge::Bridge, state::App, ui};
use ratatui::{Terminal, backend::CrosstermBackend};
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
                    handle_key(&mut app, bridge, key);
                    redraw = true;
                }
                Event::Paste(text) => {
                    if app.editor.as_ref().is_some_and(|e| !e.preview) { app.insert_editor_text(&text); }
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
fn handle_key(app: &mut App, bridge: &mut Bridge, key: KeyEvent) {
    if key.kind == KeyEventKind::Release {
        return;
    }
    if app.editor.is_some() {
        handle_editor_key(app, bridge, key);
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
            app.cycle_group();
            app.refresh(bridge);
        }
        KeyCode::Char('/') => {
            app.searching = true;
            app.search_input = app.query.clone();
        }
        KeyCode::Char('?') => app.help = true,
        KeyCode::Char('f') => app.refresh(bridge),
        _ => {}
    }
}

fn handle_editor_key(app: &mut App, bridge: &mut Bridge, key: KeyEvent) {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    if ctrl && key.code == KeyCode::Char('v') { if let Some(editor) = app.editor.as_mut() { editor.toggle_preview(); } return; }
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
    if key.code == KeyCode::Esc {
        if app.save_editor(bridge).is_ok() {
            app.editor = None;
            app.error = None;
        }
        return;
    }
    let Some(editor) = app.editor.as_mut() else {
        return;
    };
    if editor.preview {
        let max = editor.rendered.as_ref().map(|text| text.lines.len().saturating_sub(1) as u16).unwrap_or(0);
        match key.code { KeyCode::Up => editor.scroll = editor.scroll.saturating_sub(1), KeyCode::Down => editor.scroll = editor.scroll.saturating_add(1).min(max), KeyCode::PageUp => editor.scroll = editor.scroll.saturating_sub(10), KeyCode::PageDown => editor.scroll = editor.scroll.saturating_add(10).min(max), KeyCode::Home => editor.scroll = 0, KeyCode::End => editor.scroll = max, _ => {} }
        return;
    }
    let mut changed = false;
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
        KeyCode::Left => editor.buffer.left(),
        KeyCode::Right => editor.buffer.right(),
        KeyCode::Up => editor.buffer.up(),
        KeyCode::Down => editor.buffer.down(),
        KeyCode::Home => editor.buffer.home(),
        KeyCode::End => editor.buffer.end(),
        KeyCode::PageUp => {
            editor.buffer.page_up(10);
            editor.scroll = editor.scroll.saturating_sub(10);
        }
        KeyCode::PageDown => {
            editor.buffer.page_down(10);
            editor.scroll = editor.scroll.saturating_add(10);
        }
        _ => {}
    }
    if changed {
        editor.mark_changed();
    }
}
