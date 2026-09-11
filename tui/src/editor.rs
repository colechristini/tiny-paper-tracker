use unicode_width::UnicodeWidthChar;

#[derive(Clone, Debug)]
pub struct TextBuffer {
    text: Vec<char>,
    pub cursor: usize,
    visual_row_hint: Option<usize>,
    preferred_visual_col: Option<usize>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VisualRow {
    pub start: usize,
    pub end: usize,
    pub text: String,
    widths: Vec<usize>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VisualLayout {
    pub rows: Vec<VisualRow>,
}
impl TextBuffer {
    pub fn new(text: String) -> Self {
        Self {
            text: text.chars().collect(),
            cursor: 0,
            visual_row_hint: None,
            preferred_visual_col: None,
        }
    }
    pub fn text(&self) -> String {
        self.text.iter().collect()
    }
    pub fn replace_range(&mut self, start: usize, end: usize, value: &str) {
        self.text
            .splice(start..end.min(self.text.len()), value.chars());
        self.cursor = start + value.chars().count();
        self.reset_visual_navigation();
    }
    pub fn insert(&mut self, value: &str) {
        let chars: Vec<char> = value.chars().collect();
        self.text
            .splice(self.cursor..self.cursor, chars.iter().copied());
        self.cursor += chars.len();
        self.reset_visual_navigation();
    }
    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.text.remove(self.cursor);
            self.reset_visual_navigation();
        }
    }
    pub fn delete(&mut self) {
        if self.cursor < self.text.len() {
            self.text.remove(self.cursor);
            self.reset_visual_navigation();
        }
    }
    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
        self.reset_visual_navigation();
    }
    pub fn delete_line(&mut self) {
        let line_start = self.text[..self.cursor]
            .iter()
            .rposition(|c| *c == '\n')
            .map_or(0, |index| index + 1);
        let line_end = self.text[self.cursor..]
            .iter()
            .position(|c| *c == '\n')
            .map_or(self.text.len(), |offset| self.cursor + offset);
        let (start, end) = if line_end < self.text.len() {
            (line_start, line_end + 1)
        } else if line_start > 0 {
            (line_start - 1, self.text.len())
        } else {
            (line_start, line_end)
        };
        self.text.drain(start..end);
        self.cursor = start.min(self.text.len());
        self.reset_visual_navigation();
    }
    pub fn delete_word_backwards(&mut self) {
        let mut start = self.cursor;
        while start > 0 && self.text[start - 1].is_whitespace() && self.text[start - 1] != '\n' {
            start -= 1;
        }
        while start > 0 && !self.text[start - 1].is_whitespace() {
            start -= 1;
        }
        self.text.drain(start..self.cursor);
        self.cursor = start;
        self.reset_visual_navigation();
    }
    pub fn insert_newline_with_list_continuation(&mut self) {
        let line_start = self.text[..self.cursor]
            .iter()
            .rposition(|c| *c == '\n')
            .map_or(0, |index| index + 1);
        let line_end = self.text[self.cursor..]
            .iter()
            .position(|c| *c == '\n')
            .map_or(self.text.len(), |offset| self.cursor + offset);
        let line = &self.text[line_start..line_end];
        let before_cursor = &self.text[line_start..self.cursor];
        let Some((marker_start, marker_end, continuation)) = list_marker(before_cursor) else {
            self.text.insert(self.cursor, '\n');
            self.cursor += 1;
            self.reset_visual_navigation();
            return;
        };
        if line[marker_end..]
            .iter()
            .all(|character| character.is_whitespace())
        {
            let marker_start_absolute = line_start + marker_start;
            let marker_end_absolute = line_start + marker_end;
            self.text.drain(marker_start_absolute..marker_end_absolute);
            self.cursor -= marker_end - marker_start;
            self.text.insert(self.cursor, '\n');
            self.cursor += 1;
            self.reset_visual_navigation();
            return;
        }
        self.text.insert(self.cursor, '\n');
        self.cursor += 1;
        self.text
            .splice(self.cursor..self.cursor, continuation.chars());
        self.cursor += continuation.chars().count();
        self.reset_visual_navigation();
    }
    pub fn left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
        self.reset_visual_navigation();
    }
    pub fn right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.text.len());
        self.reset_visual_navigation();
    }
    pub fn home(&mut self) {
        while self.cursor > 0 && self.text[self.cursor - 1] != '\n' {
            self.cursor -= 1;
        }
        self.reset_visual_navigation();
    }
    pub fn end(&mut self) {
        while self.cursor < self.text.len() && self.text[self.cursor] != '\n' {
            self.cursor += 1;
        }
        self.reset_visual_navigation();
    }
    pub fn word_left(&mut self) {
        while self.cursor > 0 && self.text[self.cursor - 1].is_whitespace() {
            self.cursor -= 1;
        }
        while self.cursor > 0 && !self.text[self.cursor - 1].is_whitespace() {
            self.cursor -= 1;
        }
        self.reset_visual_navigation();
    }
    pub fn word_right(&mut self) {
        while self.cursor < self.text.len() && self.text[self.cursor].is_whitespace() {
            self.cursor += 1;
        }
        while self.cursor < self.text.len() && !self.text[self.cursor].is_whitespace() {
            self.cursor += 1;
        }
        self.reset_visual_navigation();
    }
    pub fn insert_pair(&mut self, marker: &str) {
        let marker = marker.chars().collect::<Vec<_>>();
        if marker.is_empty() {
            return;
        }
        if self.text[self.cursor..].starts_with(&marker) {
            self.cursor += marker.len();
            self.reset_visual_navigation();
            return;
        }
        let mut pair = marker.clone();
        pair.extend(marker.iter().copied());
        self.text.splice(self.cursor..self.cursor, pair);
        self.cursor += marker.len();
        self.reset_visual_navigation();
    }
    fn position(&self) -> (usize, usize) {
        let before = &self.text[..self.cursor];
        let line = before.iter().filter(|c| **c == '\n').count();
        let col = before.iter().rev().take_while(|c| **c != '\n').count();
        (line, col)
    }
    pub fn visual_layout(&self, width: usize) -> VisualLayout {
        VisualLayout::new(&self.text, width.max(1))
    }
    pub fn visual_position(&self, width: usize) -> (usize, usize) {
        self.visual_layout(width)
            .cursor_position(self.cursor, self.visual_row_hint)
    }
    pub fn visual_up(&mut self, width: usize) {
        self.move_visual_rows(width, -1);
    }
    pub fn visual_down(&mut self, width: usize) {
        self.move_visual_rows(width, 1);
    }
    pub fn visual_page_up(&mut self, width: usize, rows: usize) {
        self.move_visual_rows(width, -(rows as isize));
    }
    pub fn visual_page_down(&mut self, width: usize, rows: usize) {
        self.move_visual_rows(width, rows as isize);
    }
    pub fn visual_home(&mut self, width: usize) {
        let layout = self.visual_layout(width);
        let (row, _) = layout.cursor_position(self.cursor, self.visual_row_hint);
        self.cursor = layout.rows[row].start;
        self.visual_row_hint = Some(row);
        self.preferred_visual_col = None;
    }
    pub fn visual_end(&mut self, width: usize) {
        let layout = self.visual_layout(width);
        let (row, _) = layout.cursor_position(self.cursor, self.visual_row_hint);
        self.cursor = layout.rows[row].end;
        self.visual_row_hint = Some(row);
        self.preferred_visual_col = None;
    }
    fn move_visual_rows(&mut self, width: usize, delta: isize) {
        let layout = self.visual_layout(width);
        let (row, col) = layout.cursor_position(self.cursor, self.visual_row_hint);
        let preferred = self.preferred_visual_col.unwrap_or(col);
        let target = row
            .saturating_add_signed(delta)
            .min(layout.rows.len().saturating_sub(1));
        self.cursor = layout.cursor_at_column(target, preferred);
        self.visual_row_hint = Some(target);
        self.preferred_visual_col = Some(preferred);
    }
    fn reset_visual_navigation(&mut self) {
        self.visual_row_hint = None;
        self.preferred_visual_col = None;
    }
    pub fn line_col(&self) -> (usize, usize) {
        self.position()
    }
    pub fn line_prefix(&self) -> String {
        let start = self.cursor - self.position().1;
        self.text[start..self.cursor].iter().collect()
    }
}

