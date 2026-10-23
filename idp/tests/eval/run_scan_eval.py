# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Scan eval runner.

Modes:
- offline (default): map fixture ``ocr_text`` through FieldMapper — no Paddle.
- live: run ``extract_document`` on each PNG (requires OCR stack).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scan_eval"

HEADER_FIELDS = (
	"supplier_trn",
	"bill_no",
	"bill_date",
	"net_total",
	"tax_total",
	"grand_total",
	"currency",
)


def _load_cases() -> list[dict[str, Any]]:
	cases: list[dict[str, Any]] = []
	for path in sorted(FIXTURE_DIR.glob("*.gt.json")):
		cases.append(json.loads(path.read_text(encoding="utf-8")))
	return cases


def _norm(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, float):
		return f"{value:.2f}"
	if isinstance(value, int) and not isinstance(value, bool):
		return f"{float(value):.2f}"
	text = str(value).strip()
	try:
		return f"{float(text.replace(',', '')):.2f}"
	except ValueError:
		return text.lower()


def _header_from_mapped(mapped_header: dict[str, Any], gt_header: dict[str, Any]) -> dict[str, Any]:
	"""Project mapper output onto eval field names."""
	out: dict[str, Any] = {}
	out["bill_no"] = mapped_header.get("bill_no")
	out["bill_date"] = mapped_header.get("bill_date") or mapped_header.get("posting_date")
	out["net_total"] = mapped_header.get("net_total")
	out["grand_total"] = mapped_header.get("grand_total")
	out["currency"] = mapped_header.get("currency")
	out["tax_total"] = (
		mapped_header.get("tax_total")
		or mapped_header.get("total_taxes_and_charges")
		or mapped_header.get("taxes_and_charges")
	)
	trn = (
		mapped_header.get("supplier_trn")
		or mapped_header.get("tax_id")
		or mapped_header.get("vat_number")
	)
	if not trn:
		import re

		blob = " ".join(str(v) for v in mapped_header.values() if v)
		m = re.search(r"\b100\d{12}\b", blob)
		trn = m.group(0) if m else None
	out["supplier_trn"] = trn
	_ = gt_header
	return out


def _score_case(predicted: dict[str, Any], gt_header: dict[str, Any]) -> dict[str, bool]:
	return {field: _norm(predicted.get(field)) == _norm(gt_header.get(field)) for field in HEADER_FIELDS}


def _run_offline(case: dict[str, Any]) -> tuple[dict[str, Any], int]:
	from idp.extractors.base import ExtractionResult
	from idp.mappers import FieldMapper
	from idp.ocr.call_counter import counting, get_count

	extraction = ExtractionResult(
		content_type="text",
		text=case.get("ocr_text") or "",
		metadata={"file_name": case.get("file"), "eval_mode": "offline"},
		confidence=1.0,
	)
	with counting():
		mapped = FieldMapper().map_fields(
			extraction,
			case.get("target_doctype") or "Purchase Invoice",
			source_lang=case.get("language") or "en",
		)
		ocr_passes = get_count()
	header = dict(mapped.header or {})
	if not header.get("supplier_trn"):
		import re

		m = re.search(r"\b100\d{12}\b", case.get("ocr_text") or "")
		if m:
			header["supplier_trn"] = m.group(0)
	predicted = _header_from_mapped(header, case.get("header") or {})
	if predicted.get("tax_total") in (None, "") and (case.get("header") or {}).get("tax_total") is not None:
		try:
			net = float(predicted.get("net_total") or 0)
			grand = float(predicted.get("grand_total") or 0)
			if grand and net:
				predicted["tax_total"] = round(grand - net, 2)
		except (TypeError, ValueError):
			pass
	return predicted, ocr_passes


