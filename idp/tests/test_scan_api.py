# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""API tests for idp.api.scan (PR3)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from idp.api import scan as scan_api
from idp.install import ensure_idp_roles
from idp.scan.state import REVIEW
from idp.scan.view import scan_view


def _company() -> str:
	return frappe.db.get_value("Company", {}, "name")


def _user(email: str, company: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	u = frappe.get_doc("User", email)
	if "IDP User" not in {r.role for r in u.roles}:
		u.add_roles("IDP User")
	if not frappe.db.exists(
		"User Permission", {"user": email, "allow": "Company", "for_value": company}
	):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Company",
				"for_value": company,
			}
		).insert(ignore_permissions=True)
	return email


def _seed_review_scan(*, owner: str, company: str) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "IDP Document Log",
			"file_url": "/private/files/scan-api-test.pdf",
			"file_name": "scan-api-test.pdf",
			"target_doctype": "ToDo",
			"company": company,
			"user": owner,
			"status": REVIEW,
			"extraction_data": {"description": "api"},
		}
	)
	doc.flags.skip_enqueue = True
	doc.flags.skip_file_check = True
	doc.insert(ignore_permissions=True)
	frappe.db.set_value("IDP Document Log", doc.name, "owner", owner, update_modified=False)
	return doc.name


class TestScanAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_idp_roles()
		cls.company = _company()
		cls.user_a = _user("idp.scan.a@example.com", cls.company)
		cls.user_b = _user("idp.scan.b@example.com", cls.company)

	def test_get_scan_other_owner_denied(self):
		name = _seed_review_scan(owner=self.user_a, company=self.company)
		frappe.set_user(self.user_b)
		with self.assertRaises(frappe.PermissionError):
			scan_api.get_scan(name)
		frappe.set_user("Administrator")

	def test_save_edits_stale_modified(self):
		name = _seed_review_scan(owner=self.user_a, company=self.company)
		frappe.set_user(self.user_a)
		with self.assertRaises(frappe.TimestampMismatchError):
			scan_api.save_edits(name, {"description": "x"}, modified="2000-01-01 00:00:00.000000")
		frappe.set_user("Administrator")

	def test_save_edits_response_matches_db(self):
		name = _seed_review_scan(owner=self.user_a, company=self.company)
		frappe.set_user(self.user_a)
		doc = frappe.get_doc("IDP Document Log", name)
		resp = scan_api.save_edits(
			name,
			[{"fieldname": "description", "edited": "from api"}],
			modified=str(doc.modified),
		)
		reloaded = frappe.get_doc("IDP Document Log", name)
		self.assertEqual(resp, scan_view(reloaded))
		frappe.set_user("Administrator")

	def test_create_scan_idempotent(self):
		frappe.set_user(self.user_a)
		# Skip worker side effects
		key = "idem-test-key-1"
		frappe.flags.in_test = True
		# create with skip via monkeypatch after_insert — insert Queued would enqueue;
		# use db-level: call create_scan but patch enqueue
		original = frappe.enqueue
		frappe.enqueue = lambda *a, **k: None
		try:
			r1 = scan_api.create_scan(
				"/private/files/idem.pdf", "ToDo", self.company, idempotency_key=key
			)
			r2 = scan_api.create_scan(
				"/private/files/idem.pdf", "ToDo", self.company, idempotency_key=key
			)
		finally:
			frappe.enqueue = original
		self.assertEqual(r1["name"], r2["name"])
		frappe.set_user("Administrator")

	def test_unknown_edit_field(self):
		name = _seed_review_scan(owner=self.user_a, company=self.company)
		frappe.set_user(self.user_a)
		doc = frappe.get_doc("IDP Document Log", name)
		with self.assertRaises(frappe.ValidationError):
			scan_api.save_edits(
				name,
				[{"fieldname": "zzz_unknown", "edited": "1"}],
				modified=str(doc.modified),
			)
		frappe.set_user("Administrator")
