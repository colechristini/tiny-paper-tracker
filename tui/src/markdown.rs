use pulldown_cmark::{CodeBlockKind, Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
};

/// Render local Markdown for a terminal preview.
///
/// The renderer never fetches URLs or attempts to typeset math. TeX remains
/// visible as source text, which keeps notes useful in a terminal of any size.
/// Valid YAML frontmatter at the very beginning is omitted from the preview;
/// malformed frontmatter is retained as ordinary Markdown text.
pub fn render(source: &str) -> Text<'static> {
    let sanitized_source = sanitize(source);
    let body = without_frontmatter(&sanitized_source);
    let mut renderer = Renderer::default();
    let options = Options::ENABLE_MATH
        | Options::ENABLE_TABLES
        | Options::ENABLE_TASKLISTS
        | Options::ENABLE_STRIKETHROUGH
        | Options::ENABLE_FOOTNOTES;
    for event in Parser::new_ext(body, options) {
        renderer.event(event);
    }
    renderer.finish()
}

fn without_frontmatter(source: &str) -> &str {
    let Some(first_end) = source.find('\n') else {
        return source;
    };
    let first = source[..first_end].trim_end_matches('\r');
    if first != "---" {
        return source;
    }
    let mut offset = first_end + 1;
    let mut has_mapping = false;
    let mut only_mappings = true;
    while offset <= source.len() {
        let (end, next_offset) = match source[offset..].find('\n') {
            Some(relative_end) => (offset + relative_end, offset + relative_end + 1),
            None => (source.len(), source.len()),
        };
        let line = source[offset..end].trim_end_matches('\r');
        if line == "---" {
            return if has_mapping && only_mappings {
                &source[next_offset..]
            } else {
                source
            };
        }
        let trimmed = line.trim();
        if !trimmed.is_empty() {
            let Some((key, _)) = trimmed.split_once(':') else {
                only_mappings = false;
                offset = next_offset;
                continue;
            };
            if key.is_empty()
                || !key.chars().all(|character| {
                    character.is_ascii_alphanumeric() || character == '_' || character == '-'
                })
            {
                only_mappings = false;
            } else {
                has_mapping = true;
            }
        }
        if next_offset == source.len() {
            break;
        }
        offset = next_offset;
    }
    source
}

#[derive(Clone, Copy)]
struct ListFrame {
    next: u64,
    ordered: bool,
}

#[derive(Default)]
struct Renderer {
    lines: Vec<Line<'static>>,
    current: Vec<Span<'static>>,
    style: Style,
    styles: Vec<Style>,
    lists: Vec<ListFrame>,
    quote_depth: usize,
    code: bool,
    code_style: Style,
    link_target: Option<String>,
}

impl Renderer {
    fn event(&mut self, event: Event<'_>) {
        match event {
            Event::Start(tag) => self.start(tag),
            Event::End(tag) => self.end(tag),
            Event::Text(text) => {
                let style = if self.code {
                    self.code_style
                } else {
                    self.style
                };
                self.push_text(&sanitize(&text), style);
            }
            Event::Code(code) => self.push_text(
                &format!("`{}`", sanitize(&code)),
                Style::default().fg(Color::Yellow),
            ),
            Event::Html(html) | Event::InlineHtml(html) => {
                self.push_text(&sanitize(&html), self.style)
            }
            Event::FootnoteReference(reference) => {
                self.push_text(&format!("[^{reference}]"), self.style)
            }
            Event::SoftBreak => self.flush_if_nonempty(),
            Event::HardBreak => self.flush(),
            Event::Rule => {
                self.flush_if_nonempty();
                self.current.push(Span::styled(
                    "────────────────────".to_string(),
                    Style::default().fg(Color::DarkGray),
                ));
                self.flush_if_nonempty();
            }
            Event::TaskListMarker(checked) => self.push_text(
                if checked { "[x] " } else { "[ ] " },
                Style::default().fg(Color::Green),
            ),
            Event::InlineMath(math) => self.push_text(
                &format!("${}$", sanitize(&math)),
                Style::default().fg(Color::Magenta),
            ),
            Event::DisplayMath(math) => self.push_text(
                &format!("$${}$$", sanitize(&math)),
                Style::default().fg(Color::Magenta),
            ),
        }
    }

