use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Item {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub authors: Vec<String>,
    pub venue: Option<String>,
    pub published_at: Option<String>,
    #[serde(default)]
    pub status: String,
    pub added_at: Option<String>,
    pub read_at: Option<String>,
    pub note_path: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub groups: Vec<Value>,
    #[serde(default)]
    pub identifiers: Vec<Value>,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Group {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub parent_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Section {
    pub id: Option<String>,
    pub title: String,
    #[serde(default)]
    pub item_ids: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ListResponse {
    pub version: u64,
    pub ok: bool,
    #[serde(default)]
    pub items: Vec<Item>,
    #[serde(default)]
    pub groups: Vec<Group>,
    #[serde(default)]
    pub sections: Vec<Section>,
    pub error: Option<ErrorBody>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MutationResponse {
    pub version: u64,
    pub ok: bool,
    pub item: Option<Item>,
    pub error: Option<ErrorBody>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ErrorBody {
    pub kind: String,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct NoteSnapshot {
    pub path: String,
    pub text: String,
    pub revision: String,
    pub created_at: Option<String>,
    pub modified_at: String,
}

pub fn group_name(group: &Value) -> String {
    group
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}
