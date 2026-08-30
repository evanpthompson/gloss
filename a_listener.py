"""
Phase 1, Laptop A: capture two audio sources and ship raw PCM to Laptop B.
No processing, no API keys — this script never talks to Deepgram or Anthropic
directly.

**Laptop A is a role, not a platform.** `soundcard` backs onto WASAPI on
Windows, CoreAudio on macOS and PulseAudio on Linux, so this script runs on
all three; what differs is how the interviewer's voice is captured, because
loopback is the part operating systems disagree about. Windows exposes render
loopback directly, Linux exposes it as a PulseAudio monitor source, and macOS
has no OS-level loopback — it needs a virtual device (BlackHole or similar)
first. Android cannot run this file at all, but it can be Laptop A: what B
requires is two websocket streams of 16-bit mono PCM, and anything that can
capture and send that qualifies. The contract is the portable part; this
script is one implementation of it.

- WASAPI loopback off the default speaker -> the interviewer's voice.
- The laptop's built-in mic -> the user's voice. Deliberately NOT the
  Bluetooth headset's mic: opening it drops the BT link to HFP and
  collapses output to ~16kHz mono, degrading transcription of both sides.
  Keep the headset on output-only (A2DP) by never opening its mic.

Run: uv run a_listener.py   (or: python a_listener.py)
Requires: B_SERVER_URL pointing at Laptop B, e.g. ws://192.168.1.50:8765
"""

import asyncio
import os

import numpy as np
import soundcard as sc
import websockets

B_SERVER_URL = os.environ.get("B_SERVER_URL", "ws://192.168.1.100:8765")
SAMPLE_RATE = int(os.environ.get("A_LISTENER_SAMPLE_RATE", "48000"))
CHUNK_FRAMES = int(os.environ.get("A_LISTENER_CHUNK_FRAMES", "4800"))  # ~100ms at 48kHz

# Substring to pick a specific input device instead of the OS default mic
# (e.g. "Realtek"). Leave unset to use the default microphone.
MIC_NAME_SUBSTRING = os.environ.get("A_LISTENER_MIC_NAME")


def to_pcm16(frames: np.ndarray) -> bytes:
    mono = frames[:, 0]
    return (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def interviewer_recorder():
    speaker = sc.default_speaker()
    loopback_mic = sc.get_microphone(speaker.id, include_loopback=True)
    return loopback_mic.recorder(samplerate=SAMPLE_RATE, channels=1)


def user_recorder():
    if MIC_NAME_SUBSTRING:
        mic = sc.get_microphone(MIC_NAME_SUBSTRING, include_loopback=False)
    else:
        mic = sc.default_microphone()
    return mic.recorder(samplerate=SAMPLE_RATE, channels=1)


async def capture(recorder_factory, url: str, label: str) -> None:
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                print(f"[{label}] connected to {url}")
                with recorder_factory() as recorder:
                    while True:
                        frames = await asyncio.to_thread(recorder.record, CHUNK_FRAMES)
                        await ws.send(to_pcm16(frames))
        except Exception as exc:
            print(f"[{label}] error: {exc!r} — reconnecting in 2s")
            await asyncio.sleep(2)


async def main() -> None:
    print(f"interviewer input : {sc.default_speaker().name} (loopback)")
    print(f"user input        : {MIC_NAME_SUBSTRING or sc.default_microphone().name}")
    await asyncio.gather(
        capture(interviewer_recorder, f"{B_SERVER_URL}/interviewer", "interviewer"),
        capture(user_recorder, f"{B_SERVER_URL}/user", "user"),
    )


if __name__ == "__main__":
    asyncio.run(main())
