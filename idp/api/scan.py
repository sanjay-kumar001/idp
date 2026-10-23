# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Thin whitelist for TaxMate SPA Scan flow. Controllers own the rules."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from idp.scan.state import QUEUED
from idp.scan.view import scan_view

SCAN_DOCTYPE = "IDP Document Log"


def _load(name: str, ptype: str = "read"):
	doc = frappe.get_doc(SCAN_DOCTYPE, name)
	doc.check_permission(ptype)
	return doc


def _assert_modified(doc, modified: str) -> None:
	if not modified:
		frappe.throw(_("modified is required"), frappe.ValidationError)
	if str(doc.modified) != str(modified):
		frappe.throw(_("Document has been modified"), frappe.TimestampMismatchError)


def _parse_edits(edits: Any) -> list[dict[str, Any]] | dict[str, Any]:
	if isinstance(edits, str):
		edits = json.loads(edits) if edits else []
	if not isinstance(edits, (list, dict)):
		frappe.throw(_("edits must be a list or object"), frappe.ValidationError)
	return edits


@frappe.whitelist()
@rate_limit(limit=30, seconds=3600)
def create_scan(
	file_url: str,
	target_doctype: str,
	company: str,
	idempotency_key: str | None = None,
) -> dict[str, Any]:
	"""Insert a Queued Scan; worker enqueues after commit."""
	if not file_url or not target_doctype or not company:
		frappe.throw(_("file_url, target_doctype, and company are required"), frappe.ValidationError)

	key = (idempotency_key or "").strip() or None
	if key:
		existing = frappe.db.get_value(
			SCAN_DOCTYPE,
			{"idempotency_key": key, "owner": frappe.session.user},
			"name",
		)
		if existing:
			doc = _load(existing, "read")
			return {"name": doc.name, "status": doc.status}

	doc = frappe.get_doc(
		{
			"doctype": SCAN_DOCTYPE,
			"file_url": file_url,
			"target_doctype": target_doctype,
			"company": company,
			"user": frappe.session.user,
			"status": QUEUED,
			"idempotency_key": key,
		}
	)
	doc.flags.skip_file_check = False
	doc.insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_scan(name: str) -> dict[str, Any]:
	"""Return the Scan view model."""
	doc = _load(name, "read")
	return scan_view(doc)


@frappe.whitelist()
def save_edits(name: str, edits: Any, modified: str) -> dict[str, Any]:
	"""Apply field edits; optimistic lock on modified."""
	doc = _load(name, "write")
	_assert_modified(doc, modified)
	doc.apply_edits(_parse_edits(edits))
	doc.reload()
	return scan_view(doc)


@frappe.whitelist()
def make_draft(name: str, modified: str) -> dict[str, Any]:
	"""Create the target draft from frozen read + edits."""
	doc = _load(name, "write")
	_assert_modified(doc, modified)
	result = doc.make_draft()
	doc.reload()
	view = scan_view(doc)
	route = None
	if doc.created_doctype and doc.created_document:
		route = f"/{frappe.scrub(doc.created_doctype)}/{doc.created_document}"
	return {
		"name": result["name"],
		"status": view["status"],
		"created_doctype": view["created_doctype"],
		"created_document": view["created_document"],
		"route": route,
		"view": view,
	}


@frappe.whitelist()
def cancel_scan(name: str) -> dict[str, Any]:
	"""Request cancellation of an in-flight or reviewable Scan."""
	doc = _load(name, "write")
	doc.cancel()
	doc.reload()
	return {"name": doc.name, "status": doc.status}
