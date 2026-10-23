# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Phase 16 public API — LLM access + hybrid field mapping.

All endpoints require an authenticated user.  ``hybrid_map`` runs as
the calling user, while diagnostic / settings endpoints are restricted
to System Manager via :func:`frappe.only_for`.
"""

from __future__ import annotations

from typing import Any

import frappe

from idp.core.exceptions import LLMError
from idp.core.logger import get_logger

logger = get_logger("idp.api.llm")


@frappe.whitelist()
def list_providers() -> dict:
	"""Return the registered LLM providers + known model metadata.

	Useful for the settings UI's provider/model picker.  Auto-loads any
	site-declared custom providers as a side effect.
	"""

	from idp.llm.model_registry import as_dict, list_known_models
	from idp.llm.providers.registry import list_registered_providers

	return {
		"providers": list_registered_providers(),
		"models": [as_dict(m) for m in list_known_models()],
	}


@frappe.whitelist()
def estimate_chat_cost(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
	"""Estimate USD cost for a hypothetical chat completion."""

	from idp.llm.providers.base import TokenUsage
	from idp.llm.token_counter import estimate_cost

	usage = TokenUsage(prompt=int(prompt_tokens), completion=int(completion_tokens))
	return {
		"model": model,
		"prompt_tokens": usage.prompt,
		"completion_tokens": usage.completion,
		"total_tokens": usage.total,
		"estimated_cost_usd": estimate_cost(usage, model),
	}


@frappe.whitelist()
def chat(messages: list | str, model: str | None = None, **extra: Any) -> dict:
	"""Thin pass-through to :meth:`LLMClient.chat`.

	System Manager only — this is a debugging surface, not an end-user
	feature; the Phase 18 chatbot will expose a user-facing wrapper.
	"""

	frappe.only_for("System Manager")

	from idp.llm.client import LLMClient

	if isinstance(messages, str):
		import json as _json

		messages = _json.loads(messages)
	if not isinstance(messages, list):
		frappe.throw("messages must be a JSON list of {role, content} objects")

	client = LLMClient.from_settings()
	response = client.chat(messages, model=model, user=frappe.session.user, **extra)

	return {
		"content": response.content,
		"tool_calls": [
			{"name": tc.name, "arguments": tc.arguments, "id": tc.call_id} for tc in response.tool_calls
		],
		"finish_reason": response.finish_reason,
		"usage": {
			"prompt": response.usage.prompt,
			"completion": response.usage.completion,
			"total": response.usage.total,
		},
		"model": response.model,
		"provider": response.provider,
		"cost_usd": response.raw.get("_idp_cost_usd"),
	}


@frappe.whitelist()
def hybrid_map(
	file_url: str,
	target_doctype: str,
	company: str | None = None,
	source_lang: str | None = None,
	output_language: str | None = None,
) -> dict:
	"""Run hybrid (rule + LLM) mapping for an uploaded file.

	Returns a serialisable view of :class:`MappedDocument` with provenance
	in ``confidence_scores`` so the UI can show which fields the LLM
	contributed.

	Phase 22 additions
	------------------
	* ``source_lang`` — PaddleOCR language code of the document (or
	  ``"auto"``).  When omitted the IDP Settings default is used.
	* ``output_language`` — language for narrative-field translation and
	  any LLM-facing prompts.  When omitted IDP Settings's default is
	  used.
	"""

	from idp.extractors import extract_content
	from idp.llm.client import LLMClient
	from idp.mappers import FieldMapper
	from idp.mappers.hybrid_mapper import HybridFieldMapper

	if not file_url:
		frappe.throw("file_url is required")
	if not target_doctype:
		frappe.throw("target_doctype is required")

	resolved_source, resolved_output = _resolve_language_settings(
		source_lang=source_lang,
		output_language=output_language,
		file_url=file_url,
	)

	# Phase 22 — pass the resolved Paddle language to the extractor so
	# downstream OCR / table extraction picks the matching model.
	extract_lang = resolved_source if resolved_source and resolved_source != "auto" else "en"
	extracted = extract_content(file_url, lang=extract_lang)

	try:
		llm_client = LLMClient.from_settings()
	except LLMError as exc:
		# Hybrid mapper disabled or LLM unavailable — fall back to rule-only
		# rather than failing the request.
		logger.info(f"hybrid_map falling back to rule-only mapping: {exc}")
		mapped = FieldMapper().map_fields(
			extracted,
			target_doctype,
			company=company,
			source_lang=resolved_source,
		)
		mapped.warnings.append(f"hybrid: llm unavailable ({exc}); rule-only mapping returned")
	else:
		mapper = HybridFieldMapper(FieldMapper(), llm_client)
		mapped = mapper.map_fields(
			extracted,
			target_doctype,
			company=company,
			user=frappe.session.user,
			source_lang=resolved_source,
			output_language=resolved_output,
		)

	return {
		"doctype": mapped.doctype,
		"header": mapped.header,
		"items": mapped.items,
		"taxes": getattr(mapped, "taxes", []),
		"unmapped_fields": mapped.unmapped_fields,
		"confidence_scores": mapped.confidence_scores,
		"warnings": mapped.warnings,
		"link_resolutions": mapped.link_resolutions,
		"source_lang": resolved_source,
		"output_language": resolved_output,
	}


def _resolve_language_settings(
	*,
	source_lang: str | None,
	output_language: str | None,
	file_url: str | None = None,
) -> tuple[str | None, str]:
	"""Resolve ``(source_lang, output_language)`` for a hybrid_map call.

	Order of precedence:

	1. Explicit kwargs.
	2. IDP Settings defaults.
	3. Built-in defaults (``"en"`` / ``"English"``).

	When the resolved ``source_lang`` is ``"auto"`` and language
	auto-detection is enabled in settings, we run
	:func:`idp.ocr.engine.detect_language` on *file_url* to pick a
	concrete code so the rule mapper / hybrid LLM both see the right
	language.
	"""

	settings: dict = {}
	try:
		single = frappe.db.get_singles_dict("IDP Settings") or {}
		settings = dict(single)
	except Exception:
		settings = {}

	resolved_source = (source_lang or settings.get("default_ocr_language") or "en").strip() or "en"
	resolved_output = (
		output_language or settings.get("default_output_language") or "English"
	).strip() or "English"

	auto_detect = bool(int(settings.get("enable_language_auto_detect") or 1))
	if resolved_source.lower() == "auto" and auto_detect and file_url:
		try:
			from idp.ocr.engine import detect_language_isolated

			# detect_language_isolated expects a real path; ``extract_from_file``
			# does the same kind of resolution.  We pass the URL through
			# unchanged — the OCR layer accepts both file_url and path.
			# Isolated (subprocess + timeout) so a stalled model
			# download/init can't hang this request indefinitely.
			resolved_source = detect_language_isolated(file_url) or "en"
		except Exception as exc:
			logger.debug(f"hybrid_map language auto-detect failed ({exc}); falling back to 'en'")
			resolved_source = "en"

	return resolved_source, resolved_output


@frappe.whitelist()
def detect_file_language(file_url: str) -> dict:
	"""Phase 22 — return PaddleOCR's best-guess language for *file_url*.

	Used by the upload UI when the user picks ``ocr_language="auto"`` so
	the conversation row can be persisted with a concrete code (and the
	user can override on the spot).  Falls back to ``"en"`` rather than
	raising on any detection error — the resulting confidence indicator
	is therefore advisory only.
	"""

	from idp.ocr.engine import detect_language_isolated

	if not file_url:
		frappe.throw("file_url is required")
	try:
		lang = detect_language_isolated(file_url) or "en"
	except Exception as exc:
		logger.debug(f"detect_file_language failed ({exc}); returning 'en'")
		lang = "en"
	return {"file_url": file_url, "language": lang}


@frappe.whitelist()
def translate(
	text: str,
	source_lang: str,
	target_lang: str,
) -> dict:
	"""Phase 22 — whitelisted translation for ad-hoc UI use.

	Returns ``{"translated": str, "was_translated": bool}``.  Mirrors
	:func:`idp.llm.translation.translate_text` so the chatbot
	frontend can call it without going through the hybrid mapper.
	"""

	from idp.llm.translation import translate_text

	if not text:
		return {"translated": "", "was_translated": False}
	translated, ok = translate_text(
		text,
		source_lang=source_lang or "en",
		target_lang=target_lang or "English",
	)
	return {"translated": translated, "was_translated": bool(ok)}
