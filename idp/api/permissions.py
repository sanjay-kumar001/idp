# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Permission helpers for the IDP app.

``has_app_permission`` gates the apps screen. DocType hooks
(``has_*_permission``, company helpers) enforce owner + company scope.
"""

from __future__ import annotations

import frappe

IDP_USER_ROLE = "IDP User"
IDP_MANAGER_ROLE = "IDP Manager"
IDP_ROLES: tuple[str, ...] = (IDP_USER_ROLE, IDP_MANAGER_ROLE, "System Manager", "Administrator")


def has_app_permission() -> bool:
	"""True when the session user may open the IDP app."""
	user = frappe.session.user
	if not user or user == "Guest":
		return False
	return bool(set(frappe.get_roles(user)).intersection(IDP_ROLES))


def get_permitted_companies(user: str | None = None) -> list[str]:
	"""Company names from User Permission rows for *user* (empty if none)."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	if not frappe.db.exists("DocType", "User Permission"):
		return []
	if not frappe.db.exists("DocType", "Company"):
		return []
	return frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)


def _is_admin_or_system_manager(user: str) -> bool:
	if user == "Administrator":
		return True
	return "System Manager" in frappe.get_roles(user)


def _owns_document_log(doc, user: str) -> bool:
	owner = getattr(doc, "owner", None)
	logged_user = getattr(doc, "user", None)
	return owner == user or logged_user == user


def _company_allowed(doc, user: str) -> bool:
	company = getattr(doc, "company", None) or ""
	if not company:
		# Legacy rows: visible only to the owner until PR2 backfill.
		return _owns_document_log(doc, user)
	permitted = get_permitted_companies(user)
	if not permitted:
		return False
	return company in permitted


def has_document_log_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Owner + company scope for *IDP Document Log* (Scan)."""
	user = user or frappe.session.user
	_ = permission_type
	if _is_admin_or_system_manager(user):
		return True
	if not _owns_document_log(doc, user):
		return False
	return _company_allowed(doc, user)


def has_conversation_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Owner-only permission check for *IDP Conversation* documents."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	user_roles = set(frappe.get_roles(user))
	if "System Manager" in user_roles and permission_type in ("read", "export", "print", "email", "report"):
		return True
	owner = getattr(doc, "user", None) or getattr(doc, "owner", None)
	return owner == user


def has_message_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Direct-get check: message belongs to a conversation the user owns."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	user_roles = set(frappe.get_roles(user))
	if "System Manager" in user_roles and permission_type in ("read", "export", "print", "email", "report"):
		return True
	conversation = getattr(doc, "conversation", None)
	if not conversation:
		return False
	conv_owner = frappe.db.get_value("IDP Conversation", conversation, "user")
	if not conv_owner:
		conv_owner = frappe.db.get_value("IDP Conversation", conversation, "owner")
	return conv_owner == user
