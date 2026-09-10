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


def test_duplicate_can_add_tags_without_network(invoke, monkeypatch):
    invoke("add", "https://example.org/attention")

    def no_network(*args):
        raise AssertionError("Duplicate must not need the translator")

    monkeypatch.setattr(cli, "resolve", no_network)
    result = invoke("add", "https://example.org/attention", "--tag", "new-tag")
    assert result.exit_code == 0, result.output
    assert len(json.loads(invoke("ls", "--tag", "new-tag").stdout)) == 1
