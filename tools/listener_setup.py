"""Pick Laptop A's devices and Laptop B's address once, and write a launcher.

`a_listener.py` is configured entirely by environment variables, which is the
right shape for the program and the wrong shape for a person standing at a
laptop ten minutes before a call. This asks four questions, checks the answers,
and writes a script that sets them and starts the listener.

    uv run --with soundcard python tools/listener_setup.py

It runs standalone: one dependency, no repo checkout needed, same as
`a_listener.py` itself. The two files are the whole of Laptop A.

What it checks rather than assumes:

- **Bluetooth microphones are refused by default.** Opening a headset's mic
  drops the link from A2DP to HFP, which collapses output to ~16 kHz mono and
  degrades the *interviewer's* transcription — the channel that produces cards.
  The failure is invisible: your own audio still transcribes perfectly. See
  SPEC.md § The Bluetooth trap.
- **Laptop B is actually reachable** on the port before anything is written,
  because `ConnectionRefusedError` ten minutes before a call is not the moment
  to discover the server is not running.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

DEFAULT_PORT = 8765

# Names Windows and macOS give a headset when it is in hands-free mode. Matching
# on the name is crude, and it is what there is: the OS does not expose "this
# device will renegotiate your link codec" as a property.
BLUETOOTH_HINTS = (
    "hands-free", "handsfree", "hfp", "headset", "bluetooth", "airpods",
    "wh-1000", "wf-1000", "buds", "jabra", "poly bt", "beats",
)


def looks_bluetooth(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in BLUETOOTH_HINTS)


def reachable(host: str, port: int, timeout: float = 3.0) -> str | None:
    """None if the port accepts a connection, else why it did not."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def choose(kind: str, devices: list, warn_bluetooth: bool) -> object:
    print(f"\n{kind}:")
    for i, dev in enumerate(devices):
        flag = "  ← Bluetooth" if warn_bluetooth and looks_bluetooth(dev.name) else ""
        print(f"  [{i}] {dev.name}{flag}")
    while True:
        raw = input(f"Which {kind.lower()}? [0-{len(devices) - 1}] ").strip()
        if not raw.isdigit() or not (0 <= int(raw) < len(devices)):
            print("  Pick one of the numbers above.")
            continue
        picked = devices[int(raw)]
        if warn_bluetooth and looks_bluetooth(picked.name):
            print(
                "\n  That looks like a Bluetooth headset. Using its microphone drops\n"
                "  the link to HFP, which collapses playback to ~16 kHz mono and\n"
                "  degrades the INTERVIEWER's transcription — the channel that makes\n"
                "  cards. Your own voice will still sound fine, which is what makes\n"
                "  this worth refusing rather than warning about.\n"
                "  Use the built-in microphone and keep the headset on output only."
            )
            if input("  Use it anyway? [y/N] ").strip().lower() != "y":
                continue
        return picked


def launcher_text(mic: str, speaker: str, url: str, windows: bool) -> str:
    """The script a person runs before each call. Written rather than printed
    so the choices survive closing the terminal."""
    if windows:
        # .cmd rather than .ps1 deliberately. PowerShell refuses to run an
        # unsigned script under the default execution policy —
        # "running scripts is disabled on this system" — which turns a setup
        # tool into a second problem to solve. A .cmd has no such gate.
        #
        # `set "VAR=value"` rather than `set VAR="value"`: the second form puts
        # the quotes *inside* the value, and Windows device names like
        # `Microphone Array (Realtek(R) Audio)` need the quoting to survive
        # both the spaces and the parentheses.
        return (
            "@echo off\n"
            "REM Written by tools/listener_setup.py - re-run it to change these.\n"
            f'set "A_LISTENER_MIC_NAME={mic}"\n'
            f'set "A_LISTENER_SPEAKER_NAME={speaker}"\n'
            f'set "B_SERVER_URL={url}"\n'
            "uv run --with soundcard --with websockets --with numpy a_listener.py\n"
        )
    return (
        "#!/usr/bin/env bash\n"
        "# Written by tools/listener_setup.py — re-run it to change these.\n"
        f'export A_LISTENER_MIC_NAME="{mic}"\n'
        f'export A_LISTENER_SPEAKER_NAME="{speaker}"\n'
        f'export B_SERVER_URL="{url}"\n'
        "exec uv run --with soundcard --with websockets --with numpy a_listener.py\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", help="Laptop B's LAN address (skips the prompt)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out", type=Path, help="where to write the launcher")
    args = ap.parse_args()

    try:
        import soundcard as sc
    except ImportError:
        print("soundcard is not installed. Run this as:\n"
              "  uv run --with soundcard python tools/listener_setup.py", file=sys.stderr)
        return 1

    mics = sc.all_microphones(include_loopback=False)
    speakers = sc.all_speakers()
    if not mics or not speakers:
        print("No audio devices found. Nothing to configure.", file=sys.stderr)
        return 1

    print("Laptop A captures two things: your voice from a microphone, and the")
    print("interviewer's voice as loopback off an output device.")

    mic = choose("Microphone (your voice)", mics, warn_bluetooth=True)
    speaker = choose("Output device (the interviewer's voice)", speakers, warn_bluetooth=False)

    host = args.host or input("\nLaptop B's LAN address (e.g. 192.168.1.179): ").strip()
    if not host:
        print("No address given, so nothing was written.", file=sys.stderr)
        return 1

    print(f"\nChecking {host}:{args.port} ...")
    why = reachable(host, args.port)
    if why:
        print(f"  Cannot reach it — {why}\n"
              "  Start b_server.py on Laptop B first, and check both machines are on\n"
              "  the same network. Nothing was written; re-run when it answers.",
              file=sys.stderr)
        return 1
    print("  reachable.")

    windows = os.name == "nt"
    out = args.out or Path("run_listener.cmd" if windows else "run_listener.sh")
    out.write_text(launcher_text(mic.name, speaker.name, f"ws://{host}:{args.port}", windows))
    if not windows:
        out.chmod(0o755)

    # Built outside the f-string: a backslash inside one is a syntax error
    # before Python 3.12, and this project supports 3.11.
    invocation = (".\\" if windows else "./") + out.name
    print(f"\nWrote {out}. Start the listener with:\n  {invocation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
