# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Background-job orchestration for long-running extractions (Phase 14).

Large, multi-page PDFs can easily blow through Frappe's default HTTP
timeout.  This module defers those requests to the ``long`` queue so
the caller gets an immediate handle and can poll for completion.

Flow:

1. **Enqueue.**  :func:`enqueue_extraction` records a new ``IDP
   Document Log`` row in status ``Extracting`` (the placeholder the
   UI polls against) and hands the real work off via
   ``frappe.enqueue`` to :func:`_run_extraction_job`.

2. **Run.**  The worker invokes :mod:`idp.api.extract` under the hood
   and updates the placeholder row with the outcome, including retry
   accounting on transient failures.

3. **Poll.**  :func:`get_job_status` is a whitelisted endpoint the
   SPA calls to observe progress without holding a long HTTP
   connection.
"""

from __future__ import annotations

from typing import Any

import frappe

from idp.core.audit import log_event
from idp.core.exceptions import IDPError
from idp.core.logger import get_logger

logger = get_logger("idp.api.background")


DEFAULT_QUEUE = "long"
DEFAULT_TIMEOUT_SECONDS = 600   # 10 minutes — roadmap §14.2 says "configurable"
MAX_RETRIES = 2                 # per roadmap §14.2


# ---------------------------------------------------------------------------
# Enqueue + run
# ---------------------------------------------------------------------------


@frappe.whitelist()
def enqueue_extraction(
	file_url: str,
	target_doctype: str,
	company: str | None = None,
	language: str = "en",
	timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
	"""Queue an extraction job and return the placeholder log ID.

	The caller gets back ``{"job_id": <IDP Document Log name>}``
	which they can pass to :func:`get_job_status` until the status
	flips away from ``Extracting``.
	"""
	if not file_url:
		frappe.throw("file_url is required.", frappe.ValidationError)
	if not target_doctype:
		frappe.throw("target_doctype is required.", frappe.ValidationError)

	placeholder = log_event(
		file_url=file_url,
		target_doctype=target_doctype,
		company=company,
		status="Extracting",
	)
	if not placeholder:
		# Audit disabled — we still enqueue but skip polling handoff.
		placeholder = f"transient-{frappe.generate_hash(length=10)}"

	try:
		frappe.enqueue(
			"idp.api.background._run_extraction_job",
			queue=DEFAULT_QUEUE,
			timeout=int(timeout),
			job_name=f"idp-extract-{placeholder}",
			now=False,
			job_id=placeholder,
			file_url=file_url,
			target_doctype=target_doctype,
			company=company,
			language=language,
			attempt=1,
		)
	except Exception as exc:
		logger.error("Failed to enqueue extraction: %s", exc, exc_info=True)
		# Flip the placeholder to Failed so the UI stops polling.
		_update_log(placeholder, status="Failed", error_message=f"Enqueue failed: {exc}")
		raise

	return {
		"job_id": placeholder,
		"queue": DEFAULT_QUEUE,
		"timeout": timeout,
	}


def _run_extraction_job(
	job_id: str,
	file_url: str,
	target_doctype: str,
	company: str | None,
	language: str,
	attempt: int = 1,
) -> dict:
	"""Worker entry point — performs the extraction and updates the log.

	Retries transient failures up to :data:`MAX_RETRIES` times.  The
	function is **not** whitelisted — it is only invoked by the
	Frappe worker through ``frappe.enqueue``.
	"""
	logger.info("[job=%s attempt=%s] running extraction for %s", job_id, attempt, file_url)

	# Local import avoids a circular dependency between this module
	# and :mod:`idp.api.extract` (which imports audit / logger too).
	from idp.api.extract import extract_document

	try:
		result = extract_document(
			file_url=file_url,
			target_doctype=target_doctype,
			company=company,
			language=language,
		)
	except IDPError as exc:
		# Domain errors are not retryable — surface immediately.
		logger.warning("[job=%s] extraction failed permanently: %s", job_id, exc)
		_update_log(job_id, status="Failed", error_message=f"{type(exc).__name__}: {exc}")
		return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
	except Exception as exc:
		# Treat anything else as potentially transient (network, FS, OOM).
		if attempt < MAX_RETRIES:
			logger.warning("[job=%s] transient failure attempt %s: %s — retrying",
				job_id, attempt, exc, exc_info=True)
			try:
				frappe.enqueue(
					"idp.api.background._run_extraction_job",
					queue=DEFAULT_QUEUE,
					timeout=DEFAULT_TIMEOUT_SECONDS,
					job_name=f"idp-extract-{job_id}-retry{attempt + 1}",
					job_id=job_id,
					file_url=file_url,
					target_doctype=target_doctype,
					company=company,
					language=language,
					attempt=attempt + 1,
				)
				return {"success": False, "retry_scheduled": True, "attempt": attempt + 1}
			except Exception as re_exc:
				logger.error("[job=%s] retry enqueue failed: %s", job_id, re_exc)

		logger.error("[job=%s] extraction failed: %s", job_id, exc, exc_info=True)
		_update_log(job_id, status="Failed", error_message=f"Unexpected: {exc}")
		return {"success": False, "error": str(exc)}

	# Success — update the placeholder with the outcome.
	_update_log(
		job_id,
		status="Extracted" if result.get("success") else "Failed",
		processing_time_ms=result.get("processing_time_ms"),
		ocr_confidence=result.get("confidence"),
		extraction_data=result.get("extracted_data"),
		error_message=result.get("error"),
	)
	return result


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_job_status(job_id: str) -> dict:
	"""Return the current status of an enqueued extraction job.

	Requires read permission on the *IDP Document Log* row. Prefer
	``idp.api.scan.get_scan`` once that module ships (PR3).
	"""
	if not job_id:
		frappe.throw("job_id is required.", frappe.ValidationError)

	try:
		doc = frappe.get_doc("IDP Document Log", job_id)
	except frappe.DoesNotExistError:
		return {"job_id": job_id, "status": "unknown"}

	doc.check_permission("read")

	return {
		"job_id": doc.name,
		"status": doc.status,
		"processing_time_ms": doc.processing_time_ms,
		"ocr_confidence": doc.ocr_confidence,
		"error_message": doc.error_message,
		"created_doctype": doc.created_doctype,
		"created_document": doc.created_document,
		"updated_on": str(doc.modified) if doc.modified else None,
	}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_log(job_id: str, **fields: Any) -> None:
	"""Patch an existing IDP Document Log row.  Never raises."""
	if not job_id:
		return
	try:
		doc = frappe.get_doc("IDP Document Log", job_id)
	except Exception:
		logger.debug("No log row to update for job_id=%s", job_id)
		return

	for key, value in fields.items():
		if value is None:
			continue
		if key == "extraction_data":
			import json as _json

			try:
				value = _json.dumps(value, default=str)
			except Exception:
				value = _json.dumps({"note": "unserialisable"})
		if hasattr(doc, key):
			setattr(doc, key, value)

	try:
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		logger.warning("Failed to update job log row %s", job_id, exc_info=True)