impl VisualLayout {
    fn new(text: &[char], width: usize) -> Self {
        let mut rows = Vec::new();
        let mut line_start = 0;
        for line_end in
            (0..=text.len()).filter(|index| *index == text.len() || text[*index] == '\n')
        {
            wrap_line(text, line_start, line_end, width, &mut rows);
            line_start = line_end.saturating_add(1);
        }
        Self { rows }
    }

    fn cursor_position(&self, cursor: usize, row_hint: Option<usize>) -> (usize, usize) {
        let row = row_hint
            .filter(|row| {
                self.rows
                    .get(*row)
                    .is_some_and(|line| line.start <= cursor && cursor <= line.end)
            })
            .unwrap_or_else(|| {
                self.rows
                    .iter()
                    .rposition(|line| line.start <= cursor)
                    .unwrap_or(0)
            });
        let line = &self.rows[row];
        let col = line.widths[..cursor.saturating_sub(line.start).min(line.end - line.start)]
            .iter()
            .sum();
        (row, col)
    }

    fn cursor_at_column(&self, row: usize, target_col: usize) -> usize {
        let line = &self.rows[row];
        let mut cursor = line.start;
        let mut col = 0;
        for character_width in &line.widths {
            let next = col + character_width;
            if next > target_col {
                break;
            }
            col = next;
            cursor += 1;
        }
        cursor
    }
}

