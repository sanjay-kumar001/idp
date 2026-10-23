# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""IDP Document Log (Scan) controller — state machine and draft creation."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.model.document import Document

from idp.scan.state import (
	CANCELLED,
	FAILED,
	FROZEN_FIELDS,
	QUEUED,
	READING,
	REVIEW,
	REVIEW_STATUSES,
	SAVED,
	SAVING,
	TRANSITIONS,
)


class IDPDocumentLog(Document):
	"""Scan row: one OCR/read per file, owner+company scoped."""

	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user
		if not self.status:
			self.status = QUEUED

	def validate(self):
		self._validate_status_transition()
		self._validate_frozen_fields()
		self._validate_idempotency_key()
		self._validate_company_for_queued()
		self._validate_file_access()

	def after_insert(self):
		if self.status != QUEUED or self.flags.get("skip_enqueue"):
			return
		frappe.enqueue(
			"idp.idp.doctype.idp_document_log.idp_document_log.run_read_job",
			queue="long",
			timeout=600,
			job_id=self.name,
			enqueue_after_commit=True,
			scan_name=self.name,
		)

	def before_save(self):
		tokens = self.llm_tokens_used or 0
		if not tokens:
			self.tokens_per_extracted_field = 0.0
			return
		data = self._as_dict(self.extraction_data)
		field_count = sum(1 for v in data.values() if v not in (None, "", [], {}))
		self.tokens_per_extracted_field = (
			0.0 if field_count <= 0 else round(tokens / field_count, 3)
		)

	# ------------------------------------------------------------------
	# Public controller API (called from idp.api.scan / workers)
	# ------------------------------------------------------------------

	def run_read(self) -> None:
		"""Worker: single read pass → freeze fields → Review (or Failed / Cancelled)."""
		self.reload()
		if self.cancel_requested or self.status == CANCELLED:
			self._set_status(CANCELLED, error_key="cancelled")
			self.save(ignore_permissions=True)
			return
		if self.status not in (QUEUED, READING, "Uploaded", "Extracting"):
			return

		self._set_status(READING)
		self.save(ignore_permissions=True)
		frappe.publish_realtime(
			"idp_scan_stage",
			{"name": self.name, "status": READING, "stage": "reading"},
			doctype=self.doctype,
			docname=self.name,
		)

		if self.cancel_requested:
			self._set_status(CANCELLED, error_key="cancelled")
			self.save(ignore_permissions=True)
			return

		try:
			from idp.api.extract import extract_document

			result = extract_document(
				file_url=self.file_url,
				target_doctype=self.target_doctype,
				company=self.company,
				language=self.ocr_language or "en",
			)
		except Exception as exc:
			self._set_status(FAILED, error_key="read_failed", error_message=str(exc)[:500])
			self.save(ignore_permissions=True)
			return

		if self.cancel_requested:
			self._set_status(CANCELLED, error_key="cancelled")
			self.save(ignore_permissions=True)
			return

		if not result.get("success"):
			self._set_status(
				FAILED,
				error_key=result.get("error_key") or "read_failed",
				error_message=(result.get("error") or "")[:500],
			)
			self.save(ignore_permissions=True)
			return

		extracted = result.get("extracted_data") or {}
		self.extraction_data = extracted
		self.ocr_confidence = result.get("confidence")
		self.processing_time_ms = result.get("processing_time_ms")
		if result.get("ocr_text") and not self.ocr_text:
			self.ocr_text = str(result["ocr_text"])[:200000]
		if result.get("path_taken"):
			self.path_taken = result["path_taken"]
		self._seed_scan_fields(extracted)
		# Mutate in memory before freeze; hook must not save.
		self._run_hook("idp_after_extract", fail_open=True)
		self._set_status(REVIEW)
		self.save(ignore_permissions=True)
		frappe.publish_realtime(
			"idp_scan_stage",
			{"name": self.name, "status": REVIEW, "stage": "review"},
			doctype=self.doctype,
			docname=self.name,
		)

	def cancel(self) -> None:
		"""Request cancel; worker moves to Cancelled between stages."""
		if self.status in (SAVED, CANCELLED):
			frappe.throw(
				"Scan cannot be cancelled in this status.",
				frappe.ValidationError,
			)
		self.cancel_requested = 1
		if self.status in (QUEUED, "Uploaded"):
			self._set_status(CANCELLED, error_key="cancelled")
		self.save()

	def apply_edits(self, edits: list[dict[str, Any]] | dict[str, Any]) -> None:
		"""Apply per-field edits while in Review."""
		if self.status not in REVIEW_STATUSES:
			frappe.throw("Edits are only allowed in Review.", frappe.ValidationError)
		if not self.target_doctype:
			frappe.throw("target_doctype is required.", frappe.ValidationError)

		meta = frappe.get_meta(self.target_doctype)
		valid_names = {df.fieldname for df in meta.fields}
		# Allow common nested keys used by mappers
		valid_names |= {"items", "taxes", "remarks", "supplier", "customer", "posting_date", "bill_no", "bill_date"}

		rows = edits if isinstance(edits, list) else [
			{"fieldname": k, "edited": v} for k, v in (edits or {}).items()
		]
		by_name = {r.fieldname: r for r in self.scan_fields}
		for row in rows:
			fname = row.get("fieldname")
			if not fname or fname not in valid_names:
				frappe.throw(f"Unknown field: {fname}", frappe.ValidationError)
			edited = row.get("edited")
			if fname in by_name:
				by_name[fname].edited = edited if edited is None else str(edited)
			else:
				self.append(
					"scan_fields",
					{
						"fieldname": fname,
						"extracted": row.get("extracted"),
						"edited": edited if edited is None else str(edited),
						"confidence": row.get("confidence"),
						"flags": row.get("flags"),
					},
				)
		self.save()

	def make_draft(self) -> dict[str, Any]:
		"""Build target draft from frozen read + edits. Idempotent when already Saved."""
		if self.status == SAVED and self.created_document:
			return {
				"name": self.name,
				"status": self.status,
				"created_doctype": self.created_doctype,
				"created_document": self.created_document,
			}
		if self.status not in REVIEW_STATUSES:
			frappe.throw("Draft can only be made from Review.", frappe.ValidationError)

		self._run_hook("idp_before_make_draft", fail_open=False)
		payload = self._merged_payload()
		self._set_status(SAVING)
		self.save()

		try:
			target = frappe.get_doc({"doctype": self.target_doctype, **payload})
			if self.company and hasattr(target, "company"):
				target.company = self.company
			target.insert()
		except Exception as exc:
			self._set_status(REVIEW, error_key="make_draft_failed", error_message=str(exc)[:500])
			self.save()
			raise

		self.created_doctype = self.target_doctype
		self.created_document = target.name
		self._set_status(SAVED)
		self.save()
		return {
			"name": self.name,
			"status": self.status,
			"created_doctype": self.created_doctype,
			"created_document": self.created_document,
		}

	# ------------------------------------------------------------------
	# Internals
	# ------------------------------------------------------------------

	def _set_status(
		self,
		status: str,
		*,
		error_key: str | None = None,
		error_message: str | None = None,
	) -> None:
		self.status = status
		if error_key is not None:
			self.error_key = error_key
		if error_message is not None:
			self.error_message = error_message

	def _validate_status_transition(self) -> None:
		if self.is_new():
			return
		old = self.get_db_value("status")
		if not old or old == self.status:
			return
		allowed = TRANSITIONS.get(old)
		if allowed is None:
			return
		if self.status not in allowed:
			frappe.throw(
				f"Illegal status transition {old} → {self.status}",
				frappe.ValidationError,
			)

	def _validate_frozen_fields(self) -> None:
		if self.is_new():
			return
		old_status = self.get_db_value("status")
		# Fields become immutable once the scan has left Reading.
		if old_status in (None, QUEUED, READING, "Uploaded", "Extracting"):
			return
		for field in FROZEN_FIELDS:
			old = self.get_db_value(field)
			new = self.get(field)
			if self._norm(old) != self._norm(new):
				frappe.throw(
					f"Frozen field cannot change: {field}",
					frappe.ValidationError,
				)

	def _validate_idempotency_key(self) -> None:
		key = (self.idempotency_key or "").strip()
		if not key:
			return
		owner = self.owner or frappe.session.user
		existing = frappe.db.get_value(
			"IDP Document Log",
			{
				"idempotency_key": key,
				"owner": owner,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if existing:
			frappe.throw(
				f"Duplicate idempotency_key; existing scan {existing}",
				frappe.ValidationError,
			)

	def _validate_company_for_queued(self) -> None:
		if self.status == QUEUED and not self.company and self.is_new():
			frappe.throw("company is required.", frappe.ValidationError)

	def _validate_file_access(self) -> None:
		if not self.file_url or self.flags.get("skip_file_check"):
			return
		if frappe.session.user in ("Administrator", "Guest"):
			return
		# File rows are optional; URL safety is enforced in extract/security.
		file_name = frappe.db.get_value("File", {"file_url": self.file_url}, "name")
		if not file_name:
			return
		try:
			frappe.get_doc("File", file_name).check_permission("read")
		except frappe.PermissionError:
			frappe.throw("No read access to the attached file.", frappe.PermissionError)

	def _seed_scan_fields(self, extracted: dict[str, Any]) -> None:
		if self.scan_fields:
			return
		for key, value in extracted.items():
			if key.startswith("_") or isinstance(value, (list, dict)):
				continue
			self.append(
				"scan_fields",
				{
					"fieldname": key,
					"extracted": "" if value is None else str(value),
					"edited": None,
					"confidence": None,
				},
			)

	def _merged_payload(self) -> dict[str, Any]:
		base = dict(self._as_dict(self.extraction_data))
		for row in self.scan_fields:
			if row.edited not in (None, ""):
				base[row.fieldname] = row.edited
			elif row.fieldname not in base and row.extracted not in (None, ""):
				base[row.fieldname] = row.extracted
		meta = frappe.get_meta(self.target_doctype)
		allowed = {df.fieldname for df in meta.fields if df.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold")}
		return {k: v for k, v in base.items() if k in allowed}

	def _run_hook(self, hook_name: str, *, fail_open: bool) -> None:
		for path in frappe.get_hooks(hook_name) or []:
			try:
				frappe.get_attr(path)(self)
			except Exception:
				frappe.log_error(title=f"IDP hook {hook_name} failed")
				if not fail_open:
					raise
				self.error_key = self.error_key or f"hook_{hook_name}_failed"

	@staticmethod
	def _as_dict(data: Any) -> dict[str, Any]:
		if isinstance(data, str):
			try:
				data = json.loads(data) if data else {}
			except ValueError:
				data = {}
		return data if isinstance(data, dict) else {}

	@staticmethod
	def _norm(value: Any) -> Any:
		if isinstance(value, str):
			try:
				return json.loads(value)
			except ValueError:
				return value
		return value


def run_read_job(scan_name: str) -> None:
	"""RQ entry point for after_insert enqueue."""
	doc = frappe.get_doc("IDP Document Log", scan_name)
	doc.run_read()


def get_permission_query_conditions(user: str | None = None) -> str:
	"""Restrict list queries to the caller's scans (owner + company)."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	if "System Manager" in frappe.get_roles(user):
		return ""

	from idp.api.permissions import get_permitted_companies

	escaped = frappe.db.escape(user)
	owner_clause = (
		f"(`tabIDP Document Log`.owner = {escaped} "
		f"OR IFNULL(`tabIDP Document Log`.user, '') = {escaped})"
	)
	companies = get_permitted_companies(user)
	if not companies:
		return f"({owner_clause} AND IFNULL(`tabIDP Document Log`.company, '') = '')"
	in_list = ", ".join(frappe.db.escape(c) for c in companies)
	return (
		f"({owner_clause} AND ("
		f"IFNULL(`tabIDP Document Log`.company, '') = '' "
		f"OR `tabIDP Document Log`.company IN ({in_list})))"
	)
