# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""``propose_create_document`` — render the Phase 20 ConfirmationCard.

Non-mutating: never inserts the ERPNext document.  It builds the rich
card payload (per-field provenance, items + taxes with status, totals,
validation, pagination, missing-master prerequisites, action buttons)
that the chatbot UI renders so the user can review, edit, and confirm
before ``create_document`` runs.

Phase 25 — extends the payload so non-item DocTypes (Journal Entry,
Payment Entry, Payroll Entry) and very long item sets work cleanly:

* ``child_table_name`` is derived from the target DocType's schema
  rather than hard-coded to ``items``; the UI uses it to switch between
  the bespoke :class:`ItemMappingTable` (when it equals ``items``) and
  the generic :class:`GenericChildTable` for everything else.
* ``child_row_schema`` carries the child DocType's column metadata so
  the generic table can render the right columns without an extra
  round-trip.
* ``source_pages`` (card level) and ``source_page`` (per row) carry
  PDF page provenance forward from the extractor so the user can
  trace any row back to its origin.

Card payload shape (versioned by
:data:`idp.llm.schemas.CONFIRMATION_CARD_PAYLOAD_VERSION`)::

    {
      "version": 1,
      "card_type": "ConfirmationCard",
      "doctype": "Purchase Invoice",
      "company": "Acme",
      "file_id": "file_1",
      "source_pages": [1, 3, 5],
      "child_table_name": "items",         # Phase 25
      "child_row_schema": [...],           # Phase 25
      "summary": "...",
      "header": [
         {"fieldname": "supplier", "value": "...", "confidence": 0.92,
          "source": "rule" | "llm" | "template", "editable": True}, ...
      ],
      "items": {
         "page": 1, "page_size": 10, "total": 23,
         "rows": [
            {"index": 0, "data": {...}, "status": "Existing"|"New",
             "erpnext_item": "ITEM-0001", "source_page": 2,
             "item_mapping_suggestions": [{name, label, score}], ...},
            ...
         ]
      },
      "taxes": {
         "rows": [
            {"row_index": 0,
             "extracted": {"account": "IGST", "rate": 0.18, "tax_amount": 1800},
             "erpnext_account": "IGST - ACME",
             "status": "Existing" | "New",
             "tax_mapping_suggestions": [{name, label, score}, ...]}
         ]
      },
      "totals": {"net_total": ..., "total_taxes_and_charges": ..., "grand_total": ...},
      "validation": {"schema_errors": [...], "schema_warnings": [...],
                     "business_rule_warnings": [...]},
      "missing_masters": [{...}, ...],
      "actions": [{"id": "submit", "label": "Submit", "primary": True}, ...]
    }
