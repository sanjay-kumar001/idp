# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Install hooks for the IDP app.

Creates ``IDP User`` and ``IDP Manager`` roles on first install.
"""

import frappe

IDP_USER_ROLE = "IDP User"
IDP_MANAGER_ROLE = "IDP Manager"


def after_install() -> None:
	"""Called by Frappe after the app is installed."""
	ensure_idp_roles()
	_seed_prompt_library()


def ensure_idp_roles() -> None:
	"""Create IDP User and IDP Manager roles if missing (idempotent)."""
	for role_name in (IDP_USER_ROLE, IDP_MANAGER_ROLE):
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def _seed_prompt_library() -> None:
	"""Install the shipped Phase 15 prompt gallery (idempotent)."""
	try:
		from idp.advanced.prompt_library import seed_builtin_prompts

		seed_builtin_prompts(overwrite=False)
	except Exception as exc:
		frappe.log_error(f"IDP prompt library seed failed: {exc}", "IDP after_install")


# Back-compat alias used by older patches / callers
def _ensure_idp_user_role() -> None:
	ensure_idp_roles()