    fn start(&mut self, tag: Tag<'_>) {
        match tag {
            Tag::Paragraph => {}
            Tag::Heading { level, .. } => {
                self.flush_if_nonempty();
                self.style = heading_style(level);
            }
            Tag::Emphasis => self.push_style(Modifier::ITALIC),
            Tag::Strong => self.push_style(Modifier::BOLD),
            Tag::Strikethrough => self.push_style(Modifier::CROSSED_OUT),
            Tag::Link { dest_url, .. } => {
                self.push_style(Modifier::UNDERLINED);
                self.style = self.style.fg(Color::Cyan);
                self.link_target = Some(sanitize(&dest_url));
            }
            Tag::Image { dest_url, .. } => {
                self.push_text("[image: ", Style::default().fg(Color::DarkGray));
                self.push_style(Modifier::ITALIC);
                self.link_target = Some(sanitize(&dest_url));
            }
            Tag::BlockQuote(_) => {
                self.flush_if_nonempty();
                self.quote_depth += 1;
            }
            Tag::List(start) => {
                self.flush_if_nonempty();
                self.lists.push(ListFrame {
                    next: start.unwrap_or(1),
                    ordered: start.is_some(),
                });
            }
            Tag::Item => {
                self.flush_if_nonempty();
                self.prefix_quote();
                let depth = self.lists.len().saturating_sub(1);
                self.current.push(Span::raw("  ".repeat(depth)));
                if let Some(frame) = self.lists.last_mut() {
                    let marker = if frame.ordered {
                        let marker = format!("{}. ", frame.next);
                        frame.next += 1;
                        marker
                    } else {
                        "• ".to_string()
                    };
                    self.current
                        .push(Span::styled(marker, Style::default().fg(Color::Green)));
                }
            }
            Tag::CodeBlock(kind) => {
                self.flush_if_nonempty();
                self.code = true;
                self.code_style = Style::default().fg(Color::Yellow);
                let language = match kind {
                    CodeBlockKind::Fenced(language) => sanitize(&language),
                    CodeBlockKind::Indented => String::new(),
                };
                self.current.push(Span::styled(
                    if language.is_empty() {
                        "```".to_string()
                    } else {
                        format!("```{language}")
                    },
                    self.code_style,
                ));
                self.flush_if_nonempty();
            }
            Tag::Table(_) | Tag::TableHead | Tag::TableRow => {
                self.flush_if_nonempty();
            }
            Tag::TableCell if !self.current.is_empty() => self.current.push(Span::raw(" │ ")),
            Tag::TableCell => {}
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph => self.flush_if_nonempty(),
            TagEnd::Heading(_) => {
                self.flush_if_nonempty();
                self.style = Style::default();
            }
            TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => self.pop_style(),
            TagEnd::Link => {
                if let Some(target) = self.link_target.take() {
                    self.current.push(Span::styled(
                        format!(" ({target})"),
                        Style::default().fg(Color::DarkGray),
                    ));
                }
                self.pop_style();
            }
            TagEnd::Image => {
                self.pop_style();
                self.push_text("]", Style::default().fg(Color::DarkGray));
                self.link_target = None;
            }
            TagEnd::BlockQuote(_) => {
                self.flush_if_nonempty();
                self.quote_depth = self.quote_depth.saturating_sub(1);
            }
            TagEnd::Item => self.flush_if_nonempty(),
            TagEnd::List(_) => {
                self.flush_if_nonempty();
                self.lists.pop();
            }
            TagEnd::CodeBlock => {
                self.flush_if_nonempty();
                self.current
                    .push(Span::styled("```".to_string(), self.code_style));
                self.flush();
                self.code = false;
                self.code_style = Style::default();
            }
            TagEnd::TableCell => {}
            TagEnd::TableHead | TagEnd::TableRow | TagEnd::Table => self.flush_if_nonempty(),
            _ => {}
        }
    }

    fn push_style(&mut self, modifier: Modifier) {
        self.styles.push(self.style);
        self.style = self.style.add_modifier(modifier);
    }

    fn pop_style(&mut self) {
        self.style = self.styles.pop().unwrap_or_default();
    }

    fn prefix_quote(&mut self) {
        if self.quote_depth > 0 {
            self.current.push(Span::styled(
                format!("{}│ ", "  ".repeat(self.quote_depth.saturating_sub(1))),
                Style::default().fg(Color::DarkGray),
            ));
        }
    }

