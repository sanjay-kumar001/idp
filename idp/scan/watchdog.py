# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Mark Scans stuck in Reading as Failed (worker_lost)."""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from idp.scan.state import FAILED, READING

# Worker timeout (600s) + grace
READING_TIMEOUT_MINUTES = 15


def fail_stale_reading_scans() -> int:
	"""Scheduler entry: every 5 minutes. Returns number of rows updated."""
	cutoff = add_to_date(now_datetime(), minutes=-READING_TIMEOUT_MINUTES)
	names = frappe.get_all(
		"IDP Document Log",
		filters={"status": ("in", [READING, "Extracting"]), "modified": ("<", cutoff)},
		pluck="name",
	)
	for name in names:
		doc = frappe.get_doc("IDP Document Log", name)
		doc.flags.skip_file_check = True
		doc.status = FAILED
		doc.error_key = "worker_lost"
		doc.error_message = (doc.error_message or "")[:400] + " worker_lost"
		doc.save(ignore_permissions=True)
	return len(names)
