"""The parts of Laptop A's setup tool that can be tested without a sound card.

Device enumeration needs hardware and is not tested here. The two things that
can go wrong silently are: mistaking a Bluetooth headset for an ordinary
microphone, and writing a launcher that does not set what `a_listener.py`
actually reads.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from listener_setup import launcher_text, looks_bluetooth, reachable

# --- the Bluetooth trap -----------------------------------------------------


@pytest.mark.parametrize("name", [
    "Headset (WH-1000XM4 Hands-Free AG Audio)",
    "Headset Microphone (Bluetooth)",
    "AirPods Pro",
    "Jabra Evolve 65",
    "HFP Microphone",
    "Galaxy Buds Pro",
])
def test_a_headset_is_recognised(name: str) -> None:
    """Opening one of these drops the link to HFP, which degrades the
    interviewer's channel while the user's own audio still sounds perfect —
    so it has to be caught by name, before the run, or not at all."""
    assert looks_bluetooth(name)


@pytest.mark.parametrize("name", [
    "Microphone Array (Realtek(R) Audio)",
    "MacBook Pro Microphone",
    "Blue Yeti",
    "Line In (High Definition Audio)",
])
def test_a_wired_or_built_in_microphone_is_not(name: str) -> None:
    assert not looks_bluetooth(name)


def test_matching_ignores_case() -> None:
    assert looks_bluetooth("BLUETOOTH HANDS-FREE")


# --- the launcher -----------------------------------------------------------


def test_the_launcher_sets_every_variable_a_listener_reads() -> None:
    """A launcher missing one of these fails in a different way for each: no
    mic name silently uses the Bluetooth default, no speaker name captures
    whatever was plugged in last, no URL points at the Phase 1 address."""
    text = launcher_text("Mic Array", "Speakers", "ws://192.168.1.179:8765", windows=True)
    for var in ("A_LISTENER_MIC_NAME", "A_LISTENER_SPEAKER_NAME", "B_SERVER_URL"):
        assert var in text


def test_windows_and_posix_launchers_use_their_own_syntax() -> None:
    cmd = launcher_text("m", "s", "ws://h:1", windows=True)
    sh = launcher_text("m", "s", "ws://h:1", windows=False)
    assert 'set "A_LISTENER_MIC_NAME=m"' in cmd and "export" not in cmd
    assert cmd.startswith("@echo off")
    assert 'export A_LISTENER_MIC_NAME="m"' in sh and "set \"" not in sh
    assert sh.startswith("#!/usr/bin/env bash")


def test_the_windows_launcher_is_not_a_powershell_script() -> None:
    """PowerShell refuses to run an unsigned .ps1 under the default execution
    policy — "running scripts is disabled on this system" — which turns the
    setup tool into a second problem. A .cmd has no such gate, so nothing here
    may use PowerShell syntax."""
    cmd = launcher_text("m", "s", "ws://h:1", windows=True)
    assert "$env:" not in cmd


def test_device_names_with_brackets_survive_quoting() -> None:
    """Windows device names routinely contain parentheses — `Microphone Array
    (Realtek(R) Audio)` — and cmd would otherwise treat them as syntax. The
    `set "VAR=value"` form is what keeps them inside the value; `set VAR="v"`
    would put the quotes in the value instead."""
    name = "Microphone Array (Realtek(R) Audio)"
    assert f'set "A_LISTENER_MIC_NAME={name}"' in launcher_text(name, "s", "ws://h:1", windows=True)
    assert f'"{name}"' in launcher_text(name, "s", "ws://h:1", windows=False)


def test_the_launcher_actually_starts_the_listener() -> None:
    assert "a_listener.py" in launcher_text("m", "s", "ws://h:1", windows=True)


# --- reachability -----------------------------------------------------------


def test_an_open_port_reports_no_problem() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        assert reachable(*server.getsockname()) is None


def test_a_closed_port_says_why_rather_than_just_failing() -> None:
    """The message is the whole point: 'start b_server.py first' is actionable,
    a bare False is not."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    why = reachable("127.0.0.1", port, timeout=1.0)
    assert why and "Error" in why
