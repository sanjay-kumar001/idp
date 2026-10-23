# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Map legacy Document Log statuses to the Scan machine; backfill company when possible."""

from __future__ import annotations

import frappe

from idp.scan.state import STATUS_BACKFILL


def execute() -> None:
	frappe.reload_doc("idp", "doctype", "idp_scan_field", force=True)
	frappe.reload_doc("idp", "doctype", "idp_document_log", force=True)

	for old, new in STATUS_BACKFILL.items():
		frappe.db.sql(
			"""
			UPDATE `tabIDP Document Log`
			SET status = %s
			WHERE status = %s
			""",
			(new, old),
		)

	# Best-effort: first Company User Permission for the owner.
	if frappe.db.exists("DocType", "User Permission") and frappe.db.exists("DocType", "Company"):
		frappe.db.sql(
			"""
			UPDATE `tabIDP Document Log` log
			INNER JOIN (
				SELECT user, MIN(for_value) AS company
				FROM `tabUser Permission`
				WHERE allow = 'Company'
				GROUP BY user
			) up ON up.user = log.owner
			SET log.company = up.company
			WHERE IFNULL(log.company, '') = ''
			"""
		)
	frappe.clear_cache()
