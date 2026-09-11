#[derive(Clone, Debug)]
pub struct TextBuffer {
    text: Vec<char>,
    pub cursor: usize,
}
impl TextBuffer {
    pub fn new(text: String) -> Self {
        Self {
            text: text.chars().collect(),
            cursor: 0,
        }
    }
    pub fn text(&self) -> String {
        self.text.iter().collect()
    }
    pub fn replace_range(&mut self, start: usize, end: usize, value: &str) {
        self.text
            .splice(start..end.min(self.text.len()), value.chars());
        self.cursor = start + value.chars().count();
    }
    pub fn insert(&mut self, value: &str) {
        let chars: Vec<char> = value.chars().collect();
        self.text
            .splice(self.cursor..self.cursor, chars.iter().copied());
        self.cursor += chars.len();
    }
    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.text.remove(self.cursor);
        }
    }
    pub fn delete(&mut self) {
        if self.cursor < self.text.len() {
            self.text.remove(self.cursor);
        }
    }
    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
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
    }
    pub fn left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }
    pub fn right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.text.len());
    }
    pub fn home(&mut self) {
        while self.cursor > 0 && self.text[self.cursor - 1] != '\n' {
            self.cursor -= 1;
        }
    }
    pub fn end(&mut self) {
        while self.cursor < self.text.len() && self.text[self.cursor] != '\n' {
            self.cursor += 1;
        }
    }
    pub fn up(&mut self) {
        self.move_vertical(-1);
    }
    pub fn down(&mut self) {
        self.move_vertical(1);
    }
    fn move_vertical(&mut self, delta: i32) {
        let (line, col) = self.position();
        let target = line as i32 + delta;
        if target < 0 {
            self.home();
            return;
        }
        let lines: Vec<&[char]> = self.text.split(|c| *c == '\n').collect();
        let target = target as usize;
        if target >= lines.len() {
            self.end();
            return;
        }
        let start: usize = lines[..target].iter().map(|l| l.len() + 1).sum();
        self.cursor = start + col.min(lines[target].len());
    }
    fn position(&self) -> (usize, usize) {
        let before = &self.text[..self.cursor];
        let line = before.iter().filter(|c| **c == '\n').count();
        let col = before.iter().rev().take_while(|c| **c != '\n').count();
        (line, col)
    }
    pub fn page_up(&mut self, rows: usize) {
        for _ in 0..rows {
            self.up();
        }
    }
    pub fn page_down(&mut self, rows: usize) {
        for _ in 0..rows {
            self.down();
        }
    }
    pub fn line_col(&self) -> (usize, usize) {
        self.position()
    }
    pub fn line_prefix(&self) -> String {
        let start = self.cursor - self.position().1;
        self.text[start..self.cursor].iter().collect()
    }
}
#[cfg(test)]
mod tests {
    use super::TextBuffer;
    #[test]
    fn unicode_editing_preserves_char_boundaries() {
        let mut b = TextBuffer::new("hé\n世界\n".into());
        b.right();
        b.insert("🙂");
        b.down();
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
}
