# A local lab for learning OpenTelemetry

The [quick start](../README.md#quick-start) in the README points `hello-otel` at Jaeger and stops there. It is the fastest way to see a trace, and it deliberately hides everything interesting.

This document builds the other thing: a local observability stack you can take apart. It runs entirely on your laptop, costs nothing, and ends with metrics, traces and logs that link to each other.

Everything below was run end to end on Docker 29.7.2 / macOS, against Collector `v0.160.0` and `grafana/otel-lgtm:latest`.

## What you will end up with

```
┌──────────────┐  OTLP/gRPC  ┌────────────────────┐  OTLP/gRPC  ┌──────────────────────┐
│  hello-otel  │────────────▶│   otelcol-contrib  │────────────▶│      otel-lgtm       │
│              │    :4317    │                    │    :4317    │                      │
│  your app    │             │  YOUR config.yaml  │             │  Prometheus  :9090   │
└──────────────┘             │  ┌──────────────┐  │             │  Tempo               │
                             │  │ span_metrics │  │             │  Loki                │
                             │  │ tail_sampling│  │             │  Grafana     :3000   │
                             │  └──────────────┘  │             └──────────────────────┘
                             └─────────┬──────────┘
                                       │ debug exporter
                                       ▼
                                raw spans, in your terminal
```

The middle box is the point. Stage 1 skips it to get you a trace in five minutes; stage 2 puts it back and is where the actual learning happens.

**Cost:** about 1 GB of RAM and 4 GB of disk. Measured on an idle lab: `lgtm` 836 MiB, `otelcol` 47 MiB, `hello-otel` 89 MiB.

---

## Stage 1 — a backend in one container

`grafana/otel-lgtm` exists for exactly this: Prometheus, Tempo, Loki, Pyroscope, Grafana and a Collector, in one image, with the Grafana datasources already wired together. It is built for development and demos, so do not reach for it in production.

```bash
docker network create otel-lab

docker run -d --name lgtm --network otel-lab \
  -p 3000:3000 -p 4317:4317 -p 9090:9090 \
  grafana/otel-lgtm:latest
```

| Port   | What it is                                     |
|--------|------------------------------------------------|
| `3000` | Grafana — log in with `admin` / `admin`        |
| `4317` | OTLP gRPC, straight into the bundled Collector |
| `9090` | Prometheus, if you want to write raw PromQL    |

Wait for `docker ps` to report `(healthy)`, then point the app at it:

```bash
docker run -d --name hello-otel --network otel-lab -p 8000:8000 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://lgtm:4317 \
  -e OTEL_SERVICE_NAME=hello-otel \
  -e OTEL_RESOURCE_ATTRIBUTES=deployment.environment=local \
  ghcr.io/qinrui777/hello-otel:main

curl -X POST localhost:8000/v1/chat \
  -H 'content-type: application/json' \
  -d '{"message":"hello otel"}'
```

Open <http://localhost:3000>, go to **Explore → Tempo → Search**, and pick the trace. Six spans, one of them reached over real HTTP.

**What to actually look at here:** how a parent/child relationship is recorded, what resource attributes look like versus span attributes, and the fact that `GET /healthz` produces no spans at all. Then hit `GET /v1/boom` and find the `ERROR` status and its `exception` span event.

**What this stage cannot teach you:** the Collector config is baked into the image. You cannot change a pipeline you cannot see.

---

## Stage 2 — run your own Collector

Stop the app, put a Collector you control in front of LGTM, and start it again pointing at the new one. Run these from the root of a clone of this repo — the `-v` flag mounts a file out of it.

```bash
docker rm -f hello-otel lgtm

# LGTM becomes pure storage — no need to publish 4317 to the host any more
docker run -d --name lgtm --network otel-lab \
  -p 3000:3000 -p 9090:9090 \
  grafana/otel-lgtm:latest

docker run -d --name otelcol --network otel-lab -p 4317:4317 -p 4318:4318 \
  -v "$PWD/examples/otelcol/collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:latest

docker run -d --name hello-otel --network otel-lab -p 8000:8000 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317 \
  -e OTEL_SERVICE_NAME=hello-otel \
  -e OTEL_RESOURCE_ATTRIBUTES=deployment.environment=local \
  ghcr.io/qinrui777/hello-otel:main
```

`/etc/otelcol-contrib/config.yaml` is the image's default config path, so mounting over it needs no `--config` flag.

Now watch the raw data while you generate some:

```bash
docker logs -f otelcol
```

```bash
for i in $(seq 1 20); do
  curl -s -X POST localhost:8000/v1/chat \
    -H 'content-type: application/json' -d '{"message":"trace me"}' -o /dev/null
done
curl -s localhost:8000/v1/boom -o /dev/null
```

### The four things in that config file

[`examples/otelcol/collector.yaml`](../examples/otelcol/collector.yaml) is short on purpose. Read it alongside this table.

| Section        | What it is for                                                              |
|----------------|-----------------------------------------------------------------------------|
| **receivers**  | How data gets in. OTLP over gRPC (`4317`) and HTTP (`4318`)                 |
| **processors** | What happens to it on the way through. Ordered — position changes behaviour |
| **connectors** | One pipeline's output becomes another's input. Here: spans become metrics   |
| **exporters**  | Where it goes. Swap this section to change backends; nothing else moves     |

Four details in that file are worth more than the rest of this document:

**1. `debug` is the best teaching tool in the Collector.** At `verbosity: detailed` it prints every span — trace id, parent id, attributes, events, status — straight to `docker logs`. Ten minutes reading that output teaches more about the data model than any UI.

**2. Batching lives in the exporter now, not in a processor.** The old advice was "always add a `batch` processor". Upstream is moving batching into the exporter's `sending_queue.batch`, so the config here does it there. Running both means batching twice.

**3. `memory_limiter` goes first.** Processors run in list order. Putting the limiter first is what lets it refuse data before anything downstream allocates memory for it.

**4. There are two traces pipelines, and that is deliberate.** `traces/metrics` feeds the `span_metrics` connector from *every* span. `traces/storage` samples, then writes to Tempo. Sampling before the connector would give you RED metrics computed from 10% of your traffic while labelled as if they described all of it.

### Confirming the sampler works

`tail_sampling` keeps every error, every request over 500 ms, and 10% of everything else. The 21 requests above produced exactly 2 stored traces: one ordinary `/v1/chat` trace that won the dice roll, and the `/v1/boom` trace, which was never at risk because the error policy matched it.

That is the entire argument for tail sampling over head sampling. Head sampling (`OTEL_TRACES_SAMPLER` in the SDK) decides when the request *starts*, before anyone knows it is going to fail, so a 10% head sample throws away 90% of your errors.

```bash
# how many distinct traces actually made it to storage
docker logs otelcol 2>&1 | grep -oE 'Trace ID       : [0-9a-f]+' | sort -u | wc -l
```

### Confirming the connector works

The `span_metrics` connector turns spans into RED metrics with no application change. It flushes on a timer — **allow about 60 seconds** after your traffic before the series exist.

```bash
curl -s 'http://localhost:9090/api/v1/label/__name__/values' \
  | tr ',' '\n' | grep lab_
```

```
"lab_calls_total"
"lab_duration_milliseconds_bucket"
"lab_duration_milliseconds_count"
"lab_duration_milliseconds_sum"
```

The `lab` namespace is set in the config for a reason: LGTM generates span metrics of its own (`traces_span_metrics_*`), and without a namespace you cannot tell which component produced which series.

Each series carries the dimensions declared in the connector plus the span's own identity:

```
lab_calls_total{service_name="hello-otel", span_name="POST /v1/echo",
                span_kind="SPAN_KIND_SERVER", http_route="/v1/echo",
                status_code="STATUS_CODE_UNSET", deployment_environment="local"}
```

Note `deployment_environment` — that came from `OTEL_RESOURCE_ATTRIBUTES` on the app, survived the whole pipeline, and became a Prometheus label. Resource attributes propagating into metrics is one of the things OTLP gives you that a Prometheus exporter does not.

---

## Stage 3 — make the signals link to each other

Three separate UIs is not observability. The payoff is being able to move between signals without copying identifiers by hand.

| Direction            | Mechanism                                        | State in this lab                    |
|----------------------|--------------------------------------------------|--------------------------------------|
| **metrics → traces** | Exemplars on the histogram                       | Works now — already wired in LGTM    |
| **traces → logs**    | `trace_id` in the log line, matched by Grafana   | Wired in LGTM, needs work in the app |
| **traces → metrics** | Service map / node graph built from span metrics | Works now                            |

### metrics → traces, working out of the box

The connector attaches exemplars (`exemplars.enabled: true`), and LGTM's Prometheus datasource ships with `exemplarTraceIdDestinations` pointing `trace_id` at Tempo. So the chain is complete with no configuration from you.

In Grafana, **Explore → Prometheus**, query `lab_duration_milliseconds_bucket`, and turn on **Exemplars**. The diamonds under the graph each carry a `trace_id`; clicking one opens that trace in Tempo.

```bash
# the same thing without the UI (BSD date; on Linux use -d '20 minutes ago')
curl -s --data-urlencode 'query=lab_duration_milliseconds_bucket' \
  --data-urlencode "start=$(date -u -v-20M +%Y-%m-%dT%H:%M:%SZ)" \
  --data-urlencode "end=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  'http://localhost:9090/api/v1/query_exemplars'
```

**A real trade-off you will hit immediately:** some exemplars point at traces that tail sampling threw away. Metrics are computed from 100% of spans, storage keeps a fraction, and the exemplar does not know that. In a lab run, 2 of 4 exemplar trace ids returned nothing from Tempo. This is not a bug to fix — it is the cost of sampling, and it is better to meet it here than in an incident.

### traces → logs, the one that needs work

LGTM's Tempo datasource already carries the `tracesToLogsV2` configuration:

```
{${__tags}} | trace_id = "${__trace.traceId}"    matching on service.name
```

So Grafana is ready. What is missing is on the application side: `hello-otel` does not currently emit `trace_id` in its log lines or ship them to Loki. Wiring that up is the best exercise in this document — see below.

---

## Exercises

Work through these in order. Each one is a single change with a visible result.

1. **Delete every processor.** A pipeline needs only `receivers` and `exporters`; `processors` is optional. Cut it down to `otlp → debug` and confirm data still flows. Then add each processor back one at a time and watch `docker logs otelcol` change.
2. **Break the sampler on purpose.** Set `sampling_percentage: 0` and remove the `keep-all-errors` policy. Generate traffic including `/v1/boom` and watch Tempo stay empty. Put the error policy back and see the failures reappear on their own.
3. **Move sampling in front of the connector.** Put `tail_sampling` into the `traces/metrics` pipeline as well, generate steady traffic, and compare `lab_calls_total` before and after. The rate drops by roughly 90% while the traffic has not changed at all.
4. **Filter out a route.** Add the `filter` processor and drop spans where `http.route == "/v1/echo"`. Then explain to yourself why the parent `POST` client span is now orphaned.
5. **Emit `trace_id` in the application logs.** Add `opentelemetry-instrumentation-logging`, ship stdout to Loki, and complete the traces → logs link that Grafana is already waiting for.

---

## Teardown

```bash
docker rm -f hello-otel otelcol lgtm
docker network rm otel-lab
```

LGTM writes to `/data` inside the container, so nothing survives unless you mounted a volume there. If you want your traces to outlive a `docker rm`, add `-v "$PWD/lgtm-data:/data"` when you start it.

---

## Troubleshooting

**No spans in the Collector logs.** Give it time before concluding anything is broken: the SDK's `BatchSpanProcessor` holds spans for ~5 s, and `tail_sampling` adds its `decision_wait` of 10 s on top. Roughly 15 seconds from `curl` to terminal output is normal.

**No `lab_*` metrics in Prometheus.** The connector flushes on a timer — wait a full minute after generating traffic.

**`docker exec otelcol cat ...` fails.** The Collector image is distroless. There is no shell and no `cat`; read the config on the host instead.

**Deprecation warnings about component names.** Collector `v0.160.0` renamed two things the older tutorials still use: the `otlp` *exporter* is now `otlp_grpc`, and the `spanmetrics` connector is now `span_metrics`. The old names still work and log a warning. The config in this repo uses the new ones.

**Requests to `localhost` hang or fail with a proxy error.** If your shell exports `HTTP_PROXY` / `ALL_PROXY`, HTTP clients will route loopback traffic through it. Add `--noproxy '*'` to `curl`. The app itself is already immune — it passes `trust_env=False` on its self-call for exactly this reason.

---

## Where to go next

- The [official OpenTelemetry demo](https://github.com/open-telemetry/opentelemetry-demo) — a real multi-language microservice topology. Worth running *after* this lab, not before: it is 15+ containers and roughly 6 GB of RAM, and starting there makes it hard to tell which change caused which effect.
- [Prometheus as an OTLP backend](https://prometheus.io/docs/guides/opentelemetry/) — Prometheus can receive OTLP directly with `--web.enable-otlp-receiver`, which removes the exporter-plus-scrape detour entirely. The delta versus cumulative temporality section is the part worth reading twice.
- [Jaeger](https://www.jaegertracing.io/docs/latest/architecture/) — v2 is itself a distribution of the OpenTelemetry Collector. If you only care about traces, it is a smaller thing to run than LGTM, and swapping to it is a one-line exporter change.
- [Collector configuration reference](https://opentelemetry.io/docs/collector/configuration/) — the authority on everything in stage 2.
