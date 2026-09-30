# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Schema validator — checks mapped data against DocType field constraints.

Validates required fields, field types, Select options, Link existence,
child-table mandatory fields, and Data field length limits.  Also provides
link-resolution as a standalone helper.
"""

import re
from dataclasses import dataclass, field

import frappe

from idp.core.constants import AUTO_POPULATED_FIELDS
from idp.core.logger import get_logger
from idp.mappers.base import MappedDocument, get_doctype_schema
from idp.mappers.mapper import LINK_DISPLAY_FIELDS

logger = get_logger("idp.validators")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
	"""A single validation error or warning."""

	field: str
	message: str
	severity: str = "error"  # "error" | "warning"
	value: object = None


@dataclass
class ValidationResult:
	"""Aggregated result of schema + link validation."""

	is_valid: bool = True
	errors: list[ValidationIssue] = field(default_factory=list)
	warnings: list[ValidationIssue] = field(default_factory=list)
	resolved_links: dict = field(default_factory=dict)  # {fieldname: resolved_value}
	missing_masters: list[dict] = field(default_factory=list)  # [{doctype, name, field}]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def child_tables_for_rows(child_tables: dict, rows: list) -> list[tuple[str, dict]]:
	"""The child table these rows belong to.

	A Sales Invoice row is an item. The same list must not be checked as a
	tax row, a payment row, and a sales-team row.
	"""
	keys: set[str] = set()
	for row in rows or []:
		if isinstance(row, dict):
			keys.update(str(key) for key in row.keys())
	if not keys or not child_tables:
		return []
	scored: list[tuple[int, str, dict]] = []
	for name, info in child_tables.items():
		fields = {
			field.get("fieldname")
			for field in (info or {}).get("fields") or []
			if isinstance(field, dict) and field.get("fieldname")
		}
		overlap = len(keys & fields)
		if overlap:
			scored.append((overlap, str(name), info or {}))
	if not scored:
		return []
	best = max(score for score, _name, _info in scored)
	return [(name, info) for score, name, info in scored if score == best]


def validate_schema(mapped_data: MappedDocument, company: str = "") -> ValidationResult:
	"""Validate *mapped_data* against the DocType field schema.

	Checks performed:
	1. Required parent fields present (``reqd=1``).
	2. Field type correctness (Date, Currency, Float, Int, Check).
	3. Select field values belong to the allowed option list.
	4. Link field references exist in the database.
	5. Child-table mandatory fields present per row.
	6. Data field length constraints (``max_length``).
	"""
	schema = get_doctype_schema(mapped_data.doctype)
	result = ValidationResult()

	# --- Parent field checks ---
	_check_required_fields(mapped_data.header, schema["fields"], result)
	_check_field_types(mapped_data.header, schema["fields"], result)
	_check_select_options(mapped_data.header, schema["fields"], result)
	_check_data_lengths(mapped_data.header, schema["fields"], result)
	_check_links(mapped_data.header, schema["fields"], company, result)

	# Mapped rows belong to one child table. Callers of validate_schema:
	# idp/api/extract.py, idp/mappers/document_creator.py, taxmate/api/idp_desk.py.
	# No other helper picks the table. Rows are {item_code, qty, rate, amount}.
	for _table_fieldname, table_info in child_tables_for_rows(
		schema.get("child_tables") or {}, mapped_data.items
	):
		child_fields = table_info.get("fields", [])
		for idx, row in enumerate(mapped_data.items):
			row_label = f"Row {idx + 1}"
			_check_required_fields(row, child_fields, result, row_label=row_label)
			_check_field_types(row, child_fields, result, row_label=row_label)
			_check_links(row, child_fields, company, result, row_label=row_label)

	result.is_valid = len(result.errors) == 0
	return result


def resolve_links(mapped_data: MappedDocument, company: str = "") -> dict:
	"""Resolve all Link-type field values to existing records.

	Resolution strategy (in priority order):
	1. Exact match on ``name``.
	2. Exact match on display field (customer_name, supplier_name …).
	3. ``LIKE`` match on display field (unique match only).
	4. Company-scoped search for DocTypes with a ``company`` field.

	Returns:
		``{fieldname: resolved_name}`` for every successfully resolved Link.
	"""
	schema = get_doctype_schema(mapped_data.doctype)
	resolved: dict[str, str] = {}

	for f in schema["fields"]:
		if f["fieldtype"] != "Link":
			continue
		fieldname = f["fieldname"]
		if fieldname not in mapped_data.header:
			continue

		raw_value = str(mapped_data.header[fieldname]).strip()
		link_doctype = f.get("options", "")
		if not raw_value or not link_doctype:
			continue

		name = _resolve_single_link(raw_value, link_doctype, company)
		if name:
			resolved[fieldname] = name

	return resolved


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------


def _check_required_fields(
	data: dict,
	schema_fields: list[dict],
	result: ValidationResult,
	row_label: str = "",
) -> None:
	"""Flag missing required fields as errors.

	Skips fields in :data:`AUTO_POPULATED_FIELDS` since ERPNext controllers
	set them automatically (e.g. ``naming_series``, ``credit_to``, ``company``).
	"""
	for f in schema_fields:
		if not f.get("reqd"):
			continue
		fieldname = f["fieldname"]
		if fieldname in AUTO_POPULATED_FIELDS:
			continue
		value = data.get(fieldname)
		if value is None or (isinstance(value, str) and not value.strip()):
			prefix = f"{row_label} — " if row_label else ""
			result.errors.append(
				ValidationIssue(
					field=fieldname,
					message=f'{prefix}Required field "{f["label"]}" is missing',
					severity="error",
				)
			)


def _check_field_types(
	data: dict,
	schema_fields: list[dict],
	result: ValidationResult,
	row_label: str = "",
) -> None:
	"""Validate that values conform to their declared field types."""
	type_map = {f["fieldname"]: f for f in schema_fields}

	for fieldname, value in data.items():
		if fieldname not in type_map or value is None:
			continue

		ftype = type_map[fieldname]["fieldtype"]
		prefix = f"{row_label} — " if row_label else ""

		if ftype == "Date":
			if not _is_valid_date(str(value)):
				result.errors.append(
					ValidationIssue(
						field=fieldname,
						message=f'{prefix}"{value}" is not a valid date (expected YYYY-MM-DD)',
						severity="error",
						value=value,
					)
				)

		elif ftype in ("Currency", "Float"):
			if not _is_numeric(value):
				result.errors.append(
					ValidationIssue(
						field=fieldname,
						message=f'{prefix}"{value}" is not a valid number',
						severity="error",
						value=value,
					)
				)

		elif ftype == "Int":
			if not _is_int(value):
				result.errors.append(
					ValidationIssue(
						field=fieldname,
						message=f'{prefix}"{value}" is not a valid integer',
						severity="error",
						value=value,
					)
				)

		elif ftype == "Check":
			if value not in (0, 1, True, False):
				result.warnings.append(
					ValidationIssue(
						field=fieldname,
						message=f'{prefix}"{value}" will be coerced to 0/1 for Check field',
						severity="warning",
						value=value,
					)
				)


def _check_select_options(
	data: dict,
	schema_fields: list[dict],
	result: ValidationResult,
) -> None:
	"""Validate Select field values against allowed options."""
	for f in schema_fields:
		if f["fieldtype"] != "Select":
			continue
		fieldname = f["fieldname"]
		if fieldname not in data:
			continue

		options_str = f.get("options", "")
		if not options_str:
			continue

		allowed = [o.strip() for o in options_str.split("\n") if o.strip()]
		value = str(data[fieldname]).strip()
		if value and allowed and value not in allowed:
			result.warnings.append(
				ValidationIssue(
					field=fieldname,
					message=f'"{value}" is not a valid option for {f["label"]} (allowed: {", ".join(allowed[:5])})',
					severity="warning",
					value=value,
				)
			)


def _check_data_lengths(
	data: dict,
	schema_fields: list[dict],
	result: ValidationResult,
) -> None:
	"""Warn if Data field values exceed 140 characters (Frappe default)."""
	for f in schema_fields:
		if f["fieldtype"] != "Data":
			continue
		fieldname = f["fieldname"]
		if fieldname not in data:
			continue
		value = str(data[fieldname])
		max_len = 140
		if len(value) > max_len:
			result.warnings.append(
				ValidationIssue(
					field=fieldname,
					message=f'Value for "{f["label"]}" is {len(value)} chars (max {max_len})',
					severity="warning",
					value=value,
				)
			)


def _check_links(
	data: dict,
	schema_fields: list[dict],
	company: str,
	result: ValidationResult,
	row_label: str = "",
) -> None:
	"""Validate Link fields exist and record missing masters."""
	for f in schema_fields:
		if f["fieldtype"] != "Link":
			continue
		fieldname = f["fieldname"]
		if fieldname not in data:
			continue

		raw_value = str(data[fieldname]).strip()
		link_doctype = f.get("options", "")
		if not raw_value or not link_doctype:
			continue

		resolved = _resolve_single_link(raw_value, link_doctype, company)
		prefix = f"{row_label} — " if row_label else ""

		if resolved:
			result.resolved_links[fieldname] = resolved
		else:
			result.missing_masters.append(
				{
					"doctype": link_doctype,
					"name": raw_value,
					"field": fieldname,
				}
			)
			result.warnings.append(
				ValidationIssue(
					field=fieldname,
					message=f'{prefix}{link_doctype} "{raw_value}" not found',
					severity="warning",
					value=raw_value,
				)
			)


# ---------------------------------------------------------------------------
# Link resolution
# ---------------------------------------------------------------------------


def _resolve_single_link(value: str, doctype: str, company: str = "") -> str | None:
	"""Attempt to resolve *value* to an existing record of *doctype*."""
	if not value:
		return None

	# 1. Exact match on name
	if frappe.db.exists(doctype, value):
		return value

	# 2. Exact match on display field
	display_field = LINK_DISPLAY_FIELDS.get(doctype)
	if display_field:
		name = frappe.db.get_value(doctype, {display_field: value}, "name")
		if name:
			return name

	# 3. LIKE match (must be unique)
	if display_field:
		filters: dict = {display_field: ["like", f"%{value}%"]}
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
# Type-check helpers
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_date(value: str) -> bool:
	"""Return True if *value* is a valid ``YYYY-MM-DD`` date string."""
	if not _DATE_RE.match(value):
		return False
	try:
		parts = value.split("-")
		y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
		return 1 <= m <= 12 and 1 <= d <= 31 and y > 0
	except (ValueError, IndexError):
		return False


def _is_numeric(value) -> bool:
	"""Return True if *value* can be interpreted as a float."""
	try:
		float(value)
		return True
	except (ValueError, TypeError):
		return False


def _is_int(value) -> bool:
	"""Return True if *value* can be interpreted as an integer."""
	try:
		int(value)
		return True
	except (ValueError, TypeError):
		return False
