# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Rule-based field mapping engine.

Maps extracted document content to ERPNext DocType fields using keyword
matching, regex-based value-type detection, and fuzzy link resolution —
no LLM required.
"""

import re
from typing import ClassVar

import frappe

from idp.core.logger import get_logger
from idp.extractors.base import ExtractionResult
from idp.mappers.base import MappedDocument, get_doctype_schema
from idp.mappers.keywords_ml import (
	SUPPORTED_LANGUAGES,
	merge_keywords_for_languages,
	normalize_language,
)

logger = get_logger("idp.mappers")


# ---------------------------------------------------------------------------
# Field alias normalization
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, str] = {
	"terms_and_conditions": "terms",
	"bank_details": "remarks",
	"contact_person": "contact_display",
	"vat": "taxes",
	"gst": "taxes",
	"tax_amount": "total_taxes_and_charges",
	"subtotal": "net_total",
	"grand_total": "grand_total",
	"total_amount": "grand_total",
}

# Display-field used by Link doctypes for fuzzy matching
LINK_DISPLAY_FIELDS: dict[str, str] = {
	"Customer": "customer_name",
	"Supplier": "supplier_name",
	"Item": "item_name",
	"Employee": "employee_name",
	"Account": "account_name",
	"Cost Center": "cost_center_name",
	"Warehouse": "warehouse_name",
	"Company": "company_name",
}


class FieldMapper:
	"""Rule-based engine to map extracted text fields to a DocType schema."""

	# ------------------------------------------------------------------
	# Keyword → fieldname mappings per DocType
	# ------------------------------------------------------------------
	FIELD_KEYWORDS: ClassVar[dict[str, dict[str, list[str]]]] = {
		"Purchase Invoice": {
			"supplier": ["supplier", "vendor", "seller", "bill from", "sold by"],
			"posting_date": ["invoice date", "bill date", "dated", "inv date"],
			"due_date": ["due date", "payment due", "due by", "pay by"],
			"bill_no": ["invoice no", "invoice number", "bill no", "inv no", "ref no"],
			"taxes_and_charges": ["tax", "vat", "gst", "tax amount", "tax total"],
			"net_total": ["subtotal", "sub total", "net total", "net amount"],
			"grand_total": ["grand total", "total amount", "amount due", "balance due"],
			"remarks": ["remarks", "notes", "memo", "comments"],
			"terms": ["terms", "terms and conditions", "payment terms"],
			"currency": ["currency"],
		},
		"Sales Invoice": {
			"customer": ["customer", "buyer", "client", "bill to", "sold to"],
			"posting_date": ["invoice date", "bill date", "dated", "inv date"],
			"due_date": ["due date", "payment due", "due by"],
			"po_no": ["po no", "po number", "purchase order", "your order"],
			"taxes_and_charges": ["tax", "vat", "gst", "tax amount"],
			"net_total": ["subtotal", "sub total", "net total"],
			"grand_total": ["grand total", "total amount"],
			"currency": ["currency"],
		},
		"Purchase Order": {
			"supplier": ["supplier", "vendor", "seller"],
			"transaction_date": ["order date", "po date", "dated"],
			"schedule_date": ["delivery date", "expected date", "required by"],
			"net_total": ["subtotal", "sub total", "net total"],
			"grand_total": ["grand total", "total amount"],
			"currency": ["currency"],
		},
		"Sales Order": {
			"customer": ["customer", "buyer", "client", "bill to"],
			"transaction_date": ["order date", "so date", "dated"],
			"delivery_date": ["delivery date", "ship date", "expected date"],
			"po_no": ["po no", "po number", "purchase order", "your ref"],
			"net_total": ["subtotal", "sub total", "net total"],
			"grand_total": ["grand total", "total amount"],
			"currency": ["currency"],
		},
		"Quotation": {
			"party_name": ["customer", "buyer", "client", "quote to"],
			"transaction_date": ["quotation date", "quote date", "dated"],
			"valid_till": ["valid till", "validity", "expiry date", "valid until"],
			"net_total": ["subtotal", "sub total", "net total"],
			"grand_total": ["grand total", "total amount"],
			"currency": ["currency"],
		},
		"Payment Entry": {
			"party": ["party", "customer", "supplier", "vendor", "paid to", "received from"],
			"posting_date": ["payment date", "dated"],
			"paid_amount": ["paid amount", "payment amount"],
			"reference_no": ["ref no", "cheque no", "check no", "utr"],
			"reference_date": ["reference date", "cheque date", "check date"],
			"mode_of_payment": ["payment mode", "payment method"],
		},
		"Journal Entry": {
			"posting_date": ["journal date", "entry date", "dated"],
			"cheque_no": ["ref no", "cheque no", "check no"],
			"cheque_date": ["reference date", "cheque date", "check date"],
			"total_debit": ["total debit", "debit total"],
			"total_credit": ["total credit", "credit total"],
			"remark": ["remarks", "narration", "memo"],
		},
		"Opportunity": {
			"party_name": ["customer", "lead", "prospect", "company name", "client"],
			"opportunity_from": ["opportunity from", "party type"],
			"transaction_date": ["opportunity date", "dated"],
			"expected_closing": ["closing date", "expected closing"],
			"opportunity_amount": ["opportunity amount", "deal value", "deal size"],
			"opportunity_type": ["opportunity type"],
			"sales_stage": ["sales stage", "pipeline stage"],
			"probability": ["probability", "likelihood"],
			"currency": ["currency"],
			"utm_source": ["lead source", "campaign source"],
			"contact_person": ["contact person", "poc"],
			"contact_email": ["contact email", "email address"],
			"contact_mobile": ["contact mobile", "contact phone"],
		},
		"Supplier Quotation": {
			"supplier": ["supplier", "vendor", "seller", "quoted by"],
			"transaction_date": ["quotation date", "quote date", "dated"],
			"valid_till": ["valid till", "validity", "expiry date", "valid until"],
			"quotation_number": ["quotation no", "quote no", "sq no"],
			"net_total": ["subtotal", "sub total", "net total", "net amount"],
			"grand_total": ["grand total", "total amount"],
			"currency": ["currency"],
		},
	}

	# ------------------------------------------------------------------
	# Item-level keyword mappings (shared across DocTypes)
	# ------------------------------------------------------------------
	ITEM_KEYWORDS: ClassVar[dict[str, list[str]]] = {
		"item_code": ["item code", "item no", "product code", "sku", "part no", "code"],
		"item_name": ["item", "item name", "description", "product", "particular", "particulars"],
		"qty": ["qty", "quantity", "units", "nos", "pcs"],
		"rate": ["rate", "unit price", "price", "unit cost", "cost"],
		"amount": ["amount", "total", "line total", "value", "net amount"],
		"uom": ["uom", "unit", "unit of measure"],
		"discount_percentage": ["discount", "disc", "disc %", "discount %"],
	}

	# ------------------------------------------------------------------
	# Regex patterns for value-type detection
	# ------------------------------------------------------------------
	DATE_PATTERNS: ClassVar[list[tuple[str, str]]] = [
		# ISO: 2026-04-02
		(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", "YMD"),
		# DD/MM/YYYY or DD-MM-YYYY
		(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", "DMY"),
		# MM/DD/YYYY (US)
		(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", "MDY"),
		# DD-Mon-YYYY  (02-Apr-2026)
		(r"\b(\d{1,2})[/\-.\s]([A-Za-z]{3,9})[/\-.\s](\d{4})\b", "DMnY"),
		# Mon DD, YYYY  (April 02, 2026)
		(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", "MnDY"),
		# DD/MM/YY or DD-MM-YY
		(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b", "DMy"),
	]

	CURRENCY_PATTERN: ClassVar[str] = (
		r"[₹$€£¥]?\s*[\d,]+\.?\d*|[\d.,]+\s*(?:INR|USD|EUR|GBP|JPY|Rs\.?|AUD|CAD)"
	)

	NUMBER_PATTERN: ClassVar[str] = r"^[\d,]+\.?\d*$"

	# Month name lookup for date parsing
	_MONTHS: ClassVar[dict[str, int]] = {
		"jan": 1,
		"january": 1,
		"feb": 2,
		"february": 2,
		"mar": 3,
		"march": 3,
		"apr": 4,
		"april": 4,
		"may": 5,
		"jun": 6,
		"june": 6,
		"jul": 7,
		"july": 7,
		"aug": 8,
		"august": 8,
		"sep": 9,
		"september": 9,
		"oct": 10,
		"october": 10,
		"nov": 11,
		"november": 11,
		"dec": 12,
		"december": 12,
	}

	# ==================================================================
	# Public API
	# ==================================================================

	# ------------------------------------------------------------------
	# Regex patterns for extracting fields directly from OCR text
	# ------------------------------------------------------------------
	# These handle OCR output where labels and values are merged without
	# clean separators (e.g. "Invoice No. GST-3425-26" on a single line).
	TEXT_EXTRACTION_PATTERNS: ClassVar[dict[str, dict[str, list[tuple[str, int]]]]] = {
		"Purchase Invoice": {
			"bill_no": [
				# "Invoice No. GST-3425-26" — value MUST contain a digit
				(
					r"(?:Invoice\s*(?:No\.?|Number|#)|Inv\.?\s*No\.?)\s*[:\-]?\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)",
					1,
				),
				# "Bill No. 12345"
				(r"(?:Bill\s*(?:No\.?|Number))\s*[:\-]?\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)", 1),
				# "INVOICE\nSI-SINV-2025-000420" — heading then number on next line
				(r"INVOICE\s*\n\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)", 1),
				# "Ref No. ABC-123"
				(r"(?:Ref(?:erence)?\.?\s*(?:No\.?|#))\s*[:\-]?\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)", 1),
			],
			"posting_date": [
				# "Invoice Date 23-Jul-2025" or "Invoice Date: 26/12/2025"
				(
					r"(?:Invoice\s*Date|Inv\.?\s*Date|Bill\s*Date)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})",
					1,
				),
				# "Invoice Date 2025-07-23"
				(
					r"(?:Invoice\s*Date|Inv\.?\s*Date|Bill\s*Date)\s*[:\-]?\s*(\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})",
					1,
				),
				# "Dated 23-Jul-2025"
				(r"(?:Dated)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})", 1),
			],
			"due_date": [
				(
					r"(?:Due\s*Date|Payment\s*Due|Pay\s*[Bb]y)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})",
					1,
				),
				(r"(?:Due\s*Date|Payment\s*Due|Pay\s*[Bb]y)\s*[:\-]?\s*(\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})", 1),
			],
			"supplier": [
				# "M/S Tara General Trading LLC"
				(r"(?:M/[Ss]\.?|Messrs\.?)\s+(.+?)(?:\s{2,}|\n|Challan|Address|GSTIN|Phone)", 1),
				# "Supplier: Acme Corp" or "Vendor: Acme Corp"
				(r"(?:Supplier|Vendor|Seller|Bill\s*[Ff]rom)\s*[:\-]\s*(.+?)(?:\s{2,}|\n)", 1),
				# "For Akash Trading Co." (at document end)
				(r"(?:For)\s+(.+?(?:Co\.?|Ltd\.?|LLC|Pvt|Inc|Corp|Trading|Enterprise|S\.p\.A)\.?)\b", 1),
				# Company name with legal suffix on its own: "Bonatti S.p.A. - Iraqi Branch"
				(
					r"^(.+?(?:S\.p\.A\.?|Ltd\.?|LLC|Pvt\.?\s*Ltd|Inc\.?|Corp\.?|GmbH|AG|SA|SAS|BV)\.?(?:\s*[-\u2013]\s*\w[\w\s]*)?)\s*$",
					1,
				),
			],
			"net_total": [
				(
					r"(?:Sub\s*[Tt]otal|Net\s*Total|Net\s*Amount|Taxable\s*Value)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)",
					1,
				),
			],
			"grand_total": [
				(
					r"(?:Grand\s*Total)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)",
					1,
				),
				(r"(?:Total\s*Amount|Amount\s*Due|Balance\s*Due)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
				(r"Total\s+\d+\s+(?:NOS|EA|PCS)\s+[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
			"taxes_and_charges": [
				(r"(?:IGST|CGST|SGST|GST|Tax|VAT)\s*\([\d.]+\s*%?\s*\)\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
		},
		"Sales Invoice": {
			"customer": [
				(r"(?:M/[Ss]\.?|Messrs\.?)\s+(.+?)(?:\s{2,}|\n|Address|GSTIN|Phone)", 1),
				(r"(?:Customer|Buyer|Client|Bill\s*[Tt]o|Sold\s*[Tt]o)\s*[:\-]\s*(.+?)(?:\s{2,}|\n)", 1),
			],
			"posting_date": [
				(
					r"(?:Invoice\s*Date|Inv\.?\s*Date|Bill\s*Date)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})",
					1,
				),
				(
					r"(?:Invoice\s*Date|Inv\.?\s*Date|Bill\s*Date)\s*[:\-]?\s*(\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})",
					1,
				),
			],
			"po_no": [
				(
					r"(?:PO\s*(?:No\.?|Number)|Purchase\s*Order|Your\s*Order)\s*[:\-]?\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)",
					1,
				),
			],
			"net_total": [
				(r"(?:Sub\s*[Tt]otal|Net\s*Total|Net\s*Amount)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
			"grand_total": [
				(r"(?:Grand\s*Total)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
				(r"(?:Total\s*Amount|Amount\s*Due)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
		},
		"Purchase Order": {
			"supplier": [
				(r"(?:M/[Ss]\.?|Messrs\.?)\s+(.+?)(?:\s{2,}|\n|Address|GSTIN|Phone)", 1),
				(r"(?:Supplier|Vendor|Seller)\s*[:\-]\s*(.+?)(?:\s{2,}|\n)", 1),
			],
			"transaction_date": [
				(
					r"(?:Order\s*Date|PO\s*Date)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})",
					1,
				),
				(r"(?:Order\s*Date|PO\s*Date)\s*[:\-]?\s*(\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})", 1),
			],
			"grand_total": [
				(r"(?:Grand\s*Total)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
				(r"(?:Total\s*Amount)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
		},
		"Sales Order": {
			"customer": [
				(r"(?:Customer|Buyer|Client|Bill\s*[Tt]o)\s*[:\-]\s*(.+?)(?:\s{2,}|\n)", 1),
			],
			"transaction_date": [
				(
					r"(?:Order\s*Date|SO\s*Date)\s*[:\-]?\s*(\d{1,2}[/\-.\s][A-Za-z0-9]{2,9}[/\-.\s]\d{2,4})",
					1,
				),
				(r"(?:Order\s*Date|SO\s*Date)\s*[:\-]?\s*(\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})", 1),
			],
			"grand_total": [
				(r"(?:Grand\s*Total)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
				(r"(?:Total\s*Amount)\s*[:\-]?\s*[₹$€£Rs.]*\s*([\d,]+\.?\d*)", 1),
			],
		},
	}

	# Regex patterns for extracting line items directly from OCR text
	# when structured tables are not available.
	ITEM_LINE_PATTERN: ClassVar[str] = (
		r"^\s*(\d+)\s+"  # serial number
		r"(.+?)\s+"  # item name
		r"(\d{4,8})\s+"  # HSN/SAC code
		r"(\d+(?:\.\d+)?)\s+"  # qty
		r"([A-Z]+)\s+"  # UOM (NOS, KG, etc.)
		r"([\d,]+\.?\d*)\s+"  # rate
		r"([\d,]+\.?\d*)"  # amount
	)

	def map_fields(
		self,
		extracted: ExtractionResult,
		target_doctype: str,
		company: str | None = None,
		*,
		source_lang: str | list[str] | tuple[str, ...] | None = None,
	) -> MappedDocument:
		"""Map extracted content to *target_doctype* fields.

		Strategy:
		1. Load the DocType schema.
		2. Parse text into label-value pairs (colon/tab/space separated).
		3. Match header fields via keyword proximity + type inference.
		4. **Regex pass** — scan full text for known patterns to fill gaps.
		5. Match line items via column-header matching (from tables).
		6. **Text-based item extraction** if no structured tables found.
		7. Normalise values (dates, numbers, currencies).
		8. Resolve Link fields against the database.

		``source_lang`` (Phase 22) tells the mapper which multilingual
		keyword aliases to merge alongside English:

		* ``None`` / ``""`` / ``"auto"`` — fan out to **all** supported
		  languages so the mapper can recognise labels in any of them
		  (best for auto-detect or unknown documents).
		* Single string code (e.g. ``"hi"``) — English + that one
		  language only (cheap, recommended when the language is known).
		* List/tuple of codes (e.g. ``["hi", "ar"]``) — English + each.
		"""
		schema = get_doctype_schema(target_doctype)
		result = MappedDocument(doctype=target_doctype)
		full_text = extracted.text or ""

		# --- Pass 1: Parse label-value pairs from text ---
		pairs = self._parse_label_value_pairs(full_text)

		# --- Map header fields from pairs ---
		keywords = self._resolve_keywords(target_doctype, source_lang)
		schema_fields = schema["fields"]

		for label, value in pairs:
			fieldname = self._match_header_field(label, value, schema_fields, keywords)
			if fieldname:
				normalised = self._normalise_value(value, fieldname, schema_fields)
				result.header[fieldname] = normalised
				result.confidence_scores[fieldname] = self._score_match(label, fieldname, keywords)
			else:
				result.unmapped_fields.append({"label": label, "value": value})

		# --- Pass 2: Regex extraction for fields not yet mapped ---
		self._extract_by_regex(full_text, target_doctype, schema_fields, result)

		# --- Map line items from structured tables ---
		if extracted.tables:
			child_table_info = self._find_primary_child_table(schema)
			if child_table_info:
				items = self._map_table_items(extracted.tables, child_table_info)
				result.items = items

		# --- Fallback: extract items from text if no structured tables ---
		if not result.items:
			child_table_info = self._find_primary_child_table(schema)
			if child_table_info:
				text_items = self._extract_items_from_text(full_text, child_table_info)
				if text_items:
					result.items = text_items

		# --- Resolve Link fields ---
		self._resolve_links(result, schema, company)

		# --- Phase 29: capture per-field source regions when OCR
		# provided bboxes. Best-effort; failures never block mapping.
		try:
			self._capture_source_regions(result, extracted)
		except Exception as exc:  # pragma: no cover — defensive
			logger.debug(f"source_regions capture failed: {exc}")

		return result

	# ==================================================================
	# Phase 29 — Provenance capture
	# ==================================================================

	def _capture_source_regions(
		self,
		result: MappedDocument,
		extracted: ExtractionResult,
	) -> None:
		"""Best-effort lookup of OCR bboxes for each mapped header value.

		For every header value we hold, we scan ``extracted.ocr_results``
		(populated by the Paddle path) for the first text block whose
		text contains the value as a substring (case-insensitive).  When
		we find one we record::

		    result.source_regions[fieldname] = {
		        "page": <1-based page number>,
		        "bbox": [x0, y0, x1, y1],
		        "mapper": "rule",
		    }

		LLM-only fields and values that don't appear verbatim in any OCR
		block leave ``source_regions[fieldname]`` unset — the UI surfaces
		this case via a tooltip.
		"""
		ocr_results = getattr(extracted, "ocr_results", None)
		if not ocr_results:
			return

		def _poly_to_xyxy(poly: list) -> list[float] | None:
			"""Convert a 4-point polygon to ``[x0, y0, x1, y1]``."""
			try:
				xs = [float(p[0]) for p in poly]
				ys = [float(p[1]) for p in poly]
				return [min(xs), min(ys), max(xs), max(ys)]
			except (TypeError, ValueError, IndexError):
				return None

		# Build a flat list of (page_no, text_lower, xyxy) entries we
		# can scan once per field.  Skip results that don't expose the
		# expected attributes (e.g. minimal stubs in tests).
		flat: list[tuple[int, str, list[float]]] = []
		for ocr in ocr_results:
			page_no = getattr(ocr, "page_number", 1) or 1
			blocks = getattr(ocr, "text_blocks", None) or []
			for tb in blocks:
				text = getattr(tb, "text", None)
				bbox_poly = getattr(tb, "bbox", None)
				if not text or not bbox_poly:
					continue
				xyxy = _poly_to_xyxy(bbox_poly)
				if xyxy is None:
					continue
				flat.append((int(page_no), str(text).lower(), xyxy))

		if not flat:
			return

		for fieldname, value in result.header.items():
			if value is None or value == "":
				continue
			needle = str(value).strip().lower()
			if not needle:
				continue
			# Skip tiny or numeric-only needles that would match
			# nearly anything ("1", "0.0") and pollute the panel.
			if len(needle) < 3:
				continue
			for page_no, text_lc, xyxy in flat:
				if needle in text_lc:
					result.source_regions[fieldname] = {
						"page": page_no,
						"bbox": xyxy,
						"mapper": "rule",
					}
					break

	# ==================================================================
	# Keyword resolution (Phase 22 multilingual)
	# ==================================================================

	def _resolve_keywords(
		self,
		target_doctype: str,
		source_lang: str | list[str] | tuple[str, ...] | None,
	) -> dict[str, list[str]]:
		"""Pick the keyword dict for *target_doctype*, merging
		multilingual aliases according to *source_lang*.

		Resolution rules:

		* ``source_lang`` falsy / ``"auto"`` → fan out across **all**
		  :data:`SUPPORTED_LANGUAGES`.  This costs nothing at runtime
		  (the merge is a small in-memory dict build) and lets the
		  rule mapper match labels in any shipped language without
		  the caller having to commit to one upfront.
		* Single ``"en"`` (after normalisation) → legacy English-only
		  behaviour (returns ``self.FIELD_KEYWORDS[doctype]``).
		* Single non-English string code → English + that one language.
		* Iterable of codes → English + each (de-duplicated).

		English is always included as a baseline so existing English
		documents see zero regression regardless of the value passed.
		"""

		english = self.FIELD_KEYWORDS.get(target_doctype, {})
		langs = self._coerce_language_list(source_lang)

		# Filter to non-English (English is auto-prepended by
		# merge_keywords_for_languages).  If nothing is left, the
		# caller asked for English-only — return the legacy dict
		# directly to keep the fast path zero-cost.
		non_english = [lang for lang in langs if lang != "en"]
		if not non_english:
			return english

		return merge_keywords_for_languages(
			target_doctype,
			non_english,
			english_fallback=english,
		)

	@staticmethod
	def _coerce_language_list(
		source_lang: str | list[str] | tuple[str, ...] | None,
	) -> list[str]:
		"""Normalise *source_lang* into a deduplicated list of codes.

		Returns a list (never ``None``).  Falsy / ``"auto"`` inputs
		expand to the full :data:`SUPPORTED_LANGUAGES` list.
		"""

		# Falsy → fan out to all supported languages.
		if not source_lang:
			return list(SUPPORTED_LANGUAGES)

		# Treat scalar string + list/tuple uniformly.
		if isinstance(source_lang, str):
			if source_lang.strip().lower() == "auto":
				return list(SUPPORTED_LANGUAGES)
			candidates = [source_lang]
		else:
			candidates = list(source_lang)

		seen: set[str] = set()
		out: list[str] = []
		for raw in candidates:
			if not raw:
				continue
			if isinstance(raw, str) and raw.strip().lower() == "auto":
				# An explicit ``auto`` inside a list still triggers
				# the full fan-out — easier than asking the caller
				# to special-case it.
				return list(SUPPORTED_LANGUAGES)
			lang = normalize_language(raw)
			if lang in seen:
				continue
			seen.add(lang)
			out.append(lang)

		return out or list(SUPPORTED_LANGUAGES)

	# ==================================================================
	# Label-value parsing
	# ==================================================================

	def _parse_label_value_pairs(self, text: str) -> list[tuple[str, str]]:
		"""Extract ``(label, value)`` pairs from freeform text.

		Looks for patterns like ``Label: Value`` or ``Label  Value``
		separated by colons, tabs, or multiple spaces.
		"""
		pairs: list[tuple[str, str]] = []
		for line in text.splitlines():
			line = line.strip()
			if not line:
				continue

			# Try colon separator first
			if ":" in line:
				parts = line.split(":", 1)
				label = parts[0].strip()
				value = parts[1].strip()
				if label and value:
					pairs.append((label, value))
					continue

			# Try tab separator
			if "\t" in line:
				parts = line.split("\t", 1)
				label = parts[0].strip()
				value = parts[1].strip()
				if label and value:
					pairs.append((label, value))
					continue

			# Try multiple spaces (≥ 3)
			match = re.match(r"^(.+?)\s{3,}(.+)$", line)
			if match:
				pairs.append((match.group(1).strip(), match.group(2).strip()))

		return pairs

	# ==================================================================
	# Header field matching
	# ==================================================================

	def _match_header_field(
		self,
		label: str,
		value: str,
		schema_fields: list[dict],
		keywords: dict[str, list[str]],
	) -> str | None:
		"""Find the best matching fieldname for a label-value pair."""
		label_lower = label.lower().strip()

		# Longest keyword wins. Callers: FieldMapper.map_fields, then
		# idp.api.extract.extract_document and idp.api.llm.hybrid_map.
		# A short alias such as French "date" must not take "Due Date".
		best_field = None
		best_len = -1
		for fieldname, kw_list in keywords.items():
			for kw in kw_list:
				if not kw:
					continue
				if kw == label_lower or kw in label_lower:
					if len(kw) > best_len:
						best_field = fieldname
						best_len = len(kw)
		if best_field:
			return best_field

		# 2. Check field alias table
		for alias, target in FIELD_ALIASES.items():
			if alias in label_lower:
				return target

		# 3. Direct fieldname or label match from schema
		for f in schema_fields:
			if label_lower == f["fieldname"] or label_lower == (f["label"] or "").lower():
				return f["fieldname"]

		return None

	def _score_match(
		self,
		label: str,
		fieldname: str,
		keywords: dict[str, list[str]],
	) -> float:
		"""Score how confident the keyword match is (0.0-1.0)."""
		label_lower = label.lower().strip()
		kw_list = keywords.get(fieldname, [])

		# Exact match on keyword
		if label_lower in kw_list:
			return 1.0

		# Partial match
		for kw in kw_list:
			if kw in label_lower:
				return 0.8

		# Alias match
		if any(alias in label_lower for alias in FIELD_ALIASES):
			return 0.6

		return 0.5

	# ==================================================================
	# Regex-based extraction (Pass 2)
	# ==================================================================

	def _extract_by_regex(
		self,
		text: str,
		target_doctype: str,
		schema_fields: list[dict],
		result: MappedDocument,
	) -> None:
		"""Scan full text with regex patterns to extract fields not found by label-value parsing.

		Only fills fields that are not already present in ``result.header``.
		"""
		patterns = self.TEXT_EXTRACTION_PATTERNS.get(target_doctype, {})
		if not patterns:
			return

		for fieldname, regex_list in patterns.items():
			if fieldname in result.header:
				continue  # Already found in Pass 1

			for pattern, group_idx in regex_list:
				match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
				if match:
					raw_value = match.group(group_idx).strip()
					if raw_value:
						normalised = self._normalise_value(raw_value, fieldname, schema_fields)
						result.header[fieldname] = normalised
						result.confidence_scores[fieldname] = 0.7  # Regex match confidence
						logger.debug(f"Regex extracted {fieldname}={raw_value!r} via pattern")
						break

	def _extract_items_from_text(
		self,
		text: str,
		child_table_info: dict,
	) -> list[dict]:
		"""Extract line items from OCR text when no structured tables are available.

		Matches lines like::

		    1 Bosch All-in-One Metal Hand Tool Kit 8302 1 NOS 2,535.00 2,535.00

		Falls back to a simpler pattern if the full pattern doesn't match.
		"""
		child_fields = child_table_info.get("fields", [])
		items: list[dict] = []

		# Field existence checks
		has_item_name = any(f["fieldname"] == "item_name" for f in child_fields)
		has_qty = any(f["fieldname"] == "qty" for f in child_fields)
		has_rate = any(f["fieldname"] == "rate" for f in child_fields)
		has_amount = any(f["fieldname"] == "amount" for f in child_fields)
		has_uom = any(f["fieldname"] == "uom" for f in child_fields)
		has_item_code = any(f["fieldname"] == "item_code" for f in child_fields)

		# Full pattern: serial item_name HSN qty UOM rate amount
		full_pattern = re.compile(self.ITEM_LINE_PATTERN, re.MULTILINE)

		for match in full_pattern.finditer(text):
			row: dict = {}
			_serial, name, hsn, qty, uom, rate, amount = match.groups()
			if has_item_name:
				row["item_name"] = name.strip()
			if has_item_code:
				row["item_code"] = hsn.strip()
			if has_qty:
				row["qty"] = self._normalise_number(qty)
			if has_uom:
				row["uom"] = uom.strip()
			if has_rate:
				row["rate"] = self._normalise_number(rate)
			if has_amount:
				row["amount"] = self._normalise_number(amount)
			if row:
				items.append(row)

		if items:
			return items

		# Simpler fallback: lines with item-like patterns
		# serial item_name qty rate amount (no HSN/UOM)
		simple_pattern = re.compile(
			r"^\s*(\d+)\s+"  # serial
			r"(.+?)\s+"  # item name
			r"(\d+(?:\.\d+)?)\s+"  # qty
			r"([\d,]+\.?\d*)\s+"  # rate
			r"([\d,]+\.?\d*)\s*$",  # amount
			re.MULTILINE,
		)

		for match in simple_pattern.finditer(text):
			row = {}
			_serial, name, qty, rate, amount = match.groups()
			if has_item_name:
				row["item_name"] = name.strip()
			if has_qty:
				row["qty"] = self._normalise_number(qty)
			if has_rate:
				row["rate"] = self._normalise_number(rate)
			if has_amount:
				row["amount"] = self._normalise_number(amount)
			if row:
				items.append(row)

		return items

	# ==================================================================
	# Value normalisation
	# ==================================================================

	def _normalise_value(
		self,
		value: str,
		fieldname: str,
		schema_fields: list[dict],
	) -> str | float | int | None:
		"""Normalise a raw string value based on the target field type."""
		field_type = None
		for f in schema_fields:
			if f["fieldname"] == fieldname:
				field_type = f["fieldtype"]
				break

		if not field_type:
			return value

		if field_type == "Date":
			return self._normalise_date(value) or value
		elif field_type in ("Currency", "Float"):
			return self._normalise_number(value) if self._normalise_number(value) is not None else value
		elif field_type == "Int":
			num = self._normalise_number(value)
			return int(num) if num is not None else value
		elif field_type == "Check":
			return 1 if value.lower() in ("yes", "true", "1", "checked", "✓") else 0

		return value

	def _normalise_date(self, raw: str) -> str | None:
		"""Parse a date string from 12+ formats into ``YYYY-MM-DD``."""
		raw = raw.strip()

		for pattern, fmt in self.DATE_PATTERNS:
			match = re.search(pattern, raw)
			if not match:
				continue

			try:
				if fmt == "YMD":
					y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
				elif fmt == "DMY":
					d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
				elif fmt == "MDY":
					m, d, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
					# Only treat as MDY if month > 12 would fail for DMY
					if m > 12:
						continue
				elif fmt == "DMnY":
					d = int(match.group(1))
					m = self._month_to_int(match.group(2))
					y = int(match.group(3))
				elif fmt == "MnDY":
					m = self._month_to_int(match.group(1))
					d = int(match.group(2))
					y = int(match.group(3))
				elif fmt == "DMy":
					d, m = int(match.group(1)), int(match.group(2))
					y = int(match.group(3))
					y += 2000 if y < 100 else 0
				else:
					continue

				if m is None or not (1 <= m <= 12) or not (1 <= d <= 31):
					continue
				return f"{y:04d}-{m:02d}-{d:02d}"
			except (ValueError, TypeError):
				continue

		return None

	def _normalise_number(self, raw: str) -> float | None:
		"""Strip currency symbols, commas, and spaces to produce a float."""
		if not raw:
			return None
		# Remove currency symbols and whitespace
		cleaned = re.sub(r"[₹$€£¥\s]", "", raw)
		# Remove trailing currency codes
		cleaned = re.sub(r"(INR|USD|EUR|GBP|JPY|Rs\.?|AUD|CAD)$", "", cleaned, flags=re.IGNORECASE)
		cleaned = cleaned.strip()

		if not cleaned:
			return None

		# Handle Indian / European number formats
		# 1,23,456.78  or  1,234.56 → standard float
		# 1.234,56 (European) → swap . and ,
		if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", cleaned):
			# European: dots as thousands, comma as decimal
			cleaned = cleaned.replace(".", "").replace(",", ".")
		else:
			# Remove commas used as thousands separators
			cleaned = cleaned.replace(",", "")

		try:
			return float(cleaned)
		except ValueError:
			return None

	def _month_to_int(self, name: str) -> int | None:
		"""Convert a month name or abbreviation to an integer 1-12."""
		return self._MONTHS.get(name.lower().strip())

	# ==================================================================
	# Table / line-item mapping
	# ==================================================================

	def _find_primary_child_table(self, schema: dict) -> dict | None:
		"""Find the primary child table (usually ``items``) from the schema."""
		ct = schema.get("child_tables", {})
		# Prefer "items" if it exists
		if "items" in ct:
			return ct["items"]
		# Otherwise return the first one
		for _key, val in ct.items():
			return val
		return None

	def _map_table_items(
		self,
		tables: list[list[list[str]]],
		child_table_info: dict,
	) -> list[dict]:
		"""Map table rows to child-table fields via column-header matching."""
		child_fields = child_table_info.get("fields", [])
		items: list[dict] = []

		for table in tables:
			if len(table) < 2:
				continue  # Need at least a header row + one data row

			header_row = table[0]
			col_map = self._match_column_headers(header_row, child_fields)

			if not col_map:
				continue

			for data_row in table[1:]:
				row_dict: dict = {}
				for col_idx, fieldname in col_map.items():
					if col_idx < len(data_row):
						raw_val = data_row[col_idx].strip()
						if raw_val:
							# Normalise based on field type
							normalised = self._normalise_value(raw_val, fieldname, child_fields)
							row_dict[fieldname] = normalised

				if row_dict:
					# Phase 24 — populate is_stock_item via keyword heuristic
					# when the schema has the column and the row hasn't
					# already supplied it (e.g. via a dedicated "Is Stock"
					# header).  Blank => undetermined (per §24.0).
					if any(f["fieldname"] == "is_stock_item" for f in child_fields):
						if "is_stock_item" not in row_dict:
							guess = _guess_is_stock_item(row_dict)
							if guess is not None:
								row_dict["is_stock_item"] = guess
					items.append(row_dict)

		return items

	def _match_column_headers(
		self,
		header_row: list[str],
		child_fields: list[dict],
	) -> dict[int, str]:
		"""Match column headers to child-table field names.

		Returns ``{column_index: fieldname}``.
		"""
		col_map: dict[int, str] = {}

		for col_idx, header in enumerate(header_row):
			header_lower = header.lower().strip()
			if not header_lower:
				continue

			# 1. Try item keyword table
			for fieldname, kw_list in self.ITEM_KEYWORDS.items():
				if any(kw == header_lower or kw in header_lower for kw in kw_list):
					# Verify this fieldname exists in the child schema
					if any(f["fieldname"] == fieldname for f in child_fields):
						col_map[col_idx] = fieldname
						break

			# 2. Direct label / fieldname match
			if col_idx not in col_map:
				for f in child_fields:
					if header_lower == f["fieldname"] or header_lower == (f["label"] or "").lower():
						col_map[col_idx] = f["fieldname"]
						break

		return col_map

	# ==================================================================
	# Link field resolution
	# ==================================================================

	def _resolve_links(
		self,
		result: MappedDocument,
		schema: dict,
		company: str | None,
	) -> None:
		"""Attempt to resolve all Link-type header fields to existing records."""
		for f in schema["fields"]:
			if f["fieldtype"] != "Link":
				continue
			fieldname = f["fieldname"]
			if fieldname not in result.header:
				continue

			raw_value = str(result.header[fieldname])
			link_doctype = f["options"]
			if not link_doctype:
				continue

			resolved = self._resolve_link(raw_value, link_doctype, company)
			if resolved and resolved != raw_value:
				result.link_resolutions[fieldname] = {
					"original": raw_value,
					"resolved": resolved,
				}
				result.header[fieldname] = resolved
			elif not resolved:
				result.warnings.append(f'{link_doctype} "{raw_value}" not found (field: {fieldname})')

	def _resolve_link(
		self,
		value: str,
		doctype: str,
		company: str | None = None,
	) -> str | None:
		"""Fuzzy-match *value* to an existing record of *doctype*.

		Resolution order:
		1. Exact match on ``name``.
		2. Exact match on display field (customer_name, supplier_name …).
		3. ``LIKE`` match on display field (must be unique).
		4. Company-scoped search.
		"""
		if not value:
			return None

		# 1. Exact name
		if frappe.db.exists(doctype, value):
			return value

		# 2. Exact display-field match
		display_field = LINK_DISPLAY_FIELDS.get(doctype)
		if display_field:
			match = frappe.db.get_value(
				doctype,
				{display_field: value},
				"name",
			)
			if match:
				return match

		# 3. LIKE match on display field
		if display_field:
			filters = {display_field: ["like", f"%{value}%"]}
			if company and _has_company_field(doctype):
				filters["company"] = company
			matches = frappe.get_all(doctype, filters=filters, pluck="name", limit=2)
			if len(matches) == 1:
				return matches[0]

		return None


def _has_company_field(doctype: str) -> bool:
	"""Check whether *doctype* has a ``company`` field."""
	try:
		meta = frappe.get_meta(doctype)
		return meta.has_field("company")
	except Exception:
		return False


# ---------------------------------------------------------------------------
# Phase 24 — is_stock_item keyword heuristic
# ---------------------------------------------------------------------------


# Hints that strongly suggest a *stockable* (physical, inventoried) item.
_STOCK_KEYWORDS: tuple[str, ...] = (
	"raw material",
	"finished good",
	"component",
	"part",
	"goods",
	"product",
	"hardware",
	"box",
	"unit",
	"bottle",
	"piece",
	"package",
	"kg",
	"gram",
	"litre",
	"liter",
	"meter",
	"metre",
)

# Hints that strongly suggest a *non-stock* (service / consumable) item.
_NON_STOCK_KEYWORDS: tuple[str, ...] = (
	"service",
	"consultancy",
	"consulting",
	"subscription",
	"license",
	"licence",
	"fee",
	"charges",
	"freight",
	"shipping",
	"installation",
	"maintenance",
	"support",
	"training",
	"warranty",
)

# UOMs that almost always indicate a stockable item.
_STOCK_UOMS: tuple[str, ...] = (
	"nos",
	"no",
	"pcs",
	"pc",
	"piece",
	"box",
	"unit",
	"kg",
	"gm",
	"g",
	"l",
	"ltr",
	"litre",
	"liter",
	"mtr",
	"meter",
	"metre",
	"set",
	"pack",
)


def _guess_is_stock_item(row: dict) -> int | None:
	"""Return ``1``/``0``/``None`` based on row description & UOM keywords.

	Per Phase 24 §24.0 the rule mapper populates ``is_stock_item`` with
	a keyword heuristic; ``None`` (blank) means undetermined and the UI
	leaves the cell empty.
	"""

	if not isinstance(row, dict):
		return None

	desc_parts: list[str] = []
	for key in ("item_name", "description", "item_code", "particulars"):
		val = row.get(key)
		if val:
			desc_parts.append(str(val).lower())
	desc = " ".join(desc_parts)

	uom = str(row.get("uom") or row.get("stock_uom") or "").strip().lower()

	hits_stock = any(kw in desc for kw in _STOCK_KEYWORDS)
	hits_non_stock = any(kw in desc for kw in _NON_STOCK_KEYWORDS)
	uom_is_stock = uom in _STOCK_UOMS

	# Service-leaning keywords win when the row also lacks a stock-y UOM.
	if hits_non_stock and not uom_is_stock:
		return 0
	if hits_stock or uom_is_stock:
		return 1
	if hits_non_stock:
		# Stock UOM + service keyword is ambiguous → leave undetermined.
		return None
	return None
