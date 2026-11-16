"""Comprueba con exportadores en memoria que el worker continúa la traza de la API
y que cada etapa del pipeline aparece como un span hijo."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tennis_worker.contracts import JobMessage
from tennis_worker.logging_setup import JsonFormatter
from tennis_worker.observability import Instrumentation, setup_telemetry
from tennis_worker.pipeline.orchestrator import AnalysisPipeline
from tests.synthetic import GOOD_SWING
from tests.test_orchestrator import stages_for

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
STAGES = {"stage.pose", "stage.quality", "stage.classify", "stage.segment", "stage.metrics",
          "stage.rules", "stage.prioritize"}


def instrumented() -> tuple[Instrumentation, InMemorySpanExporter, InMemoryMetricReader]:
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    return Instrumentation(tracer_provider.get_tracer("test"), meter), exporter, reader


def test_job_span_continues_api_trace_and_stages_are_children(job: JobMessage) -> None:
    obs, exporter, _ = instrumented()
    with obs.job(job.analysis_id, TRACEPARENT):
        AnalysisPipeline(stages_for(GOOD_SWING), obs).run(Path("video"), job)
    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["analysis.process"]
    assert format(root.context.trace_id, "032x") == TRACE_ID  # misma traza que la petición HTTP
    assert spans.keys() >= STAGES
    for name in STAGES:
        parent = spans[name].parent
        assert parent is not None and parent.span_id == root.context.span_id


def test_job_without_traceparent_starts_new_trace(job: JobMessage) -> None:
    obs, exporter, _ = instrumented()
    with obs.job(job.analysis_id, None):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.parent is None


def test_stage_durations_and_outcomes_are_recorded() -> None:
    obs, _, reader = instrumented()
    with obs.stage("pose"):
        pass
    obs.outcome("failed", "NO_PERSON_DETECTED")
    data = reader.get_metrics_data()
    assert data is not None
    names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert {"analysis.stage.duration", "analysis.outcome"} <= names


def test_logs_carry_trace_ids_inside_a_span(job: JobMessage) -> None:
    obs, _, _ = instrumented()
    with obs.job(job.analysis_id, TRACEPARENT):
        record = logging.getLogger("t").makeRecord("t", logging.INFO, __file__, 1, "hola", (), None)
        line = json.loads(JsonFormatter().format(record))
    assert line["trace_id"] == TRACE_ID and len(line["span_id"]) == 16


def test_telemetry_is_noop_without_endpoint() -> None:
    telemetry = setup_telemetry("tennis-worker", None)
    with telemetry.instrumentation.stage("pose"):
        pass
    telemetry.shutdown()
