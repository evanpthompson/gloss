"""
Phase 1: prove the pipe.

Accepts two labeled raw-PCM WebSocket streams from Laptop A
(ws://<this-machine>:8765/interviewer and .../user), relays each to its own
Deepgram streaming connection, and prints labeled transcript lines with
elapsed time so end-to-end latency can be eyeballed.

No enrichment, no display, no LLM calls — that's Phase 2+.
"""

import asyncio
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()  # must run before importing deepgram — it reads DEEPGRAM_API_KEY
# via os.getenv() as a default *argument value*, evaluated once at import time.

import websockets
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.environment import DeepgramClientEnvironment
from deepgram.listen.v1.types import ListenV1Results

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("b_server")

HOST = os.environ.get("B_SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("B_SERVER_PORT", "8765"))
SAMPLE_RATE = int(os.environ.get("B_SERVER_SAMPLE_RATE", "48000"))
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")

# Testability hook: point the Deepgram client at a mock endpoint instead of
# production, e.g. ws://mock-deepgram:9010, for offline/CI end-to-end
# testing. Unset in normal use — real Deepgram is the default.
DEEPGRAM_WS_URL = os.environ.get("DEEPGRAM_WS_URL")
if DEEPGRAM_WS_URL:
    _http_url = DEEPGRAM_WS_URL.replace("ws://", "http://").replace("wss://", "https://")
    DEEPGRAM_ENVIRONMENT = DeepgramClientEnvironment(
        base=_http_url,
        production=DEEPGRAM_WS_URL,
        agent=DEEPGRAM_WS_URL,
        agent_rest=_http_url,
    )
else:
    DEEPGRAM_ENVIRONMENT = DeepgramClientEnvironment.PRODUCTION

CHANNELS = {"interviewer", "user"}


async def bridge(a_ws: websockets.ServerConnection, label: str) -> None:
    client = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY, environment=DEEPGRAM_ENVIRONMENT)
    started = time.monotonic()

    async with client.listen.v1.connect(
        model="nova-3",
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
        channels=1,
        endpointing=300,
        smart_format=True,
        tag=label,
    ) as dg:

        def on_message(message: object) -> None:
            if not isinstance(message, ListenV1Results):
                return
            alternatives = message.channel.alternatives if message.channel else []
            transcript = alternatives[0].transcript if alternatives else ""
            if not transcript:
                return
            tag = "FINAL" if message.speech_final else "final" if message.is_final else "interim"
            elapsed = time.monotonic() - started
            log.info("[%s/%s +%5.1fs] %s", label, tag, elapsed, transcript)

        dg.on(EventType.OPEN, lambda _: log.info("[%s] Deepgram connection open", label))
        dg.on(EventType.MESSAGE, on_message)
        dg.on(EventType.ERROR, lambda err: log.error("[%s] Deepgram error: %s", label, err))
        dg.on(EventType.CLOSE, lambda _: log.info("[%s] Deepgram connection closed", label))

        listen_task = asyncio.create_task(dg.start_listening())
        try:
            async for chunk in a_ws:
                if isinstance(chunk, bytes):
                    await dg.send_media(chunk)
        finally:
            await dg.send_close_stream()
            await asyncio.sleep(1.0)  # let trailing results arrive
            listen_task.cancel()


async def handler(a_ws: websockets.ServerConnection) -> None:
    label = a_ws.request.path.strip("/")
    if label not in CHANNELS:
        log.warning("Rejecting connection on unknown path: %s", a_ws.request.path)
        await a_ws.close(code=4404, reason="unknown channel")
        return

    log.info("[%s] Laptop A connected", label)
    try:
        await bridge(a_ws, label)
    except Exception:
        log.exception("[%s] bridge failed", label)
    finally:
        log.info("[%s] Laptop A disconnected", label)


async def main() -> None:
    if not os.environ.get("DEEPGRAM_API_KEY"):
        raise SystemExit("DEEPGRAM_API_KEY is not set — put it in .env (see .env.example)")

    async with websockets.serve(handler, HOST, PORT, max_size=None):
        log.info("Listening on ws://%s:%s/{interviewer,user}", HOST, PORT)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
