"""Smoke tests.

These run against a real uvicorn server rather than an in-process ASGI
transport, because /v1/chat calls the app back over the network - the whole
point of the demo - and an in-process transport would not exercise that path.
"""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from hello_otel.main import app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def base_url() -> str:
    port = _free_port()
    # The app calls itself at SELF_URL, so it has to agree with where we bind.
    import hello_otel.main as main

    main.SELF_URL = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.started:
            return main.SELF_URL
        time.sleep(0.05)
    raise RuntimeError("uvicorn did not start within 10s")


@pytest.fixture(scope="session")
def client(base_url: str) -> httpx.Client:
    # trust_env=False so a proxy configured in the developer's shell is not
    # applied to requests aimed at 127.0.0.1.
    return httpx.Client(base_url=base_url, timeout=10.0, trust_env=False)


def test_healthz(client: httpx.Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_echo_uppercases_steps(client: httpx.Client) -> None:
    response = client.post("/v1/echo", json={"steps": ["a", "b"]})
    assert response.status_code == 200
    assert response.json()["steps"] == ["A", "B"]


def test_chat_round_trips_through_echo(client: httpx.Client) -> None:
    response = client.post("/v1/chat", json={"message": "hello otel"})
    assert response.status_code == 200

    body = response.json()
    assert body["reply"] == "HELLO OTEL"
    # 32 hex characters whether or not tracing is enabled; all zeroes when it is not.
    assert len(body["trace_id"]) == 32


def test_boom_fails_deliberately(client: httpx.Client) -> None:
    assert client.get("/v1/boom").status_code == 500
