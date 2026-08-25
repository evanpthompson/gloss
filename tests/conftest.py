"""Shared fixtures.

`b_server` does all its work at import time — it resolves the provider, loads
the prep pack, builds the chain and freezes the system prompt — so a test
configures it by setting the environment and then importing it. Reloading is how
one interpreter gets several differently-configured servers.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fake_vendor import FakeVendor


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    """A prep pack the tests own.

    Built here rather than pointed at `sessions/example`, so a test never fails
    because someone edited a checked-in note, and so the byte count is known.
    """
    directory = tmp_path / "pack"
    directory.mkdir()
    (directory / "00-context.md").write_text("# Context\n\n" + "background. " * 200)
    (directory / "10-notes.md").write_text("# Notes\n\nKestrel is the deploy gate.\n")
    return directory


@pytest.fixture
def vendor() -> FakeVendor:
    v = FakeVendor()
    yield v
    v.stop()


@pytest.fixture
def build_server(monkeypatch: pytest.MonkeyPatch, pack: Path):
    """Import `b_server` under a chosen environment. Returns the module.

    Every key is set to a placeholder: these tests must never reach a real
    vendor, and must pass on a machine that has no credentials at all — which
    is what CI is.
    """

    def build(**env: str):
        defaults = {
            "GLOSS_SESSION": str(pack),
            "ANTHROPIC_API_KEY": "test-anthropic",
            "DEEPSEEK_API_KEY": "test-deepseek",
            "GEMINI_API_KEY": "test-gemini",
            "DEEPGRAM_API_KEY": "test-deepgram",
            "GLOSS_TIMEOUT_S": "5",
            # Port 0 so a test that reaches websockets.serve cannot collide
            # with a real b_server on the developer's machine.
            "B_SERVER_PORT": "0",
            "GLOSS_MAX_RETRIES": "0",
        }
        for key, value in {**defaults, **env}.items():
            monkeypatch.setenv(key, value)
        sys.modules.pop("b_server", None)
        return importlib.import_module("b_server")

    yield build
    sys.modules.pop("b_server", None)
