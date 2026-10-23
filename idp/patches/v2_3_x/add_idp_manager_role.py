# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Ensure IDP Manager role exists and template/correction writes are locked down."""

from __future__ import annotations

import frappe


_MANAGED = (
	"IDP Extraction Template",
	"IDP Extraction Correction",
)
_WRITE_ROLES = frozenset({"System Manager", "IDP Manager"})


def execute() -> None:
	from idp.install import ensure_idp_roles

	ensure_idp_roles()
	for doctype in (
		"idp_document_log",
		"idp_extraction_template",
		"idp_extraction_correction",
	):
		frappe.reload_doc("idp", "doctype", doctype, force=True)
	_harden_custom_perms()
	frappe.clear_cache()


def _harden_custom_perms() -> None:
	"""TaxMate copies DocPerm into Custom DocPerm; strip write for non-managers."""
	if not frappe.db.exists("DocType", "Custom DocPerm"):
		return
	for dt in _MANAGED:
		if not frappe.db.exists("DocType", dt):
			continue
		for row in frappe.get_all(
			"Custom DocPerm",
			filters={"parent": dt},
			fields=["name", "role"],
		):
			if row.role in _WRITE_ROLES:
				frappe.db.set_value(
					"Custom DocPerm",
					row.name,
					{"read": 1, "write": 1, "create": 1, "delete": 1},
					update_modified=False,
				)
				continue
			create = 1 if (dt == "IDP Extraction Correction" and row.role == "IDP User") else 0
			frappe.db.set_value(
				"Custom DocPerm",
				row.name,
				{"write": 0, "create": create, "delete": 0},
				update_modified=False,
			)
		# Ensure IDP Manager has a Custom DocPerm row when Custom DocPerm is in use
		if frappe.db.exists("Custom DocPerm", {"parent": dt}):
			exists = frappe.db.exists(
				"Custom DocPerm",
				{"parent": dt, "role": "IDP Manager", "permlevel": 0},
			)
			if not exists:
				frappe.get_doc(
					{
						"doctype": "Custom DocPerm",
						"parent": dt,
						"parenttype": "DocType",
						"parentfield": "permissions",
						"role": "IDP Manager",
						"permlevel": 0,
						"if_owner": 0,
						"read": 1,
						"write": 1,
						"create": 1,
						"delete": 1,
						"print": 1,
						"email": 1,
						"report": 1,
						"export": 1,
						"share": 1,
					}
				).insert(ignore_permissions=True)
