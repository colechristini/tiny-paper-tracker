use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use lit_tui::{bridge::Bridge, state::App, ui};
use ratatui::{Terminal, backend::CrosstermBackend};
use std::io;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut bridge = Bridge::spawn()?;
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    let result = run(&mut bridge, &mut terminal);
    disable_raw_mode().ok();
    let mut stdout = io::stdout();
    execute!(stdout, LeaveAlternateScreen).ok();
    result.map_err(Into::into)
}
fn run(
    bridge: &mut Bridge,
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
) -> io::Result<()> {
    let mut app = App::default();
    app.refresh(bridge);
    while !app.should_quit {
        terminal.draw(|f| ui::draw(f, &app))?;
        if event::poll(std::time::Duration::from_millis(250))?
            && let Event::Key(key) = event::read()?
        {
            handle_key(&mut app, bridge, key);
        }
    }
    Ok(())
}
fn handle_key(app: &mut App, bridge: &mut Bridge, key: KeyEvent) {
    if key.kind == KeyEventKind::Release {
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
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,
        KeyCode::Up | KeyCode::Char('k') => app.move_selection(-1),
        KeyCode::Down | KeyCode::Char('j') => app.move_selection(1),
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
