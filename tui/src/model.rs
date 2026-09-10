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
    pub note_path: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub groups: Vec<Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Group {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ListResponse {
    pub version: u64,
    pub ok: bool,
    #[serde(default)]
    pub items: Vec<Item>,
    #[serde(default)]
    pub groups: Vec<Group>,
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

pub fn group_name(group: &Value) -> String {
    group
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}
