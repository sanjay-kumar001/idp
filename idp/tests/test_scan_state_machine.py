# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Controller tests for IDP Scan status machine and make_draft (PR2)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from idp.scan.state import QUEUED, READING, REVIEW, SAVED, SAVING


def _company() -> str:
	name = frappe.db.get_value("Company", {}, "name")
	if not name:
		frappe.throw("Need a Company for scan tests")
	return name


def _scan(**overrides):
	company = _company()
	doc = frappe.get_doc(
		{
			"doctype": "IDP Document Log",
			"file_url": "/private/files/scan-sm-test.pdf",
			"file_name": "scan-sm-test.pdf",
			"target_doctype": "ToDo",
			"company": company,
			"user": frappe.session.user,
			"status": REVIEW,
			"extraction_data": {"description": "from ocr"},
			"ocr_text": "from ocr",
			"file_sha256": "a" * 64,
			"pipeline_version": 1,
			"path_taken": "rule",
			**overrides,
		}
	)
	doc.flags.skip_enqueue = True
	doc.flags.skip_file_check = True
	doc.insert(ignore_permissions=True)
	return doc


class TestScanStateMachine(IntegrationTestCase):
	def test_illegal_transition_rejected(self):
		doc = _scan(status=REVIEW)
		doc.status = QUEUED
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_legal_review_to_saving(self):
		doc = _scan(status=REVIEW)
		doc.status = SAVING
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("IDP Document Log", doc.name, "status"), SAVING)

	def test_frozen_extraction_cannot_change_in_review(self):
		doc = _scan(status=REVIEW, extraction_data={"description": "a"})
		doc.extraction_data = {"description": "b"}
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_apply_edits_unknown_field(self):
		doc = _scan(status=REVIEW)
		with self.assertRaises(frappe.ValidationError):
			doc.apply_edits([{"fieldname": "not_a_real_field_zz", "edited": "x"}])

	def test_apply_edits_ok(self):
		doc = _scan(status=REVIEW)
		doc.apply_edits([{"fieldname": "description", "edited": "edited text"}])
		doc.reload()
		rows = {r.fieldname: r.edited for r in doc.scan_fields}
		self.assertEqual(rows.get("description"), "edited text")

	def test_make_draft_twice_one_document(self):
		doc = _scan(status=REVIEW, extraction_data={"description": "draft once"})
		doc.apply_edits([{"fieldname": "description", "edited": "draft once"}])
		first = doc.make_draft()
		doc.reload()
		second = doc.make_draft()
		self.assertEqual(first["created_document"], second["created_document"])
		self.assertEqual(doc.status, SAVED)
		todo = frappe.get_doc("ToDo", first["created_document"])
		self.assertIn("draft once", todo.description or "")

	def test_make_draft_equals_frozen_plus_edits(self):
		doc = _scan(
			status=REVIEW,
			extraction_data={"description": "frozen"},
		)
		doc.apply_edits([{"fieldname": "description", "edited": "edited"}])
		result = doc.make_draft()
		todo = frappe.get_doc("ToDo", result["created_document"])
		self.assertEqual(todo.description, "edited")