fn wrap_line(text: &[char], start: usize, end: usize, width: usize, rows: &mut Vec<VisualRow>) {
    if start == end {
        rows.push(VisualRow {
            start,
            end,
            text: String::new(),
            widths: vec![],
        });
        return;
    }
    let mut row_start = start;
    while row_start < end {
        let mut cursor = row_start;
        let mut used = 0;
        let mut last_word_break = None;
        let mut seen_non_whitespace = false;
        while cursor < end {
            let character = text[cursor];
            let character_width = character_width(character);
            if cursor > row_start && used + character_width > width {
                break;
            }
            used += character_width;
            cursor += 1;
            if character.is_whitespace() && seen_non_whitespace {
                last_word_break = Some(cursor);
            } else if !character.is_whitespace() {
                seen_non_whitespace = true;
            }
        }
        let (row_end, hide_trailing_whitespace) = if cursor < end {
            if text[cursor].is_whitespace() {
                while cursor < end && text[cursor].is_whitespace() {
                    cursor += 1;
                }
                (cursor, true)
            } else if let Some(word_break) =
                last_word_break.filter(|word_break| *word_break > row_start)
            {
                (word_break, true)
            } else {
                (cursor, false)
            }
        } else {
            (end, false)
        };
        let display_end = if hide_trailing_whitespace {
            (row_start..row_end)
                .rev()
                .find(|index| !text[*index].is_whitespace())
                .map_or(row_start, |index| index + 1)
        } else {
            row_end
        };
        rows.push(VisualRow {
            start: row_start,
            end: row_end,
            text: text[row_start..display_end]
                .iter()
                .flat_map(|character| {
                    if *character == '\t' {
                        "    ".chars().collect::<Vec<_>>()
                    } else {
                        vec![*character]
                    }
                })
                .collect(),
            widths: (row_start..row_end)
                .map(|index| {
                    if index < display_end {
                        character_width(text[index])
                    } else {
                        0
                    }
                })
                .collect(),
        });
        row_start = row_end;
    }
}

fn character_width(character: char) -> usize {
    if character == '\t' {
        4
    } else {
        character.width().unwrap_or(0)
    }
}

fn list_marker(line: &[char]) -> Option<(usize, usize, String)> {
    let marker_start = line
        .iter()
        .take_while(|character| **character == ' ' || **character == '\t')
        .count();
    let rest = &line[marker_start..];
    let indentation = line[..marker_start].iter().collect::<String>();
    if rest.starts_with(&['-', ' ']) {
        return Some((marker_start, marker_start + 2, format!("{indentation}- ")));
    }
    let separator = rest.iter().position(|character| *character == '.')?;
    if separator + 1 >= rest.len() || rest[separator + 1] != ' ' || separator == 0 {
        return None;
    }
    let token = &rest[..separator];
    let marker_end = marker_start + separator + 2;
    if token.iter().all(|character| character.is_ascii_digit()) {
        let token_text = token.iter().collect::<String>();
        let next = token_text
            .parse::<u64>()
            .ok()
            .and_then(|number| number.checked_add(1))
            .map_or(token_text, |number| number.to_string());
        return Some((marker_start, marker_end, format!("{indentation}{next}. ")));
    }
    if token.len() <= 2
        && token
            .iter()
            .all(|character| character.is_ascii_alphabetic())
    {
        let next = increment_letters(token)?;
        return Some((marker_start, marker_end, format!("{indentation}{next}. ")));
    }
    None
}

