"""Shared test fixtures.

The important one is `block_network`: the test suite must never reach
OpenRouter. Quota is a first-class constraint (PLAN.md §2.4) and live runs
happen only on an explicit instruction — not by accident from a test someone
wrote in a hurry. `httpx.MockTransport` is unaffected, so fakes still work.
"""

from __future__ import annotations

import socket

import httpx
import pytest


class NetworkCallInTests(RuntimeError):
    """A test tried to open a real connection."""


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    async def blocked_async(self, request, *args, **kwargs):
        raise NetworkCallInTests(
            f"Test attempted a real HTTP request to {request.url}. "
            "Use httpx.MockTransport instead — the suite must never spend quota."
        )

    def blocked_sync(self, request, *args, **kwargs):
        raise NetworkCallInTests(
            f"Test attempted a real HTTP request to {request.url}. "
            "Use httpx.MockTransport instead — the suite must never spend quota."
        )

    def blocked_socket(*args, **kwargs):
        raise NetworkCallInTests("Test attempted to open a socket.")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync)
    monkeypatch.setattr(socket, "create_connection", blocked_socket)