"""

from __future__ import annotations

from typing import Any

import frappe

from idp.llm.schemas import CONFIRMATION_CARD_PAYLOAD_VERSION
from idp.tools.base import ToolContext, ToolResult, publish_progress, tool

_DEFAULT_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 100


_PARAMETERS_SCHEMA = {
	"type": "object",
	"properties": {
		"doctype": {"type": "string", "description": "Target ERPNext DocType."},
		"header": {
			"type": "object",
			"description": "Field name → value map for the parent doc.",
			"additionalProperties": True,
		},
		"items": {
			"type": "array",
			"description": (
				"Optional child-table rows.  For Item-bearing DocTypes "
				"(Sales/Purchase Invoice, Delivery Note, etc.) the rows "
				"are item lines.  For non-Item DocTypes (Journal Entry, "
				"Payment Entry, Payroll Entry) the rows are whatever the "
				"primary child table holds (accounts, references, …) — "
				"Phase 25 routes them through the generic child table UI."
			),
			"items": {"type": "object", "additionalProperties": True},
		},
		"taxes": {
			"type": "array",
			"description": (
				"Per-row tax breakdown (Phase 20).  Each entry is "
				"``{account, rate, tax_amount, taxable_amount?, description?}``."
			),
			"items": {"type": "object", "additionalProperties": True},
		},
		"file_id": {
			"type": "string",
			"description": "Source attachment alias (file_1, file_2, ...) for provenance.",
		},
		"source_pages": {
			"type": "array",
			"description": (
				"Phase 25 — PDF page numbers that contributed to this "
				"document.  Used by the UI as a trace breadcrumb."
			),
			"items": {"type": "integer", "minimum": 1},
		},
		"confidence_scores": {
			"type": "object",
			"description": (
				"Per-field confidence scores from the mapper.  Values may be a float "
				"or an object {score, source} carrying provenance."
			),
			"additionalProperties": True,
		},
		"warnings": {
			"type": "array",
			"description": "Validator warnings to surface alongside the fields.",
			"items": {"type": "string"},
		},
		"schema_errors": {
			"type": "array",
			"description": "Hard schema validation errors.",
			"items": {"type": "string"},
		},
		"missing_masters": {
			"type": "array",
			"description": "List of {doctype, name, ...} that need creation first.",
			"items": {"type": "object", "additionalProperties": True},
		},
		"source_regions": {
			"type": "object",
			"description": (
				"Phase 29 — optional per-field source attribution.  "
				"Keyed by fieldname (or ``items[N].fieldname`` for child "
				"rows), each value is ``{page, bbox?, mapper}``.  "
				"Surfaced in the UI as a click-to-source affordance."
			),
			"additionalProperties": True,
		},
		"summary": {
			"type": "string",
			"description": "Short user-facing summary of what is about to be created.",
		},
		"page_size": {
			"type": "integer",
			"description": "Items page size (default 10, max 100).",
			"minimum": 1,
			"maximum": _MAX_PAGE_SIZE,
		},
	},
	"required": ["doctype", "header"],
	"additionalProperties": False,
}


@tool(
	name="propose_create_document",
	description=(
		"Render a ConfirmationCard to the user with the extracted fields, "
		"per-row taxes (with editable account suggestions), warnings, and "
		"missing prerequisites.  This does NOT create the ERPNext document — "
		"the user must explicitly confirm in the UI before create_document is "
		"called.  Always call this before create_document."
	),
	parameters_schema=_PARAMETERS_SCHEMA,
)
def propose_create_document(arguments: dict, ctx: ToolContext) -> ToolResult:
	args = arguments or {}
	doctype = (args.get("doctype") or ctx.target_doctype or "").strip()
	if not doctype:
		return ToolResult.fail(
			"doctype is required",
			error_code="MISSING_ARGUMENT",
			stop_processing=False,
		)
	header = args.get("header") or {}
	if not isinstance(header, dict) or not header:
		return ToolResult.fail(
			"header object with at least one field is required",
			error_code="MISSING_ARGUMENT",
			stop_processing=False,
		)

	items_raw: list[dict] = list(args.get("items") or [])
	taxes_raw: list[dict] = list(args.get("taxes") or [])
	confidence_scores: dict = args.get("confidence_scores") or {}
	warnings: list[str] = list(args.get("warnings") or [])
	schema_errors: list[str] = list(args.get("schema_errors") or [])
	missing_masters: list[dict] = list(args.get("missing_masters") or [])
	file_id = args.get("file_id")
	source_pages = _normalise_source_pages(args.get("source_pages"))
	source_regions: dict = args.get("source_regions") or {}

	publish_progress(
		ctx,
		tool_name="propose_create_document",
		user_visible_message=f"Building {doctype} confirmation card…",
		stage="propose_start",
	)

	# Phase 25 — resolve the primary child table name + its row schema
	# from the target DocType meta.  Falls back to ``items`` when the
	# DocType has no child tables (or Frappe isn't loaded — unit tests).
	child_table_name, child_row_schema = _resolve_child_table_metadata(doctype)
	is_item_table = child_table_name == "items"

	# Run business-rule validation on a synthesised MappedDocument so the
	# card carries the same warnings the eventual create_document call
	# will produce.
	business_rule_warnings = _run_business_rules(doctype, header, items_raw, taxes_raw, ctx.company)

	# Header — emit as ordered list with provenance.  Phase 29 attaches
	# a per-field ``confidence_band`` (green/amber/red) and any captured
	# ``source`` region (page + bbox + mapper) so the UI can render dots
	# and a click-to-source affordance.
	header_rows = _build_header_rows(
		header,
		confidence_scores,
		doctype=doctype,
		source_regions=source_regions,
	)
	# Phase 24 — guarantee headline totals (``total``, ``net_total``,
	# ``grand_total``, ``total_taxes_and_charges``, ``rounded_total``)
	# always show up in the header strip, even when the LLM forgot to
	# include them in the ``header`` arg.  We compute them from the
	# items / taxes block when missing so the user can review the money
	# numbers without scrolling to the totals strip.
	#
	# Skip this for non-Item DocTypes (Journal Entry, Payment Entry, …)
	# whose primary child table isn't a list of priced items — synthesising
	# a "net_total" from a list of GL postings would be misleading.
	if is_item_table:
		header_rows = _ensure_total_rows(header_rows, header, items_raw, taxes_raw)

	# Items — annotate with status (New/Existing) and fuzzy suggestions
	# only when the child table actually holds Item rows.  For non-Item
	# DocTypes we keep the raw row data (with provenance) and let the
	# UI render a generic editable child-table grid.
	page_size = _clamp_page_size(args.get("page_size"))
	if is_item_table:
		items_payload = _build_items_payload(
			items_raw,
			page_size=page_size,
			doctype=doctype,
			source_regions=source_regions,
		)
	else:
		items_payload = _build_generic_rows_payload(items_raw, page_size=page_size)

	# Taxes — same treatment but always one page (rarely > 10 rows).
	taxes_payload = _build_taxes_payload(taxes_raw, company=ctx.company, doctype=doctype)

	totals = _extract_totals(header)

	summary = args.get("summary") or _default_summary(
		doctype,
		header,
		items_raw,
		taxes_raw,
		totals,
		missing_masters,
		schema_errors,
		business_rule_warnings,
	)

	card_payload: dict[str, Any] = {
		"version": CONFIRMATION_CARD_PAYLOAD_VERSION,
		"card_type": "ConfirmationCard",
		"doctype": doctype,
		"company": ctx.company,
		"file_id": file_id,
		"source_pages": source_pages,
		"child_table_name": child_table_name,
		"child_row_schema": child_row_schema,
		"summary": summary,
		"header": header_rows,
		"items": items_payload,
		"taxes": taxes_payload if is_item_table else {"rows": [], "total": 0},
		"totals": totals,
		"validation": {
			"schema_errors": schema_errors,
			"schema_warnings": warnings,
			"business_rule_warnings": business_rule_warnings,
		},
		"missing_masters": missing_masters,
		"actions": _default_actions(missing_masters, schema_errors),
	}

	# Terminal turn: the ConfirmationCard payload already carries everything
	# the user needs to review (summary, header, items, taxes, totals,
	# validation, missing_masters, action buttons).  Set stop_processing so
	# the agent loop emits one final assistant message synthesised from
	# ``card.summary`` instead of paying for another LLM round-trip just to
	# paraphrase the card we already rendered.
	return ToolResult(
		success=True,
		data={
			"awaiting_user_confirmation": True,
			"doctype": doctype,
			"items_total": len(items_raw),
			"taxes_total": len(taxes_raw),
			"missing_master_count": len(missing_masters),
		},
		card=card_payload,
		stop_processing=True,
	)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _build_header_rows(
	header: dict,
	confidence_scores: dict,
	*,
	doctype: str | None = None,
	source_regions: dict | None = None,
) -> list[dict]:
	from idp.llm.confidence import band_for

	regions = source_regions or {}
	rows: list[dict] = []
	for fieldname, value in header.items():
		conf, source = _split_confidence(confidence_scores.get(fieldname))
		row: dict[str, Any] = {
			"fieldname": fieldname,
			"value": value,
			"confidence": conf,
			"confidence_band": band_for(conf, doctype),
			"source": source,
			"editable": True,
		}
		region = regions.get(fieldname)
		if isinstance(region, dict) and region:
			# Pass-through with minimal normalisation.  The UI tolerates
			# a missing ``bbox`` (shows a "no region recorded" tooltip).
			row["source_region"] = {
				"page": region.get("page"),
				"bbox": region.get("bbox"),
				"mapper": region.get("mapper") or source,
			}
		rows.append(row)
	return rows


def _split_confidence(entry: Any) -> tuple[float | None, str | None]:
	if entry is None:
		return None, None
	if isinstance(entry, dict):
		score = entry.get("score")
		try:
			score_f = float(score) if score is not None else None
		except (TypeError, ValueError):
			score_f = None
		return score_f, entry.get("source")
	try:
		return float(entry), None
	except (TypeError, ValueError):
		return None, None


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def _build_items_payload(
	items: list[dict],
	*,
	page_size: int,
	doctype: str | None = None,
	source_regions: dict | None = None,
) -> dict:
	"""Build the paginated items block (§24.2).

	Runs the §24.1 matcher in a single pass over the *full* items list
	(so even hidden pages get statuses / candidates persisted), then
	slices out the first page for the inline card payload.
	"""

	total = len(items)
	match_results = _run_item_matcher(items)

	first_page = items[:page_size]
	rows = [
		_build_item_row(idx, row, match_results, doctype=doctype, source_regions=source_regions)
		for idx, row in enumerate(first_page)
	]
	return {
		"page": 1,
		"page_size": page_size,
		"total": total,
		"has_more": total > page_size,
		"rows": rows,
	}


def _build_generic_rows_payload(rows_raw: list[dict], *, page_size: int) -> dict:
	"""Phase 25 — paginated payload for non-Item child tables.

	Mirrors :func:`_build_items_payload` but skips the item-matcher
	(there is nothing to match for Journal Entry accounts / Payment
	Entry references) and emits a uniform row shape::

	    {"index": N, "data": {...}, "source_page": P}

	The frontend's :class:`GenericChildTable` consumes this directly.
	"""

	total = len(rows_raw)
	first_page = rows_raw[:page_size]
	rows: list[dict] = []
	for idx, row in enumerate(first_page):
		if not isinstance(row, dict):
			row = {}
		rows.append(
			{
				"index": idx,
				"data": dict(row),
				"source_page": _row_source_page(row),
			}
		)
	return {
		"page": 1,
		"page_size": page_size,
		"total": total,
		"has_more": total > page_size,
		"rows": rows,
	}


def _run_item_matcher(items: list[dict]) -> list[dict]:
	"""Invoke :func:`match_items` with safe fallback to per-row resolution.

	Returns a list (parallel to ``items``) of dicts with keys
	``status``, ``erpnext_item``, ``match_reason``, ``confidence``,
	``match_candidates``.  Falls back to the legacy ``_resolve_item``
	path when the matcher (or Frappe) is unavailable.
	"""

	if not items:
		return []
	try:
		from idp.mappers.item_matcher import match_items as _match_items

		results = _match_items(list(items))
		return [
			{
				"status": r.status,
				"erpnext_item": r.best_match,
				"match_reason": r.match_reason,
				"confidence": r.confidence,
				"match_candidates": [c.to_dict() for c in r.matches],
			}
			for r in results
		]
	except Exception:
		out: list[dict] = []
		for row in items:
			row = row if isinstance(row, dict) else {}
			needle = (row.get("item_code") or row.get("item") or row.get("item_name") or "").strip()
			resolved, status = _resolve_item(needle)
			out.append(
				{
					"status": status,
					"erpnext_item": resolved,
					"match_reason": "exact_code" if status == "Existing" else None,
					"confidence": 1.0 if status == "Existing" else 0.0,
					"match_candidates": [],
				}
			)
		return out


def _build_item_row(
	idx: int,
	row: dict,
	match_results: list[dict],
	*,
	doctype: str | None = None,
	source_regions: dict | None = None,
) -> dict:
	from idp.llm.confidence import band_for

	if not isinstance(row, dict):
		row = {}
	mr = match_results[idx] if idx < len(match_results) else {}
	status = mr.get("status") or "New"
	resolved_name = mr.get("erpnext_item")
	candidates = mr.get("match_candidates") or []

	# Backward-compat alias kept for the existing UI: emits the
	# {name, label, score} shape used since Phase 20.  The new field
	# (``match_candidates``) carries the richer §24.2 record.
	suggestions = [
		{
			"name": c.get("item_code"),
			"label": c.get("item_name"),
			"score": c.get("score"),
		}
		for c in candidates
	]

	confidence = mr.get("confidence")
	region_key = f"items[{idx}]"
	region = (source_regions or {}).get(region_key)
	out = {
		"index": idx,
		"data": row,
		"status": status,
		"erpnext_item": resolved_name,
		"match_reason": mr.get("match_reason"),
		"confidence": confidence,
		"confidence_band": band_for(confidence, doctype),
		"match_candidates": candidates,
		"item_mapping_suggestions": suggestions,
		# Phase 25 — per-row PDF page reference for traceability.
		"source_page": _row_source_page(row),
	}
	if isinstance(region, dict) and region:
		out["source_region"] = {
			"page": region.get("page"),
			"bbox": region.get("bbox"),
			"mapper": region.get("mapper"),
		}
	return out


def _resolve_item(item_code: str) -> tuple[str | None, str]:
	if not item_code:
		return None, "New"
	try:
		import frappe
	except ImportError:
		return None, "New"
	try:
		if frappe.db.exists("Item", item_code):
			return item_code, "Existing"
		# Try matching on item_name as well.
		row = frappe.db.get_value("Item", {"item_name": item_code}, "name")
		if row:
			return str(row), "Existing"
	except Exception:
		pass
	return None, "New"


# ---------------------------------------------------------------------------
# Taxes
# ---------------------------------------------------------------------------


def _build_taxes_payload(taxes: list[dict], *, company: str | None, doctype: str | None = None) -> dict:
	"""Build the per-row tax payload using the §24.3 matcher.

	Falls back to a per-row :func:`_resolve_account` lookup when the
	matcher (or Frappe) is unavailable so unit tests / CLI runs still
	produce a valid card.
	"""
	from idp.llm.confidence import band_for

	tax_results = _run_tax_matcher(taxes, company=company)

	rows: list[dict] = []
	for idx, row in enumerate(taxes):
		if not isinstance(row, dict):
			continue
		account = (row.get("account") or row.get("account_head") or "").strip()
		mr = tax_results[idx] if idx < len(tax_results) else {}
		status = mr.get("status") or "New"
		resolved = mr.get("erpnext_account")
		candidates = mr.get("match_candidates") or []

		# Backward-compat alias for the existing UI.
		suggestions = [
			{
				"name": c.get("account_name"),
				"label": c.get("display_name"),
				"score": c.get("score"),
			}
			for c in candidates
		]

		rows.append(
			{
				"row_index": idx,
				"extracted": {
					"account": account,
					"rate": row.get("rate"),
					"tax_amount": row.get("tax_amount") or row.get("amount"),
					"taxable_amount": row.get("taxable_amount") or row.get("base"),
					"description": row.get("description"),
				},
				"erpnext_account": resolved,
				"status": status,
				"match_reason": mr.get("match_reason"),
				"confidence": mr.get("confidence"),
				"confidence_band": band_for(mr.get("confidence"), doctype),
				"match_candidates": candidates,
				"tax_mapping_suggestions": suggestions,
				"editable": True,
			}
		)
	return {"rows": rows, "total": len(rows)}


def _run_tax_matcher(taxes: list[dict], *, company: str | None) -> list[dict]:
	if not taxes:
		return []
	try:
		from idp.mappers.tax_matcher import match_taxes as _match_taxes

		results = _match_taxes(list(taxes), company=company)
		return [
			{
				"status": r.status,
				"erpnext_account": r.best_match,
				"match_reason": r.match_reason,
				"confidence": r.confidence,
				"match_candidates": [c.to_dict() for c in r.matches],
			}
			for r in results
		]
	except Exception:
		out: list[dict] = []
		for row in taxes:
			row = row if isinstance(row, dict) else {}
			account = (row.get("account") or row.get("account_head") or "").strip()
			resolved, status = _resolve_account(account, company=company)
			out.append(
				{
					"status": status,
					"erpnext_account": resolved,
					"match_reason": "exact_name" if status == "Existing" else None,
					"confidence": 1.0 if status == "Existing" else 0.0,
					"match_candidates": [],
				}
			)
		return out


def _resolve_account(account: str, *, company: str | None) -> tuple[str | None, str]:
	if not account:
		return None, "New"
	try:
		import frappe
	except ImportError:
		return None, "New"
	try:
		if frappe.db.exists("Account", account):
			return account, "Existing"
		filters: dict[str, Any] = {"account_name": account}
		if company:
			filters["company"] = company
		row = frappe.db.get_value("Account", filters, "name")
		if row:
			return str(row), "Existing"
	except Exception:
		pass
	return None, "New"


# ---------------------------------------------------------------------------
# Totals & validation
# ---------------------------------------------------------------------------


def _extract_totals(header: dict) -> dict:
	return {
		"net_total": header.get("net_total"),
		"total_taxes_and_charges": (header.get("total_taxes_and_charges") or header.get("taxes_and_charges")),
		"grand_total": header.get("grand_total"),
		"rounded_total": header.get("rounded_total"),
	}


# Fields the user expects to see in the header strip alongside the
# extracted business data, in display order.  When the LLM forgets to
# include them in ``header``, we synthesise them from items/taxes.
_TOTAL_FIELDS = ("total", "net_total", "total_taxes_and_charges", "grand_total", "rounded_total")


def _coerce_amount(v: Any) -> float | None:
	if v is None or v == "":
		return None
	try:
		return float(v)
	except (TypeError, ValueError):
		return None


def _ensure_total_rows(
	header_rows: list[dict],
	header: dict,
	items: list[dict],
	taxes: list[dict],
) -> list[dict]:
	"""Append missing total fields to *header_rows* with computed values.

	Idempotent: fields already present in *header_rows* are left
	untouched.  Computed values use the items/taxes blocks so the user
	always sees a number, even when the extractor / LLM didn't ship the
	totals explicitly.
	"""

	present = {r.get("fieldname") for r in header_rows if isinstance(r, dict)}
	missing = [f for f in _TOTAL_FIELDS if f not in present]
	if not missing:
		return header_rows

	# Compute fallbacks from items/taxes so we never display a blank
	# row.  These match ERPNext's own arithmetic for the standard
	# Sales/Purchase Invoice flow.
	def _line_amount(it: dict) -> float:
		amt = _coerce_amount(it.get("amount"))
		if amt is not None:
			return amt
		qty = _coerce_amount(it.get("qty")) or 0.0
		rate = _coerce_amount(it.get("rate")) or 0.0
		return qty * rate

	items_total = sum(_line_amount(it) for it in items if isinstance(it, dict))
	tax_total = sum(_coerce_amount(t.get("tax_amount")) or 0.0 for t in taxes if isinstance(t, dict))

	derived = {
		"total": _coerce_amount(header.get("total")) or items_total,
		"net_total": _coerce_amount(header.get("net_total")) or items_total,
		"total_taxes_and_charges": (
			_coerce_amount(header.get("total_taxes_and_charges"))
			or _coerce_amount(header.get("taxes_and_charges"))
			or tax_total
		),
		"grand_total": (
			_coerce_amount(header.get("grand_total"))
			or (items_total + tax_total)
		),
		"rounded_total": (
			_coerce_amount(header.get("rounded_total"))
			or round(items_total + tax_total, 2)
		),
	}

	for fn in missing:
		val = derived.get(fn)
		if val is None:
			continue
		header_rows.append(
			{
				"fieldname": fn,
				"value": round(val, 2) if isinstance(val, float) else val,
				"confidence": None,
				"source": "computed",
				"editable": False,
			}
		)
	return header_rows


def _run_business_rules(
	doctype: str,
	header: dict,
	items: list[dict],
	taxes: list[dict],
	company: str | None,
) -> list[str]:
	"""Run the business-rules validator on a synthesised MappedDocument.

	Gated by ``IDP Settings.enable_pre_validation``: when the admin has
	turned the pre-flight check off, this returns ``[]`` immediately and
	the card renders without auto-generated warnings.  The LLM
	``validate_document`` tool stays available regardless — the toggle
	only controls the *automatic* validation step.

	Failures here never abort the card render — we just append the
	warnings to ``validation.business_rule_warnings``.
	"""

	try:
		from idp.core.config import is_feature_enabled
	except Exception:
		is_feature_enabled = None
	if is_feature_enabled is not None and not is_feature_enabled("enable_pre_validation"):
		return []

	try:
		from idp.mappers.base import MappedDocument
		from idp.validators.business_rules import validate_business_rules
	except Exception:
		return []
	try:
		mapped = MappedDocument(
			doctype=doctype,
			header=dict(header),
			items=list(items),
			taxes=list(taxes),
		)
		return validate_business_rules(mapped, company or "")
	except Exception:
		return []


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _clamp_page_size(value: Any) -> int:
	"""Resolve the items page size.

	Precedence: explicit tool arg → ``IDP Settings.confirmation_page_size``
	→ :data:`_DEFAULT_PAGE_SIZE`.  Always clamped to ``[1, _MAX_PAGE_SIZE]``.
	"""

	if value is not None:
		try:
			return max(1, min(int(value), _MAX_PAGE_SIZE))
		except (TypeError, ValueError):
			pass
	# Phase 25 — fall back to the admin-tuned default in IDP Settings.
	settings_value = _settings_page_size()
	if settings_value is not None:
		return max(1, min(settings_value, _MAX_PAGE_SIZE))
	return _DEFAULT_PAGE_SIZE


def _settings_page_size() -> int | None:
	"""Best-effort read of ``IDP Settings.confirmation_page_size``."""

	try:
		from idp.core.config import get_idp_settings
	except Exception:
		return None
	try:
		settings = get_idp_settings()
	except Exception:
		return None
	raw = (settings or {}).get("confirmation_page_size") if isinstance(settings, dict) else None
	if raw in (None, "", 0):
		return None
	try:
		v = int(raw)
	except (TypeError, ValueError):
		return None
	# IDP Settings is admin-tunable; honour it even when slightly out of
	# the §25.3 documented range (5–50) but never below 1.
	return max(1, v)


def _normalise_source_pages(value: Any) -> list[int]:
	"""Sanitise the ``source_pages`` argument into a sorted unique int list."""

	if not value:
		return []
	if isinstance(value, (int, str)):
		value = [value]
	out: set[int] = set()
	for v in value:
		try:
			n = int(v)
		except (TypeError, ValueError):
			continue
		if n > 0:
			out.add(n)
	return sorted(out)


_SOURCE_PAGE_KEYS = ("source_page", "page", "page_number", "pdf_page", "_page")


def _row_source_page(row: Any) -> int | None:
	"""Pluck the per-row PDF page from common keys produced by the extractor."""

	if not isinstance(row, dict):
		return None
	for key in _SOURCE_PAGE_KEYS:
		if key in row and row[key] not in (None, ""):
			try:
				n = int(row[key])
			except (TypeError, ValueError):
				continue
			if n > 0:
				return n
	return None


def _resolve_child_table_metadata(doctype: str) -> tuple[str, list[dict]]:
	"""Phase 25 — derive the primary child table fieldname + row schema.

	Returns ``(child_table_name, child_row_schema)``.  Falls back to
	``("items", [])`` when Frappe (or the DocType meta) is unavailable
	so unit-test fixtures and CLI runs still get a sane card.
	"""

	if not doctype:
		return "items", []
	try:
		from idp.mappers.base import get_doctype_schema
	except Exception:
		return "items", []
	try:
		schema = get_doctype_schema(doctype)
	except Exception:
		return "items", []
	child_tables = schema.get("child_tables") or {}
	if not isinstance(child_tables, dict) or not child_tables:
		return "items", []

	# Prefer ``items`` when present (the most common shape), otherwise
	# pick the first declared child table.  We deliberately ignore
	# ``taxes`` — that table is rendered by its own dedicated UI block.
	preferred_order = ("items", "accounts", "references", "earnings", "deductions")
	for name in preferred_order:
		if name in child_tables:
			return name, _summarise_child_fields(child_tables[name])

	# Pick the first non-taxes table as the primary.
	for name, info in child_tables.items():
		if name == "taxes":
			continue
		return name, _summarise_child_fields(info)

	# Only had ``taxes`` — treat it as the primary so the user still
	# sees something.  Practically unreachable for ERPNext core.
	first_name = next(iter(child_tables))
	return first_name, _summarise_child_fields(child_tables[first_name])


def _summarise_child_fields(child_info: Any) -> list[dict]:
	"""Trim a child-table schema dict down to the fields the UI needs."""

	if not isinstance(child_info, dict):
		return []
	fields = child_info.get("fields") or []
	out: list[dict] = []
	for f in fields:
		if not isinstance(f, dict):
			continue
		fn = f.get("fieldname")
		if not fn:
			continue
		out.append(
			{
				"fieldname": fn,
				"fieldtype": f.get("fieldtype") or "Data",
				"label": f.get("label") or fn,
				"reqd": int(f.get("reqd") or 0),
				"options": f.get("options") or "",
			}
		)
	return out


_PARTY_FIELDS = (
	"supplier",
	"supplier_name",
	"customer",
	"customer_name",
	"party",
	"party_name",
)
_DOC_NUMBER_FIELDS = (
	"bill_no",
	"supplier_invoice_no",
	"name",
	"invoice_number",
	"po_no",
	"reference_no",
)
_DOC_DATE_FIELDS = (
	"bill_date",
	"posting_date",
	"transaction_date",
	"invoice_date",
	"date",
)


def _first(header: dict, keys: tuple[str, ...]) -> str | None:
	for k in keys:
		v = header.get(k)
		if v not in (None, ""):
			s = str(v).strip()
			if s:
				return s
	return None


def _format_amount(value: Any, currency: str | None) -> str | None:
	if value in (None, ""):
		return None
	try:
		n = float(value)
	except (TypeError, ValueError):
		return str(value)
	formatted = f"{n:,.2f}"
	if currency:
		return f"{formatted} {currency}"
	return formatted


def _default_summary(
	doctype: str,
	header: dict,
	items: list[dict],
	taxes: list[dict],
	totals: dict,
	missing_masters: list[dict],
	schema_errors: list[str],
	business_rule_warnings: list[str],
) -> str:
	"""Deterministic, user-facing summary rendered alongside the card.

	This text is shown to the user *as the assistant's reply* — it
	replaces the LLM round-trip that previously paraphrased the card
	payload.  Keep it dense (5-7 short lines max) and fact-only.
	"""

	currency = header.get("currency") or None
	party = _first(header, _PARTY_FIELDS)
	doc_no = _first(header, _DOC_NUMBER_FIELDS)
	doc_date = _first(header, _DOC_DATE_FIELDS)

	headline = f"Prepared {doctype}"
	if party:
		headline += f" for {party}"
	if doc_no:
		headline += f" (#{doc_no})"
	if doc_date:
		headline += f" dated {doc_date}"
	headline += "."

	bullets: list[str] = []

	# Line items / taxes counts.
	count_bits: list[str] = []
	if items:
		count_bits.append(f"{len(items)} line item(s)")
	if taxes:
		count_bits.append(f"{len(taxes)} tax row(s)")
	if count_bits:
		bullets.append("Contains " + " and ".join(count_bits) + ".")

	# Totals — only show what the document actually carried.
	total_bits: list[str] = []
	net = _format_amount(totals.get("net_total"), currency)
	tax = _format_amount(totals.get("total_taxes_and_charges"), currency)
	grand = _format_amount(totals.get("grand_total"), currency)
	if net:
		total_bits.append(f"Net {net}")
	if tax:
		total_bits.append(f"Tax {tax}")
	if grand:
		total_bits.append(f"Grand Total {grand}")
	if total_bits:
		bullets.append(" | ".join(total_bits))

	taxmate = "taxmate" in frappe.get_installed_apps()
	# Blockers / warnings the user should notice.
	if missing_masters:
		if taxmate:
			bullets.append(
				f"{len(missing_masters)} line(s) need an existing Item before the draft can be created."
			)
		else:
			bullets.append(
				f"{len(missing_masters)} missing master(s) need approval before submit."
			)
	if schema_errors:
		bullets.append(f"{len(schema_errors)} schema error(s) must be fixed.")
	elif business_rule_warnings:
		bullets.append(f"{len(business_rule_warnings)} validation warning(s) to review.")

	if taxmate:
		bullets.append("Review the card below and create a draft Purchase Invoice.")
	else:
		bullets.append("Review the card below and click Submit to create the record.")

	return headline + "\n\n" + "\n".join(f"- {b}" for b in bullets)


def _default_actions(missing_masters: list[dict], schema_errors: list[str]) -> list[dict]:
	# TaxMate clerks only save drafts; Submit stays on the Purchase Invoice form.
	if "taxmate" in frappe.get_installed_apps():
		return [
			{"id": "save_draft", "label": "Create draft invoice", "primary": True, "disabled": False},
			{"id": "edit", "label": "Edit", "primary": False, "disabled": False},
			{"id": "cancel", "label": "Discard", "primary": False, "disabled": False},
		]
	# Submit is disabled (advisory only — UI enforces) when blockers exist.
	blocked = bool(missing_masters) or bool(schema_errors)
	return [
		{"id": "submit", "label": "Submit", "primary": True, "disabled": blocked},
		{"id": "save_draft", "label": "Save as Draft", "primary": False, "disabled": False},
		{"id": "edit", "label": "Edit", "primary": False, "disabled": False},
		{"id": "cancel", "label": "Cancel", "primary": False, "disabled": False},
	]


__all__ = ["propose_create_document"]
