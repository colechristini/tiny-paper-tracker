use crate::bridge::LinkCandidate;
use crate::editor::TextBuffer;

#[derive(Clone, Debug)]
pub struct Completion {
    pub start: usize,
    pub end: usize,
    pub query: String,
    pub candidates: Vec<LinkCandidate>,
    pub selected: usize,
}
pub fn extract(buffer: &TextBuffer) -> Option<(usize, usize, String)> {
    let chars: Vec<char> = buffer.text().chars().collect();
    let cursor = buffer.cursor.min(chars.len());
    let line_start = chars[..cursor]
        .iter()
        .rposition(|c| *c == '\n')
        .map_or(0, |i| i + 1);
    let at = chars[line_start..cursor].iter().rposition(|c| *c == '@')? + line_start;
    if at > line_start && !chars[at - 1].is_whitespace() {
        return None;
    }
    let query: String = chars[at + 1..cursor].iter().collect();
    if query.contains('@') || query.contains('\t') {
        return None;
    }
    Some((at, cursor, query))
}
pub fn replace(buffer: &mut TextBuffer, start: usize, end: usize, link: &str) {
    buffer.replace_range(start, end, link);
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extracts_unicode_and_spaces() {
        let mut b = TextBuffer::new("Hi @café au".into());
        b.cursor = 11;
        assert_eq!(extract(&b).unwrap().2, "café au");
    }
    #[test]
    fn rejects_email() {
        let mut b = TextBuffer::new("x me@example".into());
        b.cursor = b.text().chars().count();
        assert!(extract(&b).is_none());
    }
}
