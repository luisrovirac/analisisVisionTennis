from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import redis

from tennis_worker.config import Settings
from tennis_worker.infrastructure.redis_consumer import QueuedJob, RedisJobConsumer
from tennis_worker.infrastructure.retry import with_backoff
from tennis_worker.infrastructure.s3_store import S3ObjectStore
from tests.conftest import KNOWLEDGE_DIR

# --- retry ---


def test_retry_succeeds_after_transient_failures() -> None:
    calls, sleeps = [], []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError
        return "ok"

    assert with_backoff(flaky, (ConnectionError,), sleep=sleeps.append) == "ok"
    assert sleeps == [0.5, 1.0]


def test_retry_gives_up_and_does_not_catch_other_errors() -> None:
    def broken() -> None:
        raise ConnectionError

    with pytest.raises(ConnectionError):
        with_backoff(broken, (ConnectionError,), attempts=2, sleep=lambda _: None)
    with pytest.raises(ValueError):
        with_backoff(lambda: int("x"), (ConnectionError,), sleep=lambda _: None)


# --- Redis consumer (cliente falso con la forma de respuesta de redis-py) ---


class FakeRedis:
    def __init__(self, stale: list[Any] | None = None, new: list[Any] | None = None,
                 group_error: str | None = None) -> None:
        self.stale, self.new, self.group_error = stale or [], new, group_error
        self.acked: list[str] = []
        self.dlq: list[tuple[str, dict[str, str]]] = []
        self.executed = False

    def pipeline(self, transaction: bool) -> FakeRedis:
        assert transaction, "DLQ y ACK deben ir en una transacción"
        return self

    def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.dlq.append((stream, fields))

    def execute(self) -> None:
        self.executed = True

    def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        if self.group_error:
            raise redis.ResponseError(self.group_error)

    def xautoclaim(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [b"0-0", self.stale, []]

    def xreadgroup(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [[b"analysis_jobs", self.new]] if self.new else []

    def xack(self, stream: str, group: str, message_id: str) -> None:
        self.acked.append(message_id)


def consumer(client: FakeRedis) -> RedisJobConsumer:
    return RedisJobConsumer(client, "analysis_jobs", "g", "c1", claim_idle_ms=1000,  # type: ignore[arg-type]
                            dlq_stream="analysis_jobs:dlq")


def test_consumer_prefers_stale_messages() -> None:
    client = FakeRedis(stale=[(b"1-0", {b"payload": b"old"})], new=[(b"2-0", {b"payload": b"new"})])
    job = consumer(client).next_job()
    assert job is not None and (job.message_id, job.payload) == ("1-0", "old")


def test_consumer_reads_new_and_skips_deleted_entries() -> None:
    client = FakeRedis(stale=[(b"1-0", None)], new=[(b"2-0", {b"payload": b"new"})])
    job = consumer(client).next_job()
    assert job is not None and job.payload == "new"


def test_consumer_returns_none_when_idle_and_acks() -> None:
    client = FakeRedis()
    c = consumer(client)
    assert c.next_job() is None
    c.ack("9-0")
    assert client.acked == ["9-0"]


def test_consumer_reads_traceparent_when_present() -> None:
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    client = FakeRedis(new=[(b"2-0", {b"payload": b"new", b"traceparent": tp.encode()})])
    job = consumer(client).next_job()
    assert job is not None and job.traceparent == tp


def test_dead_letter_copies_and_acks_atomically() -> None:
    client = FakeRedis()
    consumer(client).dead_letter(QueuedJob("7-0", '{"x":1}', traceparent="00-tp"), "MAX_ATTEMPTS_EXCEEDED")
    stream, entry = client.dlq[0]
    assert stream == "analysis_jobs:dlq" and client.acked == ["7-0"] and client.executed
    assert entry["payload"] == '{"x":1}' and entry["reason"] == "MAX_ATTEMPTS_EXCEEDED"
    assert entry["original_id"] == "7-0" and entry["traceparent"] == "00-tp"


def test_ensure_group_tolerates_existing_group_only() -> None:
    consumer(FakeRedis(group_error="BUSYGROUP Consumer Group name already exists")).ensure_group()
    with pytest.raises(redis.ResponseError):
        consumer(FakeRedis(group_error="WRONGTYPE")).ensure_group()


# --- S3 store ---


class FakeS3:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        self.calls.append(("download", (bucket, key, dest)))

    def put_object(self, **kwargs: Any) -> None:
        self.calls.append(("put", kwargs))


def test_s3_store_delegates_to_client(tmp_path: Path) -> None:
    client = FakeS3()
    store = S3ObjectStore(client, "bucket")  # type: ignore[arg-type]
    store.download("videos/1", tmp_path / "v")
    store.put_json("poses/1.json", {"a": 1})
    assert client.calls[0] == ("download", ("bucket", "videos/1", str(tmp_path / "v")))
    put = client.calls[1][1]
    assert put["Key"] == "poses/1.json" and json.loads(put["Body"]) == {"a": 1}


# --- config ---


def base_env(tmp_path: Path) -> dict[str, str]:
    model = tmp_path / "model.task"
    model.write_bytes(b"x")
    return {
        "DATABASE_URL": "postgres://u:p@h/db", "REDIS_URL": "redis://h:6379/0",
        "S3_ENDPOINT": "minio:9000", "S3_ACCESS_KEY": "k", "S3_SECRET_KEY": "s", "S3_BUCKET": "b",
        "POSE_MODEL_PATH": str(model), "KNOWLEDGE_BASE_DIR": str(KNOWLEDGE_DIR),
    }


def test_settings_load_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in base_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.s3_endpoint_url == "http://minio:9000"
    assert settings.max_attempts == 3
    assert settings.dlq_stream == "analysis_jobs:dlq" and settings.otel_exporter_otlp_endpoint is None
    assert "p@h" not in repr(settings)  # los secretos no aparecen en logs


def test_settings_fail_fast_on_missing_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = base_env(tmp_path) | {"POSE_MODEL_PATH": str(tmp_path / "missing.task")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="no existe"):
        Settings()  # type: ignore[call-arg]
