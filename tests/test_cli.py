import json

import pytest
from typer.testing import CliRunner

from tiny_reading_tracker import cli
from tiny_reading_tracker.models import ResolvedItem

runner = CliRunner()


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    for name in ("LIT_CONFIG", "LIT_DB", "LIT_VAULT", "LIT_TRANSLATOR_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = tmp_path / "vault"
    vault.mkdir()

    def fake_resolve(value, translator_url, timeout):
        if "broken" in value:
            raise ValueError("Metadata unavailable")
        title = "Attention Study" if "attention" in value else "Diffusion Study"
        return ResolvedItem(
            title=title,
            url=value,
            kind="paper",
            authors=["Ada Researcher"],
            identifiers=[("url", value)],
        )

    monkeypatch.setattr(cli, "resolve", fake_resolve)

    def run(*args, input=None):
        return runner.invoke(
            cli.app,
            ["--db", str(tmp_path / "library.db"), "--vault", str(vault), "--json", *args],
            input=input,
        )

    return run


def test_six_command_roundtrip(invoke):
    added = invoke("add", "https://example.org/diffusion", "--tag", "models")
    assert added.exit_code == 0, added.output
    item = json.loads(added.stdout)["results"][0]["item"]
    item_id = item["id"]
    listed = json.loads(invoke("ls").stdout)
    assert listed[0]["status"] == "unread"
    assert len(json.loads(invoke("search", "Ada").stdout)) == 1
    assert len(json.loads(invoke("search", "models").stdout)) == 1
    noted = invoke("note", item_id, "--text", "My first thought.")
    assert noted.exit_code == 0, noted.output
    assert invoke("read", "Diffusion").exit_code == 0
    assert json.loads(invoke("ls").stdout) == []
    read_item = json.loads(invoke("ls", "--read").stdout)[0]
    assert read_item["read_at"]
    assert read_item["note_path"]
    duplicate = invoke("add", "https://example.org/diffusion")
    assert json.loads(duplicate.stdout)["counts"]["exists"] == 1
    assert json.loads(invoke("ls", "--read").stdout)[0]["read_at"] == read_item["read_at"]
    assert invoke("unread", item_id).exit_code == 0
    assert json.loads(invoke("ls").stdout)[0]["read_at"] is None


def test_batch_stdin_partial_failure(invoke):
    result = invoke("add", input="https://example.org/diffusion\nhttps://example.org/broken\n")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["counts"] == {"added": 1, "exists": 0, "error": 1}
    assert len(json.loads(invoke("ls").stdout)) == 1


def test_ambiguous_read_changes_nothing(invoke):
    assert (
        invoke("add", "https://example.org/diffusion", "https://example.org/attention").exit_code
        == 0
    )
    result = invoke("read", "Study")
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
    assert len(json.loads(invoke("ls").stdout)) == 2


def test_bad_config_json(tmp_path):
    result = runner.invoke(cli.app, ["--json", "--config", str(tmp_path / "missing"), "ls"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)


def test_read_note_and_no_note_filter(invoke):
    invoke("add", "https://example.org/attention")
    result = invoke("read", "Attention", "--note", "Useful.")
    assert result.exit_code == 0, result.output
    assert json.loads(invoke("ls", "--read", "--no-note").stdout) == []


def test_note_uses_local_default_without_vault(tmp_path, monkeypatch):
    for name in ("LIT_CONFIG", "LIT_DB", "LIT_VAULT", "LIT_NOTES_DIR", "LIT_TRANSLATOR_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def fake_resolve(value, translator_url, timeout):
        return ResolvedItem(
            title="Portable Note",
            url=value,
            kind="paper",
            source="test",
            identifiers=[("url", value)],
        )

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    result = runner.invoke(
        cli.app,
        ["--db", str(tmp_path / "library.db"), "--json", "add", "https://example.org/portable"],
    )
    item_id = json.loads(result.stdout)["results"][0]["item"]["id"]
    noted = runner.invoke(
        cli.app, ["--db", str(tmp_path / "library.db"), "--json", "note", item_id]
    )
    assert noted.exit_code == 0, noted.output
    assert (
        str(tmp_path / "data/tiny-reading-tracker/notes") in json.loads(noted.stdout)["note_path"]
    )


def test_tui_launcher_forwards_resolved_notes_config(tmp_path, monkeypatch):
    binary = tmp_path / "lit-tui"
    binary.write_text("")
    monkeypatch.setenv("LIT_TUI_BINARY", str(binary))
    config = tmp_path / "settings.toml"
    config.write_text(
        'db = "configured.db"\nnotes_dir = "configured-notes"\n'
        'translator_url = "https://translator.example"\ntimeout = 7.5\n'
    )
    captured = {}

    def fake_run(args, env, check):
        captured.update(args=args, env=env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    result = runner.invoke(
        cli.app,
        [
            "--config",
            str(config),
            "tui",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["env"]["LIT_TUI_DB"] == str(tmp_path / "configured.db")
    assert captured["env"]["LIT_TUI_NOTES_DIR"] == str(tmp_path / "configured-notes")
    assert captured["env"]["LIT_TUI_TRANSLATOR_URL"] == "https://translator.example"
    assert captured["env"]["LIT_TUI_TIMEOUT"] == "7.5"


def test_duplicate_can_add_tags_without_network(invoke, monkeypatch):
    invoke("add", "https://example.org/attention")

    def no_network(*args):
        raise AssertionError("Duplicate must not need the translator")

    monkeypatch.setattr(cli, "resolve", no_network)
    result = invoke("add", "https://example.org/attention", "--tag", "new-tag")
    assert result.exit_code == 0, result.output
    assert len(json.loads(invoke("ls", "--tag", "new-tag").stdout)) == 1


def test_group_membership_lifecycle(invoke):
    created = invoke("group", "create", "Reading Club")
    assert created.exit_code == 0, created.output
    group_id = json.loads(created.stdout)["id"]
    invoke("group", "create", "Research")
    saved = invoke(
        "add", "https://example.org/attention", "--group", "Reading Club", "--group", "Research"
    )
    assert saved.exit_code == 0, saved.output
    item = json.loads(saved.stdout)["results"][0]["item"]
    assert {g["name"] for g in item["groups"]} == {"Reading Club", "Research"}
    invoke("add", "https://example.org/diffusion")
    assert len(json.loads(invoke("ls", "--group", group_id).stdout)) == 1
    assert len(json.loads(invoke("search", "Study", "--group", "Reading Club").stdout)) == 1
    assert invoke("group", "add", "Reading Club", "Diffusion").exit_code == 0
    assert invoke("group", "add", "Reading Club", "Diffusion").exit_code == 0
    assert len(json.loads(invoke("ls", "--group", group_id).stdout)) == 2
    renamed = invoke("group", "rename", group_id, "Favorites")
    assert json.loads(renamed.stdout)["id"] == group_id
    assert invoke("read", item["id"], "--note", "Keep this thought.").exit_code == 0
    assert len(json.loads(invoke("ls", "--group", "Favorites").stdout)) == 1
    assert len(json.loads(invoke("ls", "--all", "--group", "Favorites").stdout)) == 2
    assert invoke("group", "remove", "Favorites", item["id"]).exit_code == 0
    assert len(json.loads(invoke("ls", "--read", "--group", "Research").stdout)) == 1
    assert invoke("group", "delete", "Research").exit_code == 0
    remaining = json.loads(invoke("ls", "--read").stdout)[0]
    assert remaining["note_path"] and remaining["read_at"]
    assert remaining["groups"] == []
    assert len(json.loads(invoke("ls", "--all").stdout)) == 2


def test_unknown_group_prevents_network_and_writes(invoke, monkeypatch):
    def no_network(*args):
        raise AssertionError("Unknown groups must fail before resolution")

    monkeypatch.setattr(cli, "resolve", no_network)
    result = invoke("add", "https://example.org/attention", "--group", "Missing")
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
    assert json.loads(invoke("ls", "--all").stdout) == []
    assert invoke("ls", "--group", "Missing").exit_code == 1
    assert invoke("search", "Study", "--group", "Missing").exit_code == 1


def test_group_add_ambiguity_changes_no_memberships(invoke):
    invoke("group", "create", "Work")
    invoke("add", "https://example.org/attention", "https://example.org/diffusion")
    result = invoke("group", "add", "Work", "Attention", "Study")
    assert result.exit_code == 1
    assert json.loads(invoke("ls", "--all", "--group", "Work").stdout) == []


def test_duplicate_save_joins_new_group_without_network(invoke, monkeypatch):
    invoke("group", "create", "Work")
    invoke("group", "create", "Club")
    saved = invoke("add", "https://example.org/attention", "--group", "Work")
    item_id = json.loads(saved.stdout)["results"][0]["item"]["id"]
    invoke("read", item_id)

    def no_network(*args):
        raise AssertionError("Duplicate must not need the translator")

    monkeypatch.setattr(cli, "resolve", no_network)
    result = invoke("add", "https://example.org/attention", "--group", "Club")
    assert result.exit_code == 0, result.output
    item = json.loads(result.stdout)["results"][0]["item"]
    assert item["id"] == item_id and item["status"] == "read"
    assert {g["name"] for g in item["groups"]} == {"Work", "Club"}


def test_group_names_are_exact_and_case_insensitive(invoke):
    invoke("group", "create", "  Reading Club  ")
    assert invoke("group", "create", "READING CLUB").exit_code == 1
    assert invoke("group", "rename", "Club", "Renamed").exit_code == 1
    renamed = invoke("group", "rename", "reading club", "Renamed")
    assert renamed.exit_code == 0, renamed.output
    assert json.loads(invoke("group", "ls").stdout)[0]["name"] == "Renamed"
