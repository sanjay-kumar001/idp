# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

app_name = "idp"
app_title = "Intelligent Document Processing"
app_publisher = "Sanjay Kumar"
app_description = "The IDP subsystem extracts structured data from uploaded documents and creates ERPNext records from the extracted data."
app_email = "sanjay.kumar001@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe", "erpnext"]

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "idp",
		"logo": "/assets/idp/logo.png",
		"title": "Intelligent Document Processing",
		"route": "/idp",
		"has_permission": "idp.api.permissions.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/idp/css/idp.css"
# app_include_js = "/assets/idp/js/idp.js"

# include js, css files in header of web template
# web_include_css = "/assets/idp/css/idp.css"
# web_include_js = "/assets/idp/js/idp.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "idp/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "idp/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Website Route Rules
# --------------------
# Route all /idp/* paths to the Vue SPA entry point
website_route_rules = [
	{"from_route": "/idp/<path:app_path>", "to_route": "idp"},
]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "idp.utils.jinja_methods",
# 	"filters": "idp.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "idp.install.before_install"
after_install = "idp.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "idp.uninstall.before_uninstall"
# after_uninstall = "idp.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "idp.utils.before_app_install"
# after_app_install = "idp.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "idp.utils.before_app_uninstall"
# after_app_uninstall = "idp.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "idp.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"IDP Conversation": "idp.idp.doctype.idp_conversation.idp_conversation.get_permission_query_conditions",
	"IDP Message": "idp.idp.doctype.idp_message.idp_message.get_permission_query_conditions",
	"IDP Batch Job": "idp.idp.doctype.idp_batch_job.idp_batch_job.get_permission_query_conditions",
	"IDP Document Log": "idp.idp.doctype.idp_document_log.idp_document_log.get_permission_query_conditions",
}

has_permission = {
	"IDP Conversation": "idp.api.permissions.has_conversation_permission",
	"IDP Message": "idp.api.permissions.has_message_permission",
	"IDP Document Log": "idp.api.permissions.has_document_log_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# Phase 26 §26.3 — invalidate tool-registry / plugin caches when a
# user's role set changes, so per-tool access decisions stay fresh.
doc_events = {
	"Has Role": {
		"after_insert": "idp.plugins.loader.invalidate_plugin_cache",
		"on_trash": "idp.plugins.loader.invalidate_plugin_cache",
	},
}

# Scan extension points (other apps append handlers)
# idp_after_extract(doc) — after frozen read; failures are logged, read still succeeds
# idp_before_make_draft(doc) — before target insert; failures block draft
idp_after_extract = []
idp_before_make_draft = []

# Scheduled Tasks

# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"idp.scan.watchdog.fail_stale_reading_scans",
		],
	},
	"daily": [
		"idp.core.retention.daily",
	],
	"weekly": [
		"idp.advanced.fine_tuning.weekly_export",
		# Phase 28 — TE regression watchdog.  Compares this-week's
		# avg ``tokens_per_extracted_field`` to last week's and emails
		# System Managers when the delta exceeds
		# ``IDP Settings.te_regression_alert_pct`` (default 15%).
		"idp.core.metrics.weekly_te_regression_check",
	],
}

# Testing
# -------

# before_tests = "idp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "idp.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "idp.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "idp.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["idp.utils.before_request"]
# after_request = ["idp.utils.after_request"]

# Job Events
# ----------
# before_job = ["idp.utils.before_job"]
# after_job = ["idp.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"idp.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Fixtures
# --------
# Phase 26 §26.7 — ship sensible defaults for prompt templates / skills /
# the built-in plugin configuration row.  Site admins can override or
# delete these freely; they are re-applied on ``bench migrate`` only
# when the row is missing.
fixtures = [
	{"doctype": "IDP Plugin Configuration", "filters": [["plugin_name", "in", ["core"]]]},
	{"doctype": "IDP Prompt Template", "filters": [["template_name", "in", [
		"Generic Document Extraction",
		"Sales Invoice Extraction",
		"Purchase Invoice Extraction",
	]]]},
	{"doctype": "IDP Skill", "filters": [["skill_name", "in", [
		"Bill of Lading Fields",
		"VAT Tax Rounding",
	]]]},
]

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
