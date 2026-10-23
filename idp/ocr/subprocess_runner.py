# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Subprocess-isolated PaddleOCR runner (Phase 27 §27.1).

PaddleOCR runs heavyweight C++ kernels inside a Python wheel; a
segfault, OOM, or stuck inference inside the wheel takes the whole
Frappe worker down with it and surfaces as a generic 500 to the user.

This module isolates the OCR call inside a separate Python process so
the worst that can happen is an :class:`OCRError`.  Strategy:

* Run the OCR call as ``python -m idp.ocr.subprocess_runner <args>``.
* Pass arguments via JSON on stdin (avoids quoting headaches on Windows
  and keeps the CLI shape stable across versions).
* Stream JSON back over stdout.
* Enforce a hard wall-clock timeout via :func:`subprocess.run`; on
  expiry we kill the child and raise :class:`OCRTimeoutError`.
* Pre-flight memory check via :func:`idp.core.system.check_memory_available`
  before spawning to avoid OOM-killer drama.

This file is intentionally importable as a script
(``python -m idp.ocr.subprocess_runner``) — its ``main`` reads JSON from
stdin, performs the requested operation in-process, and writes JSON to
stdout.  The parent (this same module's ``run_in_subprocess``) is what
the rest of the IDP codebase calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from idp.core.exceptions import OCRError, OCRTimeoutError
from idp.core.logger import get_logger
from idp.core.system import check_memory_available

logger = get_logger("idp.ocr_subprocess")


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Files smaller than this threshold are processed in the parent process —
# the fork + Paddle warm-up cost dominates extraction time for tiny
# inputs.  See Phase 27 §27.6 (Risks: "Subprocess overhead on small files").
INLINE_SIZE_THRESHOLD: int = 1 * 1024 * 1024  # 1 MiB


# Supported operations the child can perform.  Anything else is rejected
# before we spawn the subprocess.
_OPERATIONS = {"extract_text", "detect_language"}


# ---------------------------------------------------------------------------
# Parent-side API
# ---------------------------------------------------------------------------


@dataclass
class SubprocessResult:
	"""Decoded result envelope returned from the child."""

	operation: str
	data: Any
	stderr: str = ""

	def as_dict(self) -> dict:
		return {"operation": self.operation, "data": self.data, "stderr": self.stderr}


def should_use_subprocess(file_path: str) -> bool:
	"""Always isolate PaddleOCR in a child process."""
	_ = (file_path, INLINE_SIZE_THRESHOLD)
	return True


def run_in_subprocess(
	operation: str,
	*,
	file_path: str,
	lang: str = "en",
	timeout: float = 300.0,
	required_memory_mb: int | None = None,
) -> SubprocessResult:
	"""Execute *operation* on *file_path* inside an isolated Python process.

	Args:
		operation: One of ``"extract_text"`` / ``"detect_language"``.
		file_path: Absolute path to the file to OCR.
		lang: PaddleOCR language code.
		timeout: Wall-clock timeout in seconds.  ``OCRTimeoutError`` is
			raised on expiry.
		required_memory_mb: Minimum available RAM (MiB) demanded by the
			pre-flight check.  ``None`` uses the system default.

	Returns:
		A :class:`SubprocessResult` with the decoded payload.

	Raises:
		OCRError: child exited non-zero, returned invalid JSON, or the
			operation itself failed.
		OCRTimeoutError: child exceeded ``timeout``.
		InsufficientMemoryError: pre-flight memory check failed.
	"""

	if operation not in _OPERATIONS:
		raise OCRError(
			f"Unsupported OCR subprocess operation: {operation!r}",
			details={"operation": operation},
		)

	# Pre-flight RAM check.
	check_memory_available(required_memory_mb) if required_memory_mb else check_memory_available()

	payload = {
		"operation": operation,
		"file_path": file_path,
		"lang": lang,
	}

	cmd = [sys.executable, "-m", "idp.ocr.subprocess_runner"]
	logger.debug(f"run_in_subprocess: {cmd} payload={payload!r}")

	# Inherit the parent environment so the child can find PaddleOCR
	# wheels installed under bench's venv.  We disable Python output
	# buffering so the JSON payload arrives before the process exits.
	env = os.environ.copy()
	env.setdefault("PYTHONUNBUFFERED", "1")

	try:
		completed = subprocess.run(
			cmd,
			input=json.dumps(payload),
			capture_output=True,
			text=True,
			timeout=timeout,
			env=env,
			check=False,
		)
	except subprocess.TimeoutExpired as exc:
		raise OCRTimeoutError(
			f"OCR subprocess exceeded {timeout:.0f}s for {file_path!r}",
			timeout_seconds=timeout,
		) from exc
	except OSError as exc:
		raise OCRError(
			f"Failed to launch OCR subprocess: {exc}",
			details={"file_path": file_path},
		) from exc

	stderr = (completed.stderr or "").strip()

	if completed.returncode != 0:
		raise OCRError(
			f"OCR subprocess exited with code {completed.returncode}",
			details={
				"file_path": file_path,
				"operation": operation,
				"stderr": stderr[:2_000],
			},
		)

	stdout = (completed.stdout or "").strip()
	if not stdout:
		raise OCRError(
			"OCR subprocess returned empty stdout",
			details={"stderr": stderr[:2_000]},
		)

	try:
		decoded = json.loads(stdout)
	except ValueError as exc:
		raise OCRError(
			f"OCR subprocess returned non-JSON output: {exc}",
			details={"stdout_head": stdout[:500], "stderr": stderr[:500]},
		) from exc

	if not isinstance(decoded, dict) or decoded.get("ok") is not True:
		raise OCRError(
			decoded.get("error", "OCR subprocess reported failure") if isinstance(decoded, dict) else "Unknown OCR failure",
			details={"payload": decoded if isinstance(decoded, dict) else None},
		)

	return SubprocessResult(
		operation=operation,
		data=decoded.get("data"),
		stderr=stderr,
	)


# ---------------------------------------------------------------------------
# Child-side entry point
# ---------------------------------------------------------------------------


def _child_main() -> int:
	"""Read JSON from stdin, perform OCR, write JSON to stdout.

	The child intentionally has its own minimal import surface so a
	broken parent module does not poison the child's start-up.
	"""

	try:
		raw = sys.stdin.read()
		payload = json.loads(raw)
	except Exception as exc:
		_write_error(f"Invalid stdin payload: {exc}")
		return 2

	operation = payload.get("operation")
	file_path = payload.get("file_path")
	lang = payload.get("lang") or "en"

	if operation not in _OPERATIONS:
		_write_error(f"Unsupported operation: {operation!r}")
		return 2
	if not file_path or not isinstance(file_path, str):
		_write_error("file_path is required")
		return 2

	try:
		# Lazy import: keep the parent fast in the small-file path.
		from idp.ocr.engine import detect_language, extract_text

		if operation == "extract_text":
			blocks = extract_text(file_path, lang=lang)
			data = {"blocks": blocks}
		else:  # detect_language
			detected = detect_language(file_path)
			data = {"language": detected}
	except Exception as exc:
		_write_error(f"OCR child failed: {exc}")
		return 1

	json.dump({"ok": True, "data": data}, sys.stdout)
	sys.stdout.write("\n")
	return 0


def _write_error(message: str) -> None:
	json.dump({"ok": False, "error": message}, sys.stdout)
	sys.stdout.write("\n")


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
	raise SystemExit(_child_main())


__all__ = [
	"INLINE_SIZE_THRESHOLD",
	"SubprocessResult",
	"run_in_subprocess",
	"should_use_subprocess",
]
