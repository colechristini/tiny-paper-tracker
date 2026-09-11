use crossterm::{
    cursor::Show,
    event::{
        self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers, KeyboardEnhancementFlags, PopKeyboardEnhancementFlags,
        PushKeyboardEnhancementFlags,
    },
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use lit_tui::completion;
use lit_tui::{
    bridge::Bridge,
    state::{App, NoteFilter},
    ui,
};
use ratatui::{Terminal, backend::CrosstermBackend, layout::Rect};
use std::io;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut bridge = Bridge::spawn()?;
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableBracketedPaste)?;
    let keyboard_enhancement = execute!(
        stdout,
        PushKeyboardEnhancementFlags(
            KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES
                | KeyboardEnhancementFlags::REPORT_ALL_KEYS_AS_ESCAPE_CODES
                | KeyboardEnhancementFlags::REPORT_ALTERNATE_KEYS
                | KeyboardEnhancementFlags::REPORT_EVENT_TYPES
        )
    )
    .is_ok();
    let _terminal_guard = TerminalGuard {
        keyboard_enhancement,
    };
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    let result = run(&mut bridge, &mut terminal);
    result.map_err(Into::into)
}

struct TerminalGuard {
    keyboard_enhancement: bool,
}
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let mut stdout = io::stdout();
        if self.keyboard_enhancement {
            let _ = execute!(stdout, PopKeyboardEnhancementFlags);
        }
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
                    if app.add.is_some() {
                        app.insert_add_text(&text);
                    } else if app.rename.is_some() {
                        app.insert_rename_text(&text);
                    } else if app.searching {
                        app.search_input.insert(&text.replace(['\r', '\n'], " "));
                    } else if app.editor.as_ref().is_some_and(|e| !e.preview) {
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
        if app.poll_add(bridge) {
            redraw = true;
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
    if app.add.is_some() {
        if app.add.as_ref().is_some_and(|add| add.busy()) {
            return;
        }
        let command = key
            .modifiers
            .intersects(KeyModifiers::SUPER | KeyModifiers::META);
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if command && matches!(key.code, KeyCode::Backspace | KeyCode::Delete) {
            if let Some(add) = app.add.as_mut() {
                add.buffer.clear();
            }
            return;
        }
        if ctrl && key.code == KeyCode::Char('u') {
            if let Some(add) = app.add.as_mut() {
                add.buffer.clear();
            }
            return;
        }
        if (ctrl && key.code == KeyCode::Char('w'))
            || (alt && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
        {
            if let Some(add) = app.add.as_mut() {
                add.buffer.delete_word_backwards();
            }
            return;
        }
        if app
            .add
            .as_mut()
            .is_some_and(|add| apply_text_navigation(&mut add.buffer, key))
        {
            return;
        }
        match key.code {
            KeyCode::Esc => app.cancel_add(),
            KeyCode::Enter => app.submit_add(),
            KeyCode::Backspace => {
                if let Some(add) = app.add.as_mut() {
                    add.buffer.backspace()
                }
            }
            KeyCode::Delete => {
                if let Some(add) = app.add.as_mut() {
                    add.buffer.delete()
                }
            }
            KeyCode::Char(c) if is_unmodified_text(key) => app.insert_add_text(&c.to_string()),
            _ => {}
        }
        return;
    }
    if app.membership.is_some() {
        match key.code {
            KeyCode::Esc => app.cancel_membership(),
            KeyCode::Enter => app.submit_membership(bridge),
            _ => app.membership_key(key.code),
        }
        return;
    }
    if app.rename.is_some() {
        let command = key
            .modifiers
            .intersects(KeyModifiers::SUPER | KeyModifiers::META);
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if command && matches!(key.code, KeyCode::Backspace | KeyCode::Delete) {
            if let Some(rename) = app.rename.as_mut() {
                rename.buffer.clear();
            }
            return;
        }
        if ctrl && key.code == KeyCode::Char('u') {
            if let Some(rename) = app.rename.as_mut() {
                rename.buffer.clear();
            }
            return;
        }
        if (ctrl && key.code == KeyCode::Char('w'))
            || (alt && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
        {
            if let Some(rename) = app.rename.as_mut() {
                rename.buffer.delete_word_backwards();
            }
            return;
        }
        if app
            .rename
            .as_mut()
            .is_some_and(|rename| apply_text_navigation(&mut rename.buffer, key))
        {
            return;
        }
        match key.code {
            KeyCode::Esc => app.cancel_rename(),
            KeyCode::Enter => app.submit_rename(bridge),
            KeyCode::Backspace => {
                if let Some(r) = app.rename.as_mut() {
                    r.buffer.backspace()
                }
            }
            KeyCode::Delete => {
                if let Some(r) = app.rename.as_mut() {
                    r.buffer.delete()
                }
            }
            KeyCode::Char(c) if is_unmodified_text(key) => app.insert_rename_text(&c.to_string()),
            _ => {}
        }
        return;
    }
    if app.help {
        if matches!(key.code, KeyCode::Char('?') | KeyCode::Esc) {
            app.help = false;
        }
        return;
    }
    if app.searching {
        if apply_search_edit(&mut app.search_input, key) {
            return;
        }
        match key.code {
            KeyCode::Esc => app.searching = false,
            KeyCode::Enter => {
                app.query = app.search_input.text();
                app.searching = false;
                app.refresh(bridge);
            }
            KeyCode::Backspace => {
                app.search_input.backspace();
            }
            KeyCode::Delete => app.search_input.delete(),
            KeyCode::Char(c) if is_unmodified_text(key) => app.search_input.insert(&c.to_string()),
            _ => {}
        }
        return;
    }
    app.notice = None;
    match key.code {
        KeyCode::Enter => app.open_editor(bridge),
        KeyCode::Char('a') => app.begin_add(),
        KeyCode::F(2) | KeyCode::Char('R') => app.begin_rename(),
        KeyCode::Char('m') => app.begin_membership(),
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
        KeyCode::Char('5') => {
            app.toggle_note_filter(NoteFilter::HasNote);
            app.refresh(bridge);
        }
        KeyCode::Char('6') => {
            app.toggle_note_filter(NoteFilter::NoNote);
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
        KeyCode::Delete | KeyCode::Backspace if is_plain_delete(key) => app.delete_selected(bridge),
        KeyCode::Char('/') => {
            app.searching = true;
            app.search_input = lit_tui::editor::TextBuffer::new(app.query.clone());
            app.search_input.end();
        }
        KeyCode::Char('?') => app.help = true,
        KeyCode::Char('f') => app.refresh(bridge),
        _ => {}
    }
}

fn is_plain_delete(key: KeyEvent) -> bool {
    matches!(key.code, KeyCode::Delete | KeyCode::Backspace) && key.modifiers.is_empty()
}

fn is_unmodified_text(key: KeyEvent) -> bool {
    !key.modifiers.intersects(
        KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::META,
    )
}

fn apply_text_navigation(buffer: &mut lit_tui::editor::TextBuffer, key: KeyEvent) -> bool {
    if apply_modified_text_navigation(buffer, key) {
        return true;
    }
    match key.code {
        KeyCode::Left if key.modifiers.is_empty() => buffer.left(),
        KeyCode::Right if key.modifiers.is_empty() => buffer.right(),
        KeyCode::Home if key.modifiers.is_empty() => buffer.home(),
        KeyCode::End if key.modifiers.is_empty() => buffer.end(),
        _ => return false,
    }
    true
}

fn apply_modified_text_navigation(buffer: &mut lit_tui::editor::TextBuffer, key: KeyEvent) -> bool {
    let command = key
        .modifiers
        .intersects(KeyModifiers::SUPER | KeyModifiers::META);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        KeyCode::Left if command => buffer.home(),
        KeyCode::Right if command => buffer.end(),
        KeyCode::Char('a' | 'A') if ctrl => buffer.home(),
        KeyCode::Char('e' | 'E') if ctrl => buffer.end(),
        KeyCode::Left if alt || ctrl => buffer.word_left(),
        KeyCode::Right if alt || ctrl => buffer.word_right(),
        KeyCode::Char('b' | 'B') if alt => buffer.word_left(),
        KeyCode::Char('f' | 'F') if alt => buffer.word_right(),
        KeyCode::Char('p' | 'P') if ctrl => buffer.word_left(),
        KeyCode::Char('n' | 'N') if ctrl => buffer.word_right(),
        _ => return false,
    }
    true
}

fn apply_search_edit(search: &mut lit_tui::editor::TextBuffer, key: KeyEvent) -> bool {
    let command = key
        .modifiers
        .intersects(KeyModifiers::SUPER | KeyModifiers::META);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    if (command && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
        || (ctrl && key.code == KeyCode::Char('u'))
    {
        search.clear();
        return true;
    }
    if (ctrl && key.code == KeyCode::Char('w'))
        || (alt && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
    {
        search.delete_word_backwards();
        return true;
    }
    apply_text_navigation(search, key)
}

fn apply_note_edit_shortcut(buffer: &mut lit_tui::editor::TextBuffer, key: KeyEvent) -> bool {
    let command = key
        .modifiers
        .intersects(KeyModifiers::SUPER | KeyModifiers::META);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    if (command && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
        || (ctrl && key.code == KeyCode::Char('u'))
    {
        buffer.delete_line();
        return true;
    }
    if (ctrl && key.code == KeyCode::Char('w'))
        || (alt && matches!(key.code, KeyCode::Backspace | KeyCode::Delete))
    {
        buffer.delete_word_backwards();
        return true;
    }
    false
}

fn apply_formatting_shortcut(buffer: &mut lit_tui::editor::TextBuffer, key: KeyEvent) -> bool {
    let command = key
        .modifiers
        .intersects(KeyModifiers::SUPER | KeyModifiers::META);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let marker = match key.code {
        KeyCode::Char('b' | 'B') if command || ctrl => "**",
        KeyCode::Char('i' | 'I') if command || ctrl => "*",
        KeyCode::Char('t' | 'T') if ctrl => "*",
        KeyCode::Tab if key.modifiers.is_empty() => "*",
        _ => return false,
    };
    buffer.insert_pair(marker);
    true
}

fn accepts_completion(key: KeyEvent) -> bool {
    key.code == KeyCode::Tab
        || (key.code == KeyCode::Char('i') && key.modifiers.contains(KeyModifiers::CONTROL))
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
    if ctrl && key.code == KeyCode::Char('g') {
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
            _ if accepts_completion(key) => {
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
    if apply_formatting_shortcut(&mut editor.buffer, key) {
        editor.mark_changed();
        update_completion(editor, bridge);
        return;
    }
    let mut changed = false;
    let mut moved = false;
    let text_area = ui::editor_text_area(area);
    let wrap_width = text_area.width.saturating_sub(3).max(1) as usize;
    let page_rows = text_area.height.saturating_sub(2).max(1) as usize;
    if apply_note_edit_shortcut(&mut editor.buffer, key) {
        changed = true;
    } else if apply_modified_text_navigation(&mut editor.buffer, key) {
        moved = true;
    } else {
        match key.code {
            KeyCode::Char(c) if is_unmodified_text(key) => {
                editor.buffer.insert(&c.to_string());
                changed = true;
            }
            KeyCode::Enter => {
                editor.buffer.insert_newline_with_list_continuation();
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
            KeyCode::Left if key.modifiers.is_empty() => {
                editor.buffer.left();
                moved = true;
            }
            KeyCode::Right if key.modifiers.is_empty() => {
                editor.buffer.right();
                moved = true;
            }
            KeyCode::Up if key.modifiers.is_empty() => {
                editor.buffer.visual_up(wrap_width);
                moved = true;
            }
            KeyCode::Down if key.modifiers.is_empty() => {
                editor.buffer.visual_down(wrap_width);
                moved = true;
            }
            KeyCode::Home if key.modifiers.is_empty() => {
                editor.buffer.visual_home(wrap_width);
                moved = true;
            }
            KeyCode::End if key.modifiers.is_empty() => {
                editor.buffer.visual_end(wrap_width);
                moved = true;
            }
            KeyCode::PageUp if key.modifiers.is_empty() => {
                editor.buffer.visual_page_up(wrap_width, page_rows);
                editor.scroll = editor.scroll.saturating_sub(page_rows as u16);
                moved = true;
            }
            KeyCode::PageDown if key.modifiers.is_empty() => {
                editor.buffer.visual_page_down(wrap_width, page_rows);
                editor.scroll = editor.scroll.saturating_add(page_rows as u16);
                moved = true;
            }
            _ => {}
        }
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
    use super::{
        accepts_completion, apply_formatting_shortcut, apply_note_edit_shortcut, apply_search_edit,
        apply_text_navigation, is_plain_delete, is_unmodified_text, wrapped_lines,
    };
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use lit_tui::editor::TextBuffer;
    use ratatui::text::Text;
    #[test]
    fn wrapped_preview_scroll_counts_long_paragraphs() {
        let text = Text::raw("x".repeat(2000));
        assert!(wrapped_lines(&text, 38) > 50);
        assert_eq!(wrapped_lines(&Text::raw("short"), 38), 1);
        assert_eq!(wrapped_lines(&Text::raw("123456 123456 123456"), 10), 3);
    }

    #[test]
    fn modified_delete_edits_note_line_and_cannot_delete_list_item() {
        let mut buffer = TextBuffer::new("first\nsecond\nlast".into());
        buffer.cursor = 8;
        let command_delete = KeyEvent::new(KeyCode::Delete, KeyModifiers::SUPER);
        assert!(apply_note_edit_shortcut(&mut buffer, command_delete));
        assert_eq!(buffer.text(), "first\nlast");

        assert!(is_plain_delete(KeyEvent::new(
            KeyCode::Delete,
            KeyModifiers::NONE
        )));
        assert!(!is_plain_delete(command_delete));
        assert!(!is_plain_delete(KeyEvent::new(
            KeyCode::Delete,
            KeyModifiers::ALT
        )));

        let mut search = TextBuffer::new("unicode 世界".into());
        search.end();
        assert!(apply_search_edit(
            &mut search,
            KeyEvent::new(KeyCode::Char('w'), KeyModifiers::CONTROL)
        ));
        assert_eq!(search.text(), "unicode ");
    }

    #[test]
    fn command_formatting_and_modified_navigation_do_not_insert_plain_characters() {
        let mut buffer = TextBuffer::new("one 世界  next".into());
        buffer.cursor = 1;
        assert!(apply_text_navigation(
            &mut buffer,
            KeyEvent::new(KeyCode::Right, KeyModifiers::ALT)
        ));
        assert_eq!(buffer.cursor, 3);
        assert!(apply_text_navigation(
            &mut buffer,
            KeyEvent::new(KeyCode::Right, KeyModifiers::SUPER)
        ));
        assert_eq!(buffer.cursor, buffer.text().chars().count());

        assert!(apply_formatting_shortcut(
            &mut buffer,
            KeyEvent::new(KeyCode::Char('b'), KeyModifiers::SUPER)
        ));
        assert!(buffer.text().ends_with("****"));
        assert!(!is_unmodified_text(KeyEvent::new(
            KeyCode::Char('b'),
            KeyModifiers::SUPER
        )));
        assert!(apply_formatting_shortcut(
            &mut buffer,
            KeyEvent::new(KeyCode::Char('i'), KeyModifiers::CONTROL)
        ));
        assert!(apply_formatting_shortcut(
            &mut buffer,
            KeyEvent::new(KeyCode::Char('t'), KeyModifiers::CONTROL)
        ));
        assert!(apply_formatting_shortcut(
            &mut buffer,
            KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE)
        ));
        assert!(accepts_completion(KeyEvent::new(
            KeyCode::Tab,
            KeyModifiers::NONE
        )));
        assert!(accepts_completion(KeyEvent::new(
            KeyCode::Char('i'),
            KeyModifiers::CONTROL
        )));
        assert!(!accepts_completion(KeyEvent::new(
            KeyCode::Char('t'),
            KeyModifiers::CONTROL
        )));

        let mut portable = TextBuffer::new("first middle last".into());
        portable.end();
        assert!(apply_text_navigation(
            &mut portable,
            KeyEvent::new(KeyCode::Char('a'), KeyModifiers::CONTROL)
        ));
        assert_eq!(portable.cursor, 0);
        assert!(apply_text_navigation(
            &mut portable,
            KeyEvent::new(KeyCode::Char('n'), KeyModifiers::CONTROL)
        ));
        assert_eq!(portable.cursor, "first".chars().count());
        assert!(apply_text_navigation(
            &mut portable,
            KeyEvent::new(KeyCode::Right, KeyModifiers::CONTROL)
        ));
        assert_eq!(portable.cursor, "first middle".chars().count());
        assert!(apply_text_navigation(
            &mut portable,
            KeyEvent::new(KeyCode::Char('p'), KeyModifiers::CONTROL)
        ));
        assert_eq!(portable.cursor, "first ".chars().count());
        assert!(apply_text_navigation(
            &mut portable,
            KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL)
        ));
        assert_eq!(portable.cursor, portable.text().chars().count());
    }

    #[test]
    fn search_buffer_supports_middle_edits_and_word_navigation() {
        let mut search = TextBuffer::new("alpha 世界 omega".into());
        search.cursor = "alpha ".chars().count();
        search.insert("new ");
        assert_eq!(search.text(), "alpha new 世界 omega");
        assert!(apply_search_edit(
            &mut search,
            KeyEvent::new(KeyCode::Right, KeyModifiers::ALT)
        ));
        assert_eq!(search.cursor, "alpha new 世界".chars().count());
        search.backspace();
        assert_eq!(search.text(), "alpha new 世 omega");
    }
}
