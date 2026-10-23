# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Thread-local OCR call counter for eval."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


_local = threading.local()


def reset() -> None:
	_local.count = 0


def enable() -> None:
	_local.enabled = True
	_local.count = 0


def disable() -> None:
	_local.enabled = False


def bump(n: int = 1) -> None:
	if getattr(_local, "enabled", False):
		_local.count = getattr(_local, "count", 0) + n


def get_count() -> int:
	return int(getattr(_local, "count", 0) or 0)


@contextmanager
def counting() -> Iterator[None]:
	enable()
	try:
		yield
	finally:
		disable()