fn increment_letters(token: &[char]) -> Option<String> {
    let uppercase = token[0].is_ascii_uppercase();
    let mut value: Vec<u8> = token
        .iter()
        .map(|character| character.to_ascii_lowercase() as u8 - b'a')
        .collect();
    for index in (0..value.len()).rev() {
        if value[index] < 25 {
            value[index] += 1;
            return Some(
                value
                    .into_iter()
                    .map(|character| {
                        let character = (b'a' + character) as char;
                        if uppercase {
                            character.to_ascii_uppercase()
                        } else {
                            character
                        }
                    })
                    .collect(),
            );
        }
        value[index] = 0;
    }
    let character = if uppercase { 'A' } else { 'a' };
    Some(
        std::iter::once(character)
            .chain(value.into_iter().map(|_| character))
            .collect(),
    )
}
#[cfg(test)]
mod tests {
    use super::TextBuffer;
    #[test]
    fn unicode_editing_preserves_char_boundaries() {
        let mut b = TextBuffer::new("hé\n世界\n".into());
        b.right();
        b.insert("🙂");
        b.visual_down(80);
        b.end();
        b.backspace();
        assert_eq!(b.text(), "h🙂é\n世\n");
    }
    #[test]
    fn trailing_newline_is_preserved_without_edits() {
        let b = TextBuffer::new("a\n".into());
        assert_eq!(b.text(), "a\n");
    }

    #[test]
    fn clear_and_delete_line_cover_first_middle_and_last_lines() {
        let mut first = TextBuffer::new("first\nsecond\nlast".into());
        first.delete_line();
        assert_eq!(first.text(), "second\nlast");

        let mut middle = TextBuffer::new("first\nsecond\nlast".into());
        middle.cursor = 8;
        middle.delete_line();
        assert_eq!(middle.text(), "first\nlast");

        let mut blank = TextBuffer::new("first\n\nlast".into());
        blank.cursor = 6;
        blank.delete_line();
        assert_eq!(blank.text(), "first\nlast");

        let mut last = TextBuffer::new("first\nsecond\nlast".into());
        last.cursor = last.text.len();
        last.delete_line();
        assert_eq!(last.text(), "first\nsecond");

        last.clear();
        assert_eq!(last.text(), "");
        assert_eq!(last.cursor, 0);
    }

    #[test]
    fn backward_word_delete_is_unicode_safe_and_stops_at_newline() {
        let mut b = TextBuffer::new("hé  世界\none".into());
        b.cursor = "hé  世界".chars().count();
        b.delete_word_backwards();
        assert_eq!(b.text(), "hé  \none");
        b.delete_word_backwards();
        assert_eq!(b.text(), "\none");

        let mut line = TextBuffer::new("hé\none".into());
        line.cursor = 3;
        line.delete_word_backwards();
        assert_eq!(line.text(), "hé\none");
    }

    #[test]
    fn list_enter_continues_markers_and_preserves_unicode_split() {
        let mut bullet = TextBuffer::new("  - hé世界".into());
        bullet.cursor = "  - hé".chars().count();
        bullet.insert_newline_with_list_continuation();
        assert_eq!(bullet.text(), "  - hé\n  - 世界");

        let mut number = TextBuffer::new("9. item".into());
        number.cursor = number.text().chars().count();
        number.insert_newline_with_list_continuation();
        assert_eq!(number.text(), "9. item\n10. ");

        let mut letter = TextBuffer::new("a. item".into());
        letter.cursor = letter.text().chars().count();
        letter.insert_newline_with_list_continuation();
        assert_eq!(letter.text(), "a. item\nb. ");

        let mut boundary = TextBuffer::new("z. item".into());
        boundary.cursor = boundary.text().chars().count();
        boundary.insert_newline_with_list_continuation();
        assert_eq!(boundary.text(), "z. item\naa. ");
    }

