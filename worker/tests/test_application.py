from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tennis_worker.application import (
    INVALID_PAYLOAD_REASON,
    MAX_ATTEMPTS_CODE,
    JobProcessor,
    Outcome,
    WorkerLoop,
)
from tennis_worker.contracts import JobMessage, Quality, Report, Segment
from tennis_worker.errors import NoPersonDetectedError
from tennis_worker.infrastructure.redis_consumer import QueuedJob
from tennis_worker.pipeline.orchestrator import PipelineResult
from tests.synthetic import make_pose


class TransientError(Exception):
    pass


class FakeRepo:
    def __init__(self, attempt: int | None = 1) -> None:
        self.attempt = attempt
        self.completed: list[Report] = []
        self.failed: list[tuple[str, str]] = []

    def start_processing(self, analysis_id: str) -> int | None:
        return self.attempt

    def complete(self, analysis_id: str, report: Report) -> None:
        self.completed.append(report)

    def fail(self, analysis_id: str, code: str, message: str) -> None:
        self.failed.append((code, message))


class FakeStore:
    def __init__(self) -> None:
        self.downloaded: list[str] = []
        self.uploaded: dict[str, dict[str, Any]] = {}

    def download(self, key: str, destination: Path) -> None:
        destination.write_bytes(b"video")
        self.downloaded.append(key)

    def put_json(self, key: str, data: dict[str, Any]) -> None:
        self.uploaded[key] = data


REPORT = Report(
    stroke_type="forehand",
    segment=Segment(fps=60, start_frame=0, backswing_frame=1, impact_frame=2, end_frame=3),
    metrics=[], findings=[], quality=Quality(pose_detection_rate=1.0, warnings=[]),
    knowledge_base_version="forehand@test",
)


class FakePipeline:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen_paths: list[Path] = []

    def run(self, video_path: Path, job: JobMessage) -> PipelineResult:
        self.seen_paths.append(video_path)
        assert video_path.exists()
        if self.error:
            raise self.error
        return PipelineResult(report=REPORT, pose=make_pose())


def processor(repo: FakeRepo, store: FakeStore | None = None, pipeline: FakePipeline | None = None) -> JobProcessor:
    return JobProcessor(repo, store or FakeStore(), pipeline or FakePipeline(), max_attempts=3)


def test_successful_job_stores_pose_and_report(job: JobMessage) -> None:
    repo, store, pipeline = FakeRepo(), FakeStore(), FakePipeline()
    assert processor(repo, store, pipeline).process(job) is Outcome.COMPLETED
    assert store.downloaded == [job.video_key]
    assert f"poses/{job.analysis_id}.json" in store.uploaded
    assert repo.completed == [REPORT] and repo.failed == []
    assert not pipeline.seen_paths[0].exists()  # el directorio temporal se limpia


def test_analysis_error_marks_failed_with_user_message(job: JobMessage) -> None:
    repo = FakeRepo()
    outcome = processor(repo, pipeline=FakePipeline(NoPersonDetectedError("0 detecciones"))).process(job)
    assert outcome is Outcome.FAILED
    assert repo.failed == [(NoPersonDetectedError.code, NoPersonDetectedError.user_message)]
    assert repo.completed == []


def test_already_resolved_job_is_skipped(job: JobMessage) -> None:
    repo, store = FakeRepo(attempt=None), FakeStore()
    assert processor(repo, store).process(job) is Outcome.SKIPPED
    assert store.downloaded == [] and repo.failed == [] and repo.completed == []


def test_exceeding_max_attempts_fails_and_dead_letters(job: JobMessage) -> None:
    repo, store = FakeRepo(attempt=4), FakeStore()
    assert processor(repo, store).process(job) is Outcome.DEAD_LETTER
    assert repo.failed[0][0] == MAX_ATTEMPTS_CODE and store.downloaded == []


def test_infrastructure_errors_propagate(job: JobMessage) -> None:
    with pytest.raises(TransientError):
        processor(FakeRepo(), pipeline=FakePipeline(TransientError())).process(job)


# --- WorkerLoop ---

class FakeConsumer:
    def __init__(self, jobs: list[QueuedJob]) -> None:
        self.jobs = jobs
        self.acked: list[str] = []
        self.dead: list[tuple[str, str]] = []
        self.group_ready = False

    def ensure_group(self) -> None:
        self.group_ready = True

    def next_job(self) -> QueuedJob | None:
        return self.jobs.pop(0) if self.jobs else None

    def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    def dead_letter(self, job: QueuedJob, reason: str) -> None:
        self.dead.append((job.message_id, reason))


class RecordingProcessor(JobProcessor):
    def __init__(self, error: Exception | None = None, outcome: Outcome = Outcome.COMPLETED) -> None:
        self.error = error
        self.outcome = outcome
        self.processed: list[JobMessage] = []

    def process(self, job: JobMessage) -> Outcome:
        if self.error:
            raise self.error
        self.processed.append(job)
        return self.outcome


def loop(consumer: FakeConsumer, proc: RecordingProcessor) -> WorkerLoop:
    return WorkerLoop(consumer, proc, transient_errors=(TransientError,))


def test_loop_processes_and_acks(job: JobMessage) -> None:
    consumer, proc = FakeConsumer([QueuedJob("1-0", job.model_dump_json())]), RecordingProcessor()
    loop(consumer, proc).run_once()
    assert proc.processed == [job] and consumer.acked == ["1-0"]


def test_loop_dead_letters_invalid_payload() -> None:
    consumer, proc = FakeConsumer([QueuedJob("1-0", '{"bad": true}')]), RecordingProcessor()
    loop(consumer, proc).run_once()
    assert proc.processed == [] and consumer.dead == [("1-0", INVALID_PAYLOAD_REASON)]
    assert consumer.acked == []


def test_loop_dead_letters_when_attempts_exhausted(job: JobMessage) -> None:
    consumer = FakeConsumer([QueuedJob("1-0", job.model_dump_json())])
    loop(consumer, RecordingProcessor(outcome=Outcome.DEAD_LETTER)).run_once()
    assert consumer.dead == [("1-0", MAX_ATTEMPTS_CODE)] and consumer.acked == []


def test_loop_survives_unexpected_errors_without_ack(job: JobMessage) -> None:
    consumer = FakeConsumer([QueuedJob("1-0", job.model_dump_json())])
    loop(consumer, RecordingProcessor(ZeroDivisionError())).run_once()
    assert consumer.acked == [] and consumer.dead == []


def test_loop_leaves_message_pending_on_transient_error(job: JobMessage) -> None:
    consumer = FakeConsumer([QueuedJob("1-0", job.model_dump_json())])
    loop(consumer, RecordingProcessor(TransientError())).run_once()
    assert consumer.acked == []


def test_loop_idle_when_no_jobs() -> None:
    consumer = FakeConsumer([])
    loop(consumer, RecordingProcessor()).run_once()
    assert consumer.acked == []


def test_run_stops_when_requested(job: JobMessage) -> None:
    consumer = FakeConsumer([QueuedJob("1-0", job.model_dump_json())])
    worker = loop(consumer, RecordingProcessor())

    def stop_after_first() -> QueuedJob | None:
        worker.stop()
        return FakeConsumer.next_job(consumer)

    consumer.next_job = stop_after_first  # type: ignore[method-assign]
    worker.run()
    assert consumer.group_ready and consumer.acked == ["1-0"]
