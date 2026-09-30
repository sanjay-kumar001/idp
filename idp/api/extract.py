# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Document extraction API endpoint.

Orchestrates the full IDP extraction pipeline:
file resolution -> content extraction -> OCR (if needed) ->
field mapping -> validation -> return for user review.
"""

import time

import frappe

from idp.core.audit import log_bank_event, log_extraction_event
from idp.core.config import get_default_company
from idp.core.constants import SUPPORTED_DOCTYPES
from idp.core.exceptions import IDPError, RateLimitExceededError, SecurityError
from idp.core.logger import get_logger
from idp.core.rate_limit import check_and_consume
from idp.core.security import assert_safe_file_url, assert_user_can_read
from idp.extractors import extract_content
from idp.mappers import FieldMapper, MappedDocument
from idp.validators import validate_business_rules, validate_schema

logger = get_logger("idp.api.extract")


@frappe.whitelist()
def extract_document(
	file_url: str,
	target_doctype: str,
	company: str | None = None,
	language: str = "en",
) -> dict:
	"""Extract structured data from an uploaded document.

	Runs the full IDP pipeline:

	1. Resolve and extract file content (Phase 3).
	2. OCR if image or scanned PDF (Phase 2 — transparent).
	3. Map extracted content to *target_doctype* schema (Phase 4).
	4. Validate mapped data (Phase 5).
	5. Return mapped data for user review before creation.

	Args:
		file_url: Frappe file URL (e.g. ``/private/files/invoice.pdf``).
		target_doctype: ERPNext DocType to map to (e.g. ``Purchase Invoice``).
		company: Company context. Falls back to ``get_default_company()``.
		language: OCR language code (default ``en``).

	Returns:
		dict with keys:
			- ``success``: bool
			- ``extracted_data``: ``{header: {...}, items: [...]}``
			- ``unmapped_fields``: list of fields that could not be mapped
			- ``validation``: ``{is_valid, errors, warnings}``
			- ``confidence``: float (average extraction confidence)
			- ``processing_time_ms``: int
	"""
	start = time.monotonic()
	company = company or get_default_company()

	# --- Input validation ---
	if not file_url:
		frappe.throw("file_url is required.", frappe.ValidationError)

	if target_doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(
			f'Unsupported target DocType: "{target_doctype}". Supported: {", ".join(SUPPORTED_DOCTYPES)}',
			frappe.ValidationError,
		)

	# --- Phase 14 hardening: rate limit + security pre-flight ---
	try:
		check_and_consume()
		assert_safe_file_url(file_url)
		assert_user_can_read(target_doctype)
	except RateLimitExceededError as exc:
		logger.info("Extraction rejected by rate limiter: %s", exc)
		log_extraction_event(
			file_url=file_url,
			target_doctype=target_doctype,
			success=False,
			processing_time_ms=0,
			language=language,
			error_message=f"RateLimitExceededError: {exc}",
			company=company,
		)
		return {
			"success": False,
			"error": str(exc),
			"error_type": "RateLimitExceededError",
			"details": exc.details,
		}
	except SecurityError as exc:
		logger.warning("Extraction blocked by security guard: %s", exc)
		log_extraction_event(
			file_url=file_url,
			target_doctype=target_doctype,
			success=False,
			processing_time_ms=0,
			language=language,
			error_message=f"SecurityError: {exc}",
			company=company,
		)
		return {
			"success": False,
			"error": str(exc),
			"error_type": "SecurityError",
			"details": exc.details,
		}

	try:
		# 1. Extract content from file
		extraction = extract_content(file_url, lang=language)

		# 2. Map to target DocType schema
		mapper = FieldMapper()
		mapped: MappedDocument = mapper.map_fields(
			extraction, target_doctype, company=company, source_lang=language
		)

		# 3. Validate — gated by IDP Settings.enable_pre_validation so users
		# can short-circuit schema checks before the LLM stage.
		_pre_val_on = bool(
			frappe.db.get_single_value("IDP Settings", "enable_pre_validation")
		)
		if _pre_val_on:
			schema_result = validate_schema(mapped, company)
			biz_warnings = validate_business_rules(mapped, company)
		else:
			from idp.validators.schema_validator import ValidationResult

			schema_result = ValidationResult(
				is_valid=True, errors=[], warnings=[], resolved_links={}
			)
			biz_warnings = []

		# Build validation summary
		validation = {
			"is_valid": schema_result.is_valid,
			"errors": [
				{"field": e.field, "message": e.message, "severity": e.severity} for e in schema_result.errors
			],
			"warnings": (
				[
					{"field": w.field, "message": w.message, "severity": w.severity}
					for w in schema_result.warnings
				]
				+ [{"field": "", "message": w, "severity": "warning"} for w in biz_warnings]
			),
			"resolved_links": schema_result.resolved_links,
			"missing_masters": schema_result.missing_masters,
		}

		elapsed_ms = int((time.monotonic() - start) * 1000)

		logger.info(
			f"Extraction complete | doctype={target_doctype} file={file_url} "
			f"valid={schema_result.is_valid} time={elapsed_ms}ms"
		)

		log_extraction_event(
			file_url=file_url,
			target_doctype=target_doctype,
			success=True,
			processing_time_ms=elapsed_ms,
			confidence=extraction.confidence,
			language=language,
			extraction_data={"header": mapped.header, "items": mapped.items},
			validation_errors=validation["errors"] + validation["warnings"],
			company=company,
		)

		return {
			"success": True,
			"extracted_data": {
				"doctype": mapped.doctype,
				"header": mapped.header,
				"items": mapped.items,
			},
			"unmapped_fields": mapped.unmapped_fields,
			"confidence_scores": mapped.confidence_scores,
			"validation": validation,
			"confidence": extraction.confidence,
			"processing_time_ms": elapsed_ms,
		}

	except IDPError as exc:
		elapsed_ms = int((time.monotonic() - start) * 1000)
		logger.warning(f"Extraction failed | file={file_url} error={exc}")
		log_extraction_event(
			file_url=file_url,
			target_doctype=target_doctype,
			success=False,
			processing_time_ms=elapsed_ms,
			language=language,
			error_message=f"{type(exc).__name__}: {exc}",
			company=company,
		)
		return {
			"success": False,
			"error": str(exc),
			"error_type": type(exc).__name__,
			"details": exc.details,
			"processing_time_ms": elapsed_ms,
		}

	except Exception as exc:
		elapsed_ms = int((time.monotonic() - start) * 1000)
		logger.error(f"Unexpected extraction error | file={file_url} error={exc}")
		log_extraction_event(
			file_url=file_url,
			target_doctype=target_doctype,
			success=False,
			processing_time_ms=elapsed_ms,
			language=language,
			error_message=f"{type(exc).__name__}: {exc}",
			company=company,
		)
		frappe.throw(
			f"Extraction failed: {exc}",
			title="IDP Extraction Error",
		)


# ---------------------------------------------------------------------------
# Phase 12 — Bank statement endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist()
def extract_bank_statement_api(file_url: str, language: str = "en") -> dict:
	"""Extract a bank statement into a structured transaction list.

	Args:
		file_url: Frappe file URL.
		language: OCR language code (default ``en``).

	Returns dict with keys:
		- ``success``: bool
		- ``statement``: serialised :class:`BankStatement` payload
		- ``processing_time_ms``: int
		- ``error`` / ``error_type``: populated on failure
	"""
	from idp.extractors.bank_statement import (
		extract_bank_statement, statement_to_dict,
	)

	start = time.monotonic()

	if not file_url:
		frappe.throw("file_url is required.", frappe.ValidationError)

	try:
		statement = extract_bank_statement(file_url, lang=language)
		elapsed_ms = int((time.monotonic() - start) * 1000)
		logger.info(
			"Bank statement extracted | file=%s txns=%d time=%dms",
			file_url, len(statement.transactions), elapsed_ms,
		)
		log_bank_event(
			file_url=file_url,
			success=True,
			processing_time_ms=elapsed_ms,
			transaction_count=len(statement.transactions),
		)
		return {
			"success": True,
			"statement": statement_to_dict(statement),
			"processing_time_ms": elapsed_ms,
		}
	except IDPError as exc:
		elapsed_ms = int((time.monotonic() - start) * 1000)
		logger.warning("Bank statement extraction failed | file=%s error=%s", file_url, exc)
		log_bank_event(
			file_url=file_url,
			success=False,
			processing_time_ms=elapsed_ms,
			error_message=f"{type(exc).__name__}: {exc}",
		)
		return {
			"success": False,
			"error": str(exc),
			"error_type": type(exc).__name__,
			"details": exc.details,
			"processing_time_ms": elapsed_ms,
		}


@frappe.whitelist()
def reconcile_bank_statement_api(
	bank_account: str,
	transactions: str | list,
	company: str | None = None,
) -> dict:
	"""Reconcile a list of parsed transactions against ERPNext records.

	Args:
		bank_account: ERPNext Account for the bank account.
		transactions: JSON string or list of transaction dicts as produced
			by ``statement_to_dict`` (the ``transactions`` key).
		company: Optional company filter.

	Returns:
		dict containing the serialised ``ReconciliationResult``.
	"""
	import json

	from idp.reconciliation.bank_reconciliation import reconcile_bank_statement, result_to_dict
	from idp.extractors.bank_statement import transactions_from_dicts

	if not bank_account:
		frappe.throw("bank_account is required.", frappe.ValidationError)

	if isinstance(transactions, str):
		try:
			rows = json.loads(transactions)
		except ValueError as exc:
			frappe.throw(f"transactions must be JSON: {exc}", frappe.ValidationError)
	else:
		rows = transactions

	if not isinstance(rows, list):
		frappe.throw("transactions must be a list of dicts.", frappe.ValidationError)

	txns = transactions_from_dicts(rows)
	result = reconcile_bank_statement(
		transactions=txns,
		bank_account=bank_account,
		company=company or None,
	)
	log_bank_event(
		file_url="<reconcile>",
		success=True,
		processing_time_ms=0,
		transaction_count=result.total_count(),
		bank_account=bank_account,
		matched=len(result.matched),
		unmatched=len(result.unmatched),
		company=company or None,
	)
	return {"success": True, "reconciliation": result_to_dict(result)}
