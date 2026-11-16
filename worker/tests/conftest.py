from __future__ import annotations

from pathlib import Path

import pytest

from tennis_worker.contracts import JobMessage

WORKER_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = WORKER_ROOT / "knowledge_base"
CONTRACTS_DIR = WORKER_ROOT.parent / "contracts"


@pytest.fixture
def job() -> JobMessage:
    return JobMessage(
        schema_version="1.0",
        analysis_id="3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10",
        video_key="videos/3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10",
        stroke_type="forehand",
        handedness="right",
        camera_view="side",
    )
