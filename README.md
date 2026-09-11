# hello-otel

A single-container FastAPI service that produces a real distributed trace — by calling itself.

`POST /v1/chat` does actual HTTP work against `POST /v1/echo` in the same process. The outbound call carries a W3C `traceparent` header, so one request yields a **client span and a downstream server span under a single trace id**. You get a genuinely distributed trace to look at without deploying a second service, a database, or a message broker.

Tracing stays off unless you ask for it. With `OTEL_EXPORTER_OTLP_ENDPOINT` unset there is no provider, no exporter and no background thread — the service runs as a plain FastAPI app.

## Quick start

Two containers, no compose file. Jaeger accepts OTLP directly and gives you a UI to look at:

```bash
docker network create otel-demo

docker run -d --name jaeger --network otel-demo -p 16686:16686 \
  jaegertracing/all-in-one:latest

docker run -d --name hello-otel --network otel-demo -p 8000:8000 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317 \
  -e OTEL_SERVICE_NAME=hello-otel \
  ghcr.io/qinrui777/hello-otel:main
```

Make a request:

```bash
curl -X POST localhost:8000/v1/chat \
  -H 'content-type: application/json' \
  -d '{"message":"hello otel"}'
```

The response hands back the trace id, so you can go straight to it:

```json
{"reply": "HELLO OTEL", "trace_id": "0f12d870c7a371d29eb2add62ad70f12"}
```

Open <http://localhost:16686>, pick the `hello-otel` service, and you'll find six spans:

```
POST /v1/chat                       Server     the request arrives
├── chat.plan                       Internal
├── POST                            Client    ─┐  traceparent header
│   └── POST /v1/echo               Server    ─┘  same trace, over real HTTP
│       └── echo.transform          Internal
└── chat.render                     Internal
```

Clean up with `docker rm -f jaeger hello-otel && docker network rm otel-demo`.

## Endpoints

| Method | Path        | What it does                                                                  |
|--------|-------------|-------------------------------------------------------------------------------|
| `GET`  | `/healthz`  | Liveness. Deliberately excluded from tracing — probes are noise                |
| `POST` | `/v1/chat`  | The showcase: nested manual spans wrapped around a real call to `/v1/echo`     |
| `POST` | `/v1/echo`  | The downstream leg, reached over HTTP rather than in-process                   |
| `GET`  | `/v1/boom`  | Fails on purpose, so the demo also shows an `ERROR` span with an exception event |

Interactive docs are at <http://localhost:8000/docs>.

## Configuration

Every variable below is a **standard OpenTelemetry variable read by the SDK itself**, not by this app's code. That is why [`hello_otel/tracing.py`](hello_otel/tracing.py) is under fifty lines and hardcodes nothing, and why the same image drops into any collector setup unchanged.

| Variable                      | Default                 | Purpose                                                                    |
|-------------------------------|-------------------------|----------------------------------------------------------------------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset                   | OTLP/gRPC collector address. **Unset disables tracing entirely**            |
| `OTEL_SERVICE_NAME`           | `unknown_service`       | Service name shown in your backend                                          |
| `OTEL_RESOURCE_ATTRIBUTES`    | unset                   | Extra resource attributes, e.g. `deployment.environment=local`              |
| `OTEL_TRACES_SAMPLER`         | `parentbased_always_on` | Head-based sampling, e.g. `parentbased_traceidratio` with `..._ARG=0.1`     |
| `SELF_URL`                    | `http://127.0.0.1:8000` | Where `/v1/chat` reaches this same app. The only non-standard variable      |

See [`.env.example`](.env.example).

### Pointing at something other than Jaeger

Anything speaking OTLP/gRPC on port 4317 works — an OpenTelemetry Collector, Grafana Alloy, or a vendor endpoint. For AWS X-Ray, run the [ADOT Collector](https://aws-otel.github.io/docs/getting-started/x-ray) as a sidecar with its `awsxray` exporter and point `OTEL_EXPORTER_OTLP_ENDPOINT` at it.

## Running from source

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/uvicorn hello_otel.main:app --reload
```

The tests run against a real uvicorn server on a random port rather than an in-process ASGI transport, because the self-call is the whole point and an in-process transport would not exercise it.

## Images

Published to the GitHub Container Registry for `linux/amd64` and `linux/arm64`:

| Tag              | Points at                          |
|------------------|------------------------------------|
| `main`           | Latest commit on the default branch |
| `1.2.3`, `1.2`   | A `v1.2.3` release tag              |
| `sha-<short>`    | One specific commit                 |

[`.github/workflows/publish.yml`](.github/workflows/publish.yml) runs the tests first and only publishes if they pass. Pull requests build the image but never push, so the Dockerfile stays verified without publishing a tag per proposed change.

> **First run only:** a package published to GHCR inherits its repository's *access permissions* but [**not** its visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility), so it starts out private even from a public repo. After the first successful workflow run, go to the package page → **Package settings** → **Change visibility** → **Public**. No workflow setting can do this, and the change cannot be undone.

## How it is built

| Choice                                  | Why                                                                                                                |
|-----------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Programmatic setup, not `opentelemetry-instrument` | The `TracerProvider` / `BatchSpanProcessor` / `Resource` wiring is the part worth reading. The zero-code agent hides it |
| Digest-pinned base image                | A rebuild months from now gets the same base, not whatever `:3.13-slim` points at that day                          |
| Runs as uid 10001                       | Nothing here needs root, so the image should not hand it to anyone who gets in                                      |
| ASGI `http send` / `http receive` spans excluded | Protocol plumbing, not application behaviour. Excluding them halves the span count of every trace           |
| `trust_env=False` on the self-call      | A loopback request should never be routed through a `HTTP_PROXY` picked up from the environment                     |

Built on the patterns in the [official OpenTelemetry demo](https://github.com/open-telemetry/opentelemetry-demo) — see its [`src/agent`](https://github.com/open-telemetry/opentelemetry-demo/tree/main/src/agent) FastAPI service — and the [OpenTelemetry Python exporter docs](https://opentelemetry.io/docs/languages/python/exporters/).

## License

MIT — see [LICENSE](LICENSE).
