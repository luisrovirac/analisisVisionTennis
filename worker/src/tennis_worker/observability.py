"""Trazas y métricas del worker (OpenTelemetry). Sin OTEL_EXPORTER_OTLP_ENDPOINT todo es no-op,
así los tests y el desarrollo sin collector no necesitan nada."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

SCOPE = "tennis_worker"
_PROPAGATOR = TraceContextTextMapPropagator()


class Instrumentation:
    """Un único punto para spans y métricas: los pasos del pipeline no conocen OpenTelemetry."""

    def __init__(self, tracer: Tracer, meter: Meter) -> None:
        self._tracer = tracer
        self._stage_duration = meter.create_histogram(
            "analysis.stage.duration", unit="s", description="Duración de cada etapa del pipeline")
        self._outcomes = meter.create_counter(
            "analysis.outcome", description="Trabajos terminados por resultado y código de error")

    @classmethod
    def noop(cls) -> Instrumentation:
        return cls(trace.NoOpTracer(), metrics.NoOpMeter(SCOPE))

    @contextmanager
    def job(self, analysis_id: str, traceparent: str | None) -> Iterator[Span]:
        """Span raíz del trabajo, hijo de la petición HTTP que lo encoló (vía `traceparent`)."""
        with self._tracer.start_as_current_span(
            "analysis.process", context=_parent_context(traceparent), kind=trace.SpanKind.CONSUMER,
            attributes={"analysis.id": analysis_id},
        ) as span:
            yield span

    @contextmanager
    def stage(self, name: str) -> Iterator[Span]:
        start = perf_counter()
        try:
            with self._tracer.start_as_current_span(f"stage.{name}") as span:
                yield span
        finally:
            self._stage_duration.record(perf_counter() - start, {"stage": name})

    def outcome(self, status: str, error_code: str = "") -> None:
        self._outcomes.add(1, {"status": status, "error_code": error_code})


def _parent_context(traceparent: str | None) -> Context | None:
    return _PROPAGATOR.extract({"traceparent": traceparent}) if traceparent else None


@dataclass(frozen=True)
class Telemetry:
    instrumentation: Instrumentation
    shutdown: Callable[[], None]


def setup_telemetry(service_name: str, otlp_endpoint: str | None) -> Telemetry:
    if not otlp_endpoint:
        return Telemetry(Instrumentation.noop(), lambda: None)
    # Importes diferidos: el SDK y el exportador solo se cargan si hay collector.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    base = otlp_endpoint.rstrip("/")
    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"))
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)

    def shutdown() -> None:
        tracer_provider.shutdown()
        meter_provider.shutdown()

    instrumentation = Instrumentation(tracer_provider.get_tracer(SCOPE), meter_provider.get_meter(SCOPE))
    return Telemetry(instrumentation, shutdown)


def current_trace_ids() -> dict[str, str]:
    """trace_id/span_id del span activo, para correlacionar cada línea de log con su traza."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x")}
