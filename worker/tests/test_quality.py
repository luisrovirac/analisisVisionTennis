from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.contracts import JobMessage
from tennis_worker.errors import LowPoseQualityError, NoPersonDetectedError
from tennis_worker.pipeline.quality import QualityAssessor
from tests.synthetic import SwingSpec, make_pose


def test_rejects_video_without_person() -> None:
    pose = make_pose()
    pose = replace(pose, detected=np.zeros(pose.frame_count, dtype=bool))
    with pytest.raises(NoPersonDetectedError):
        QualityAssessor(min_detection_rate=0.6).check(pose)


def test_rejects_low_detection_rate() -> None:
    pose = make_pose()
    detected = pose.detected.copy()
    detected[: pose.frame_count // 2] = False
    with pytest.raises(LowPoseQualityError):
        QualityAssessor(min_detection_rate=0.6).check(replace(pose, detected=detected))


def test_accepts_good_video_without_warnings(job: JobMessage) -> None:
    pose = make_pose()
    assessor = QualityAssessor(min_detection_rate=0.6)
    assessor.check(pose)
    assert assessor.warnings(pose, job, []) == []


def test_warns_about_fps_view_and_unmeasured(job: JobMessage) -> None:
    pose = make_pose(SwingSpec(fps=24))
    warnings = QualityAssessor(min_detection_rate=0.6).warnings(
        pose, job.model_copy(update={"camera_view": "front"}), ["Ángulo del codo"])
    assert len(warnings) == 3
    assert "24 fps" in warnings[0] and "perfil" in warnings[1] and "Ángulo del codo" in warnings[2]
