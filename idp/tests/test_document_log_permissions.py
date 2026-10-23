# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Permission and get_job_status tests for IDP Document Log (PR1)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from idp.api.background import get_job_status
from idp.api.permissions import has_document_log_permission, has_message_permission
from idp.install import ensure_idp_roles


def _pick_companies() -> tuple[str, str]:
	"""Reuse two existing companies; create a second only if the site has one."""
	names = frappe.get_all("Company", pluck="name", limit=2)
	if len(names) >= 2:
		return names[0], names[1]
	if len(names) == 1:
		# Duplicate logical company not available — use blank for B cases carefully
		return names[0], names[0]
	frappe.throw("Site needs at least one Company for IDP permission tests")


def _make_user(email: str, company: str | None) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		u.insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	roles = {r.role for r in user.roles}
	if "IDP User" not in roles:
		user.add_roles("IDP User")
	if company and frappe.db.exists("DocType", "User Permission"):
		exists = frappe.db.exists(
			"User Permission",
			{"user": email, "allow": "Company", "for_value": company},
		)
		if not exists:
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Company",
					"for_value": company,
				}
			).insert(ignore_permissions=True)
	return email


def _make_log(*, owner: str, company: str | None, status: str = "Uploaded") -> str:
	doc = frappe.get_doc(
		{
			"doctype": "IDP Document Log",
			"file_url": "/private/files/idp-perm-test.pdf",
			"file_name": "idp-perm-test.pdf",
			"target_doctype": "Purchase Invoice",
			"company": company,
			"user": owner,
			"status": status,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.set_value("IDP Document Log", doc.name, "owner", owner, update_modified=False)
	return doc.name


class TestDocumentLogPermissions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_idp_roles()
		cls.company_a, cls.company_b = _pick_companies()
		cls.same_company = cls.company_a == cls.company_b
		cls.user_a = _make_user("idp.perm.a@example.com", cls.company_a)
		cls.user_b = _make_user("idp.perm.b@example.com", cls.company_b if not cls.same_company else cls.company_a)

	def test_owner_can_read_own_log(self):
		name = _make_log(owner=self.user_a, company=self.company_a)
		doc = frappe.get_doc("IDP Document Log", name)
		self.assertTrue(has_document_log_permission(doc, user=self.user_a, permission_type="read"))

	def test_other_user_denied(self):
		name = _make_log(owner=self.user_a, company=self.company_a)
		doc = frappe.get_doc("IDP Document Log", name)
		self.assertFalse(has_document_log_permission(doc, user=self.user_b, permission_type="read"))

	def test_wrong_company_denied_even_for_owner_without_up(self):
		if self.same_company:
			self.skipTest("Need two companies on site")
		# Clear user_a UP for B if any, keep only A
		frappe.db.delete(
			"User Permission",
			{"user": self.user_a, "allow": "Company", "for_value": self.company_b},
		)
		name = _make_log(owner=self.user_a, company=self.company_b)
		doc = frappe.get_doc("IDP Document Log", name)
		self.assertFalse(has_document_log_permission(doc, user=self.user_a, permission_type="read"))

	def test_get_job_status_requires_read(self):
		name = _make_log(owner=self.user_a, company=self.company_a)
		frappe.set_user(self.user_b)
		with self.assertRaises(frappe.PermissionError):
			get_job_status(name)
		frappe.set_user("Administrator")

	def test_get_job_status_owner_ok(self):
		name = _make_log(owner=self.user_a, company=self.company_a)
		frappe.set_user(self.user_a)
		result = get_job_status(name)
		self.assertEqual(result["job_id"], name)
		self.assertEqual(result["status"], "Uploaded")
		frappe.set_user("Administrator")

	def test_get_job_status_missing_unknown(self):
		frappe.set_user(self.user_a)
		result = get_job_status("IDPLOG-DOES-NOT-EXIST")
		self.assertEqual(result["status"], "unknown")
		frappe.set_user("Administrator")

	def test_template_idp_user_cannot_write(self):
		frappe.clear_cache(doctype="IDP Extraction Template")
		frappe.set_user(self.user_a)
		self.assertFalse(frappe.has_permission("IDP Extraction Template", "write"))
		frappe.set_user("Administrator")

	def test_message_has_permission_denies_other_conversation(self):
		conv = frappe.get_doc(
			{
				"doctype": "IDP Conversation",
				"user": self.user_a,
				"title": "perm-test",
			}
		)
		try:
			conv.insert(ignore_permissions=True)
		except Exception:
			self.skipTest("IDP Conversation insert failed in this site fixture")
		msg = frappe.get_doc(
			{
				"doctype": "IDP Message",
				"conversation": conv.name,
				"role": "user",
				"content": "hi",
			}
		)
		try:
			msg.insert(ignore_permissions=True)
		except Exception:
			self.skipTest("IDP Message insert failed in this site fixture")
		self.assertTrue(has_message_permission(msg, user=self.user_a, permission_type="read"))
		self.assertFalse(has_message_permission(msg, user=self.user_b, permission_type="read"))
