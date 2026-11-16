from __future__ import annotations

import json
import logging
import sys

from tennis_worker.logging_setup import JsonFormatter, configure_logging


def test_json_formatter_includes_extra_fields_and_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "falló %s", ("x",), sys.exc_info(), extra={"analysis_id": "a1"})
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "falló x" and line["level"] == "ERROR"
    assert line["analysis_id"] == "a1" and "ValueError" in line["exc"]


def test_configure_logging_sets_level() -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