    fn push_text(&mut self, text: &str, style: Style) {
        let mut parts = text.split('\n').peekable();
        while let Some(part) = parts.next() {
            if self.current.is_empty() {
                self.prefix_quote();
            }
            if !part.is_empty() {
                self.current.push(Span::styled(part.to_string(), style));
            }
            if parts.peek().is_some() {
                self.flush();
            }
        }
    }

    fn flush(&mut self) {
        self.lines
            .push(Line::from(std::mem::take(&mut self.current)));
    }

    fn flush_if_nonempty(&mut self) {
        if !self.current.is_empty() {
            self.flush();
        }
    }

    fn finish(mut self) -> Text<'static> {
        if !self.current.is_empty() || self.lines.is_empty() {
            self.flush();
        }
        Text::from(self.lines)
    }
}

fn heading_style(level: HeadingLevel) -> Style {
    let color = match level {
        HeadingLevel::H1 => Color::LightBlue,
        HeadingLevel::H2 => Color::Cyan,
        HeadingLevel::H3 => Color::Green,
        _ => Color::Yellow,
    };
    Style::default().fg(color).add_modifier(Modifier::BOLD)
}

fn sanitize(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    let mut characters = value.chars().peekable();
    while let Some(character) = characters.next() {
        if character == '\u{1b}' {
            if characters.next_if_eq(&'[').is_some() {
                for parameter in characters.by_ref() {
                    if ('@'..='~').contains(&parameter) {
                        break;
                    }
                }
            } else {
                let _ = characters.next();
            }
        } else if character == '\n' {
            output.push('\n');
        } else if character == '\t' {
            output.push(' ');
        } else if !character.is_control() {
            output.push(character);
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::render;
    use ratatui::style::Modifier;

    fn plain_lines(source: &str) -> Vec<String> {
        render(source)
            .lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect()
            })
            .collect()
    }

    #[test]
    fn renders_headings_emphasis_lists_and_code() {
        let text = render(
            "# Heading\n\nA **bold** and *italic* word.\n\n- one\n- two\n\n```rust\nlet x = 1;\n```",
        );
        let lines = plain_lines(
            "# Heading\n\nA **bold** and *italic* word.\n\n- one\n- two\n\n```rust\nlet x = 1;\n```",
        );
        assert!(lines.iter().any(|line| line == "Heading"));
        assert!(lines.iter().any(|line| line.contains("bold")));
        assert!(lines.iter().any(|line| line.contains("• one")));
        assert!(lines.iter().any(|line| line == "```rust"));
        assert!(lines.iter().any(|line| line.contains("let x = 1;")));
        assert!(
            text.lines[0].spans[0].style.add_modifier(Modifier::BOLD)
                == text.lines[0].spans[0].style
        );
    }

    #[test]
    fn keeps_links_math_unicode_and_line_breaks_visible() {
        let lines = plain_lines(
            "See [paper](https://example.test/p?a=1) and $x^2$ 🧪  \nnext\n\n$a_b \\\\ c$",
        );
        assert!(lines[0].contains("paper (https://example.test/p?a=1)"));
        assert!(lines[0].contains("$x^2$ 🧪"));
        assert_eq!(lines[1], "next");
        assert!(lines[2].contains("$a_b \\\\ c$"));
    }

    #[test]
    fn strips_only_valid_frontmatter() {
        assert_eq!(
            plain_lines("---\nlit_id: lit_1\n---\n# Title"),
            vec!["Title"]
        );
        assert_eq!(plain_lines("---\nlit_id: lit_1\n---"), vec![""]);
        let malformed = plain_lines("---\nlit_id: lit_1\n# Missing close").join("\n");
        assert!(malformed.contains("lit_id: lit_1"));
        assert!(malformed.contains("Missing close"));
        let horizontal = plain_lines("---\nimportant paragraph\n---").join("\n");
        assert!(horizontal.contains("important paragraph"));
    }

    #[test]
    fn removes_terminal_controls_without_merging_lines() {
        let lines = plain_lines("before\u{1b}[31mafter\nnext");
        assert_eq!(lines, vec!["beforeafter", "next"]);
    }
}
