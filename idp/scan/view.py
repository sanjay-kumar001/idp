# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Build the Scan view model from a saved Document Log row."""

from __future__ import annotations

import json
from typing import Any

from idp.scan.state import REVIEW, REVIEW_STATUSES, SAVED


def scan_view(doc) -> dict[str, Any]:
	"""Response shape for get_scan / save_edits — always from the DB doc."""
	extracted = _as_dict(doc.extraction_data)
	confidence = _as_dict(doc.field_confidence_json)
	fields = _fields(doc, extracted, confidence)
	blocked = _blocked_reasons(doc, fields)
	return {
		"name": doc.name,
		"status": doc.status,
		"stage": _stage(doc.status),
		"error_key": doc.error_key,
		"error_message": doc.error_message,
		"modified": str(doc.modified) if doc.modified else None,
		"file_url": doc.file_url,
		"target_doctype": doc.target_doctype,
		"company": doc.company,
		"pipeline_version": doc.pipeline_version,
		"file_sha256": doc.file_sha256,
		"path_taken": doc.path_taken,
		"ocr_engine": doc.ocr_engine,
		"cancel_requested": bool(doc.cancel_requested),
		"fields": fields,
		"totals_check": extracted.get("totals_check"),
		"missing_masters": extracted.get("missing_masters") or [],
		"duplicate_candidates": extracted.get("duplicate_candidates") or [],
		"can_make_draft": doc.status in REVIEW_STATUSES and not blocked,
		"blocked_reasons": blocked,
		"created_doctype": doc.created_doctype,
		"created_document": doc.created_document,
	}


def _stage(status: str) -> str | None:
	mapping = {
		"Queued": "queued",
		"Reading": "reading",
		"Review": "review",
		"Saving": "saving",
		"Saved": "saved",
		"Failed": "failed",
		"Cancelled": "cancelled",
		"Ready": "review",
		"Extracting": "reading",
		"Extracted": "review",
	}
	return mapping.get(status)


def _fields(doc, extracted: dict, confidence: dict) -> list[dict[str, Any]]:
	by_name = {r.fieldname: r for r in (doc.scan_fields or [])}
	names = list(by_name.keys()) or [
		k for k, v in extracted.items() if not k.startswith("_") and not isinstance(v, (list, dict))
	]
	out = []
	for name in names:
		row = by_name.get(name)
		value = extracted.get(name)
		edited = row.edited if row else None
		out.append(
			{
				"fieldname": name,
				"value": value if edited in (None, "") else edited,
				"extracted": row.extracted if row else value,
				"edited": edited,
				"confidence": (row.confidence if row and row.confidence is not None else confidence.get(name)),
				"flags": row.flags if row else None,
				"source_box": row.source_box if row else None,
			}
		)
	return out


def _blocked_reasons(doc, fields: list[dict]) -> list[str]:
	reasons = []
	if doc.status not in REVIEW_STATUSES:
		if doc.status != SAVED:
			reasons.append(f"status_{doc.status}")
	if not doc.target_doctype:
		reasons.append("missing_target_doctype")
	if not doc.company:
		reasons.append("missing_company")
	# Low-confidence required fields can be flagged by extractors via flags JSON
	for f in fields:
		flags = f.get("flags") or ""
		if "required" in str(flags) and f.get("value") in (None, ""):
			reasons.append(f"missing_{f['fieldname']}")
	return reasons


def _as_dict(data: Any) -> dict[str, Any]:
	if isinstance(data, str):
		try:
			data = json.loads(data) if data else {}
		except ValueError:
			data = {}
	return data if isinstance(data, dict) else {}