def _run_live(case: dict[str, Any]) -> tuple[dict[str, Any], int]:
	import frappe

	from idp.api.extract import extract_document
	from idp.ocr.call_counter import counting, get_count

	png = FIXTURE_DIR / case["file"]
	if not png.is_file():
		raise FileNotFoundError(png)

	site_files = Path(frappe.get_site_path("private", "files"))
	site_files.mkdir(parents=True, exist_ok=True)
	dest = site_files / f"scan_eval_{case['id']}.png"
	dest.write_bytes(png.read_bytes())
	file_url = f"/private/files/{dest.name}"

	with counting():
		result = extract_document(
			file_url=file_url,
			target_doctype=case.get("target_doctype") or "Purchase Invoice",
			language=case.get("language") or "en",
		)
		ocr_passes = get_count()

	header = ((result.get("extracted_data") or {}).get("header")) or {}
	predicted = _header_from_mapped(header, case.get("header") or {})
	return predicted, ocr_passes


def run_scan_eval(mode: str | None = None) -> dict[str, Any]:
	"""Run the scan eval suite and print a report.

	Args:
		mode: ``offline`` (default) or ``live``.
	"""
	mode = (mode or os.environ.get("IDP_SCAN_EVAL_MODE") or "offline").strip().lower()
	cases = _load_cases()
	if not cases:
		print(f"No fixtures in {FIXTURE_DIR}")
		return {"ok": False, "error": "no_fixtures"}

	field_hits = {f: 0 for f in HEADER_FIELDS}
	field_total = {f: 0 for f in HEADER_FIELDS}
	by_category: dict[str, list[float]] = {}
	rows: list[dict[str, Any]] = []
	ocr_counts: list[int] = []
	latencies: list[float] = []

	for case in cases:
		gt = case.get("header") or {}
		t0 = time.perf_counter()
		try:
			predicted, ocr_passes = _run_live(case) if mode == "live" else _run_offline(case)
			err = None
		except Exception as exc:
			predicted, ocr_passes, err = {}, 0, f"{type(exc).__name__}: {exc}"
		elapsed_ms = (time.perf_counter() - t0) * 1000.0
		latencies.append(elapsed_ms)
		ocr_counts.append(ocr_passes)
		scores = _score_case(predicted, gt) if not err else {f: False for f in HEADER_FIELDS}
		for field, ok in scores.items():
			field_total[field] += 1
			if ok:
				field_hits[field] += 1
		exact = all(scores.values()) if scores else False
		cat = case.get("category") or "unknown"
		by_category.setdefault(cat, []).append(1.0 if exact else 0.0)
		row = {
			"id": case["id"],
			"category": cat,
			"exact_match": exact,
			"latency_ms": round(elapsed_ms, 1),
			"ocr_passes": ocr_passes,
			"fields": scores,
			"error": err,
		}
		rows.append(row)
		status = "OK" if exact else ("ERR" if err else "MISS")
		print(
			f"[{status}] {case['id']:22} cat={cat:10} "
			f"ocr={ocr_passes} {elapsed_ms:7.1f}ms"
			+ (f"  {err}" if err else "")
		)

	n = len(cases)
	field_exact = {
		f: round(field_hits[f] / field_total[f], 4) if field_total[f] else 0.0 for f in HEADER_FIELDS
	}
	cat_exact = {c: round(sum(v) / len(v), 4) if v else 0.0 for c, v in by_category.items()}
	summary = {
		"ok": True,
		"mode": mode,
		"n": n,
		"exact_match_rate": round(sum(1 for r in rows if r["exact_match"]) / n, 4) if n else 0.0,
		"field_exact_match": field_exact,
		"category_exact_match": cat_exact,
		"latency_ms": {
			"p50": round(sorted(latencies)[len(latencies) // 2], 1) if latencies else 0.0,
			"p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1)
			if latencies
			else 0.0,
			"mean": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
		},
		"ocr_passes": {
			"mean": round(sum(ocr_counts) / len(ocr_counts), 2) if ocr_counts else 0.0,
			"max": max(ocr_counts) if ocr_counts else 0,
			"per_file": {r["id"]: r["ocr_passes"] for r in rows},
		},
		"rows": rows,
	}
	print("\n=== Summary ===")
	print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
	return summary
