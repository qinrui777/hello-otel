"""A FastAPI service that produces a genuinely distributed trace on its own.

``POST /v1/chat`` does real HTTP work against ``POST /v1/echo`` on this same
process. That is deliberate: the outbound call carries a W3C ``traceparent``
header, so a single request yields a client span *and* a downstream server span
under one trace id - the thing worth looking at in a tracing backend - without
a second service to deploy or a database to stand up.
"""

import asyncio
import logging
import os
import random

import httpx
from fastapi import FastAPI
from opentelemetry import trace
from pydantic import BaseModel, Field

from hello_otel import tracing

logging.basicConfig(level=logging.INFO)

# Where /v1/chat reaches this same app over the network. The default is correct
# inside the container, where uvicorn binds all interfaces on port 8000.
SELF_URL = os.getenv("SELF_URL", "http://127.0.0.1:8000")

app = FastAPI(
    title="hello-otel",
    description="A single-container FastAPI service that traces a call to itself.",
)
TRACING_ENABLED = tracing.setup(app)

tracer = trace.get_tracer("hello-otel")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, examples=["hello otel"])


class EchoRequest(BaseModel):
    steps: list[str]


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness. Excluded from tracing - see hello_otel.tracing.EXCLUDED_URLS."""
    return {"status": "ok", "tracing": TRACING_ENABLED}


@app.post("/v1/chat")
async def chat(request: ChatRequest) -> dict:
    """The showcase: nested manual spans wrapped around a real outbound call."""
    root = trace.get_current_span()
    root.set_attribute("hello.chat.message_length", len(request.message))

    with tracer.start_as_current_span("chat.plan") as span:
        steps = request.message.split()
        span.set_attribute("hello.chat.step_count", len(steps))
        # Stand in for the work a real handler would do, so the span has a
        # duration worth rendering on a timeline.
        await asyncio.sleep(random.uniform(0.01, 0.04))

    # The interesting hop. httpx is instrumented, so this emits a client span and
    # injects traceparent; the /v1/echo handler below picks it up and becomes a
    # child server span in the same trace.
    # trust_env=False: this is a loopback call, so any HTTP_PROXY/ALL_PROXY in the
    # environment would be wrong to honour and would fail in confusing ways.
    async with httpx.AsyncClient(
        base_url=SELF_URL, timeout=5.0, trust_env=False
    ) as client:
        response = await client.post("/v1/echo", json={"steps": steps})
        response.raise_for_status()
        echoed = response.json()["steps"]

    with tracer.start_as_current_span("chat.render"):
        reply = " ".join(echoed)

    # Handing back the trace id lets you paste it straight into your backend.
    trace_id = format(root.get_span_context().trace_id, "032x")
    return {"reply": reply, "trace_id": trace_id}


@app.post("/v1/echo")
async def echo(request: EchoRequest) -> dict:
    """The downstream leg of /v1/chat, reached over HTTP rather than in-process."""
    with tracer.start_as_current_span("echo.transform") as span:
        steps = [step.upper() for step in request.steps]
        span.set_attribute("hello.echo.step_count", len(steps))
    return {"steps": steps}


@app.get("/v1/boom")
async def boom() -> dict:
    """Fail on purpose, so the demo shows what a broken span looks like.

    start_as_current_span records the exception as a span event and sets the span
    status to ERROR before re-raising, which is what marks the trace as faulted.
    """
    with tracer.start_as_current_span("boom.explode") as span:
        span.set_attribute("hello.boom.deliberate", True)
        raise RuntimeError("deliberate failure, so the error path has a trace too")
