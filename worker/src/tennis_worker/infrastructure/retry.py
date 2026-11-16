"""Reintentos con backoff exponencial para errores transitorios específicos."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


def with_backoff(
    action: Callable[[], T],
    retry_on: tuple[type[Exception], ...],
    attempts: int = 5,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning("operación fallida, reintentando", extra={"attempt": attempt, "delay": delay,
                                                                      "error": str(exc)})
            sleep(delay)
    raise AssertionError("inalcanzable")
