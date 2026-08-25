"""A local stand-in for a model vendor, speaking both wire formats gloss uses.

Integration tests need real SDK exception objects and real response parsing —
that is the layer where the interesting bugs live, and a mocked `ainvoke` would
skip straight past it. They must not need network access or a funded account,
because CI has neither.

So this serves the two dialects on one port and picks between them by path:
`/v1/messages` is Anthropic, `/chat/completions` is OpenAI-compatible (DeepSeek).
Point `ANTHROPIC_BASE_URL` and `DEEPSEEK_API_BASE` at it and both links in the
chain run against it, through their own SDKs, unmodified.

Responses are queued. An empty queue answers with zero cards, so a test only has
to arrange the calls it actually cares about — preflight and quiet turns take
care of themselves.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Reply:
    """One queued response. Either cards, or a failure."""

    status: int = 200
    cards: list[dict] | None = None
    error_type: str = "invalid_request_error"
    message: str = "staged failure"
    headers: dict[str, str] = field(default_factory=dict)


def _anthropic_body(cards: list[dict]) -> dict:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_fake",
                "name": "emit_cards",
                "input": {"cards": cards},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def _openai_body(cards: list[dict]) -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_fake",
                            "type": "function",
                            "function": {
                                "name": "emit_cards",
                                "arguments": json.dumps({"cards": cards}),
                            },
                        }
                    ],
                },
                "logprobs": None,
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


class FakeVendor:
    """A vendor that answers however the test tells it to, and counts calls."""

    def __init__(self) -> None:
        self.replies: deque[Reply] = deque()
        self.calls: list[str] = []  # "anthropic" / "deepseek", in order
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    # --- arrangement ------------------------------------------------------
    def enqueue(self, reply: Reply) -> FakeVendor:
        self.replies.append(reply)
        return self

    def fail(self, status: int, error_type: str = "invalid_request_error",
             message: str = "staged failure", headers: dict[str, str] | None = None,
             times: int = 1) -> FakeVendor:
        for _ in range(times):
            self.enqueue(Reply(status=status, error_type=error_type,
                               message=message, headers=headers or {}))
        return self

    def cards(self, *cards: dict, times: int = 1) -> FakeVendor:
        for _ in range(times):
            self.enqueue(Reply(status=200, cards=list(cards)))
        return self

    # --- inspection -------------------------------------------------------
    def calls_to(self, vendor: str) -> int:
        return self.calls.count(vendor)

    # --- lifecycle --------------------------------------------------------
    def start(self) -> str:
        vendor = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                dialect = "anthropic" if "messages" in self.path else "deepseek"
                with vendor._lock:
                    vendor.calls.append(dialect)
                    reply = vendor.replies.popleft() if vendor.replies else Reply(cards=[])

                if reply.status == 200:
                    cards = reply.cards or []
                    body = _anthropic_body(cards) if dialect == "anthropic" else _openai_body(cards)
                else:
                    # Both vendors nest a machine-readable `type` under `error`.
                    body = {
                        "type": "error",
                        "error": {"type": reply.error_type, "message": reply.message},
                    }
                raw = json.dumps(body).encode()
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: object) -> None:
                pass  # keep pytest output readable

        # Threading, not the plain HTTPServer: with HTTP/1.1 keep-alive the
        # SDK's httpx client holds the connection open between calls, and a
        # single-threaded server sits blocked inside handle() reading the next
        # request — so shutdown() never returns and teardown hangs. Daemon
        # threads so a stuck handler cannot outlive the test run either.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
