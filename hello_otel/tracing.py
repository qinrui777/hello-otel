"""OpenTelemetry wiring for the service.

Every knob here is a standard ``OTEL_*`` environment variable read by the SDK
itself, so this module hardcodes nothing and the same image drops into any
collector setup unchanged.

Tracing stays off entirely unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set: with
no endpoint there is no provider, no exporter and no background thread, and the
service runs as a plain FastAPI app.
"""

import logging
import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = logging.getLogger(__name__)

# Liveness probes fire every few seconds and describe nothing a human wants to
# read. Excluding them keeps the trace data about real requests.
EXCLUDED_URLS = "healthz"

# The ASGI instrumentation emits a span per "http.send" / "http.receive" protocol
# event. They are protocol plumbing, not application behaviour, and they roughly
# double the span count of every trace. Drop them so the timeline reads cleanly.
EXCLUDED_SPANS = ["receive", "send"]


def setup(app: FastAPI) -> bool:
    """Instrument ``app``. Returns whether tracing was actually enabled."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        log.info("OTEL_EXPORTER_OTLP_ENDPOINT is unset - tracing disabled")
        return False

    # Resource.create() picks up OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES,
    # and TracerProvider() resolves its sampler from OTEL_TRACES_SAMPLER. Passing
    # them explicitly here would only shadow the operator's configuration.
    provider = TracerProvider(resource=Resource.create())

    # OTLPSpanExporter() likewise reads the endpoint, headers, timeout and TLS
    # settings from the OTEL_EXPORTER_OTLP_* variables.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=EXCLUDED_URLS, exclude_spans=EXCLUDED_SPANS
    )
    HTTPXClientInstrumentor().instrument()

    log.info("tracing enabled - exporting OTLP/gRPC to %s", endpoint)
    return True
