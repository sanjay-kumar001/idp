# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from idp.scan.state import FAILED, READING
from idp.scan.watchdog import fail_stale_reading_scans


class TestScanWatchdog(IntegrationTestCase):
	def test_stale_reading_marked_failed(self):
		company = frappe.db.get_value("Company", {}, "name")
		doc = frappe.get_doc(
			{
				"doctype": "IDP Document Log",
				"file_url": "/private/files/watchdog.pdf",
				"target_doctype": "ToDo",
				"company": company,
				"status": READING,
				"user": frappe.session.user,
			}
		)
		doc.flags.skip_enqueue = True
		doc.flags.skip_file_check = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(
			"IDP Document Log",
			doc.name,
			"modified",
			add_to_date(now_datetime(), minutes=-20),
			update_modified=False,
		)
		n = fail_stale_reading_scans()
		self.assertGreaterEqual(n, 1)
		doc.reload()
		self.assertEqual(doc.status, FAILED)
		self.assertEqual(doc.error_key, "worker_lost")