    #[test]
    fn list_enter_exits_empty_items_and_ignores_non_markers_or_marker_cursor() {
        let mut empty = TextBuffer::new("\t- ".into());
        empty.cursor = empty.text().chars().count();
        empty.insert_newline_with_list_continuation();
        assert_eq!(empty.text(), "\t\n");

        let mut ordinary = TextBuffer::new("words. text".into());
        ordinary.cursor = ordinary.text().chars().count();
        ordinary.insert_newline_with_list_continuation();
        assert_eq!(ordinary.text(), "words. text\n");

        let mut inside_marker = TextBuffer::new("- item".into());
        inside_marker.cursor = 1;
        inside_marker.insert_newline_with_list_continuation();
        assert_eq!(inside_marker.text(), "-\n item");
    }

    #[test]
    fn visual_layout_wraps_words_oversized_tokens_and_unicode_without_editing_text() {
        let text = "hello world abcdef\n世界🙂 x";
        let buffer = TextBuffer::new(text.into());

        let layout = buffer.visual_layout(5);

        assert_eq!(
            layout
                .rows
                .iter()
                .map(|row| row.text.as_str())
                .collect::<Vec<_>>(),
            vec!["hello", "world", "abcde", "f", "世界", "🙂 x"]
        );
        assert_eq!(buffer.text(), text);

        let mut whitespace = TextBuffer::new("hello world".into());
        let layout = whitespace.visual_layout(5);
        assert_eq!(
            layout
                .rows
                .iter()
                .map(|row| row.text.as_str())
                .collect::<Vec<_>>(),
            vec!["hello", "world"]
        );
        whitespace.cursor = 5;
        assert_eq!(whitespace.visual_position(5), (0, 5));
        whitespace.cursor = 6;
        assert_eq!(whitespace.visual_position(5), (1, 0));

        let tabbed = TextBuffer::new("a\tb".into());
        assert_eq!(tabbed.visual_layout(8).rows[0].text, "a    b");
    }

    #[test]
    fn visual_cursor_navigation_tracks_wrap_boundaries_and_preferred_unicode_column() {
        let mut wrapped = TextBuffer::new("abcdeX".into());
        wrapped.cursor = 5;
        assert_eq!(wrapped.visual_position(5), (1, 0));
        wrapped.cursor = 2;
        wrapped.visual_end(5);
        assert_eq!(wrapped.cursor, 5);
        assert_eq!(wrapped.visual_position(5), (0, 5));
        wrapped.right();
        assert_eq!(wrapped.visual_position(5), (1, 1));

        let mut unicode = TextBuffer::new("abcd\n界x\nabcdef".into());
        unicode.cursor = 4;
        unicode.visual_down(10);
        assert_eq!(unicode.visual_position(10), (1, 3));
        unicode.visual_down(10);
        assert_eq!(unicode.visual_position(10), (2, 4));
        unicode.visual_up(10);
        assert_eq!(unicode.visual_position(10), (1, 3));
    }

    #[test]
    fn paired_markers_insert_at_cursor_and_repeat_skips_the_closer() {
        let mut bold = TextBuffer::new("ab".into());
        bold.cursor = 1;
        bold.insert_pair("**");
        assert_eq!(bold.text(), "a****b");
        assert_eq!(bold.cursor, 3);
        bold.insert("世");
        assert_eq!(bold.text(), "a**世**b");
        bold.insert_pair("**");
        assert_eq!(bold.text(), "a**世**b");
        assert_eq!(bold.cursor, 6);

        let mut italic = TextBuffer::new(String::new());
        italic.insert_pair("*");
        assert_eq!(italic.text(), "**");
        assert_eq!(italic.cursor, 1);
        italic.insert_pair("*");
        assert_eq!(italic.text(), "**");
        assert_eq!(italic.cursor, 2);
    }

    #[test]
    fn word_navigation_is_unicode_safe() {
        let mut buffer = TextBuffer::new("one 世界  🙂test".into());
        buffer.cursor = buffer.text.len();
        buffer.word_left();
        assert_eq!(buffer.cursor, "one 世界  ".chars().count());
        buffer.word_left();
        assert_eq!(buffer.cursor, "one ".chars().count());
        buffer.word_right();
        assert_eq!(buffer.cursor, "one 世界".chars().count());
        buffer.word_right();
        assert_eq!(buffer.cursor, buffer.text.len());

        buffer.cursor = 1;
        buffer.word_right();
        assert_eq!(buffer.cursor, "one".chars().count());
    }
}
