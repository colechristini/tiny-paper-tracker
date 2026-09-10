"""Opt-in live smoke test; requires the translation server and writes only temporary data.

Run: uv run python scripts/smoke_live.py
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="lit-smoke-") as directory:
        root = Path(directory)
        vault = root / "vault"
        vault.mkdir()
        env = os.environ.copy()
        for key in ("LIT_CONFIG", "LIT_DB", "LIT_VAULT"):
            env.pop(key, None)
        env["XDG_CONFIG_HOME"] = str(root / "config")

        def run(*args):
            result = subprocess.run(
                ["lit", "--db", str(root / "library.db"), "--vault", str(vault), "--json", *args],
                env=env,
                capture_output=True,
                text=True,
                timeout=150,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return json.loads(result.stdout)

        inputs = [
            "https://arxiv.org/abs/1706.03762",
            "https://arxiv.org/abs/1810.04805",
            "https://arxiv.org/abs/2005.14165",
            "https://arxiv.org/abs/2103.00020",
            "https://arxiv.org/abs/2203.02155",
            "https://arxiv.org/abs/2303.08774",
            "https://example.com",
            "https://www.zotero.org/support/quick_start_guide",
            "https://arxiv.org/abs/1706.03762v1",
            "https://arxiv.org/abs/1810.04805v1",
            "https://arxiv.org/abs/2005.14165v1",
        ]
        started = time.monotonic()
        added = run("add", *inputs, "--tag", "smoke")
        elapsed = time.monotonic() - started
        assert added["counts"] == {"added": 8, "exists": 3, "error": 0}, added["counts"]
        item = added["results"][0]["item"]
        assert item["title"] == "Attention Is All You Need", item["title"]
        assert len(run("ls")) == 8
        assert run("search", '"Attention Is All You Need"')[0]["id"] == item["id"]
        run("read", item["id"], "--note", "First smoke-test thought.")
        assert len(run("ls")) == 7
        run("note", item["id"], "--append", "Second smoke-test thought.")
        note = Path(run("note", item["id"])["note_path"])
        assert "First smoke-test thought." in note.read_text()
        assert "Second smoke-test thought." in note.read_text()
        duplicate = run("add", "10.48550/arXiv.1706.03762")
        assert duplicate["counts"]["exists"] == 1
        assert duplicate["results"][0]["item"]["status"] == "read"
        run("unread", item["id"])
        assert len(run("ls")) == 8
        print(
            json.dumps(
                {
                    "batch_inputs": len(inputs),
                    "counts": added["counts"],
                    "batch_seconds": round(elapsed, 2),
                    "six_command_workflow": "passed",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
