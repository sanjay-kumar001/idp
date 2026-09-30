# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Hybrid rule + LLM field mapper (Phase 16).

Strategy:

1. Run the deterministic :class:`FieldMapper` first.
2. If every required field is filled and the average confidence is at
   or above the configured threshold, return the rule mapping verbatim
   (free, fast, deterministic).
3. Otherwise call the LLM via :class:`LLMClient` with the field-mapping
   tool schema, and merge the response back in **without overwriting**
   high-confidence rule values.
4. Tag every field with provenance (``rule`` / ``template`` / ``llm``)
   in :attr:`MappedDocument.confidence_scores` so downstream UIs and
   audits can see which side won.
5. On any LLM failure, return the rule-only mapping with a warning —
   the hybrid mapper must never make extraction *less* reliable than
   the rule mapper alone.
"""

from __future__ import annotations

from dataclasses import asdict, fields as _dc_fields, is_dataclass
from typing import Any

from idp.core.cache import cache_get, cache_set
from idp.core.exceptions import LLMError
from idp.core.logger import get_logger
from idp.llm.prompts import build_system_prompt, build_user_message
from idp.llm.schemas import FIELD_MAPPING_TOOL_SCHEMA
from idp.mappers.base import MappedDocument

logger = get_logger("idp.mappers.hybrid")

DEFAULT_THRESHOLD = 0.70

# Phase 28 G2 — mapper output cache.  Default off, default mapper_version=1,
# default TTL 24h.  All three are overridable via IDP Settings.
_DEFAULT_MAPPER_CACHE_TTL_SECONDS: float = 24 * 60 * 60
_DEFAULT_MAPPER_VERSION: int = 1


def _read_mapper_cache_settings() -> tuple[bool, int, float]:
	"""Return ``(enabled, mapper_version, ttl_seconds)`` from IDP Settings.

	Returns conservative defaults (disabled, v1, 24h) when Frappe is
	unavailable (pure-mode tests) or the Phase 28 fields are missing on
	legacy installs.  Never raises.
	"""

	try:
		import frappe
	except ImportError:
		return False, _DEFAULT_MAPPER_VERSION, _DEFAULT_MAPPER_CACHE_TTL_SECONDS
	try:
		enabled = bool(frappe.db.get_single_value("IDP Settings", "mapper_cache_enabled"))
	except Exception:
		enabled = False
	try:
		version = int(
			frappe.db.get_single_value("IDP Settings", "mapper_version") or _DEFAULT_MAPPER_VERSION
		)
	except Exception:
		version = _DEFAULT_MAPPER_VERSION
	try:
		hours = float(frappe.db.get_single_value("IDP Settings", "mapper_cache_ttl_hours") or 24)
	except Exception:
		hours = 24.0
	return enabled, version, max(hours, 0.0) * 3600.0


def _build_mapper_cache_key(file_sha256: str, target_doctype: str, mapper_version: int) -> str:
	return f"idp:mapper:{file_sha256}:{target_doctype}:{mapper_version}"


def _mapped_to_dict(mapped: MappedDocument) -> dict:
	"""Convert a :class:`MappedDocument` to a JSON-safe dict for caching."""

	if is_dataclass(mapped):
		return asdict(mapped)
	return {
		"doctype": getattr(mapped, "doctype", ""),
		"header": dict(getattr(mapped, "header", {}) or {}),
		"items": list(getattr(mapped, "items", []) or []),
		"taxes": list(getattr(mapped, "taxes", []) or []),
		"unmapped_fields": list(getattr(mapped, "unmapped_fields", []) or []),
		"confidence_scores": dict(getattr(mapped, "confidence_scores", {}) or {}),
		"warnings": list(getattr(mapped, "warnings", []) or []),
		"link_resolutions": dict(getattr(mapped, "link_resolutions", {}) or {}),
		# Phase 29 — provenance pass-through.
		"source_regions": dict(getattr(mapped, "source_regions", {}) or {}),
	}


def _dict_to_mapped(payload: dict) -> MappedDocument:
	"""Inverse of :func:`_mapped_to_dict` — tolerant of missing keys."""

	allowed = {f.name for f in _dc_fields(MappedDocument)}
	kwargs = {k: v for k, v in (payload or {}).items() if k in allowed}
	return MappedDocument(**kwargs)


class HybridFieldMapper:
	"""Combine the rule mapper with an LLM fallback below threshold."""

	def __init__(
		self,
		rule_mapper: Any,
		llm_client: Any,
		*,
		confidence_threshold: float = DEFAULT_THRESHOLD,
		doctype_thresholds: dict[str, float] | None = None,
	) -> None:
		self.rule_mapper = rule_mapper
		self.llm_client = llm_client
		self.confidence_threshold = confidence_threshold
		# Per-DocType overrides (§TE.5).  Populated either explicitly by
		# callers (tests) or implicitly via :meth:`_resolve_threshold`,
		# which consults IDP Extraction Template at map time.
		self.doctype_thresholds: dict[str, float] = dict(doctype_thresholds or {})

	def map_fields(
		self,
		extracted: Any,
		target_doctype: str,
		*,
		company: str | None = None,
		output_language: str = "English",
		industry: str | None = None,
		user: str | None = None,
		source_lang: str | list[str] | tuple[str, ...] | None = None,
		file_sha256: str | None = None,
	) -> MappedDocument:
		# --- 0. Phase 28 G2 — mapper output cache lookup ------------------------
		# Cache state surfaced via the ``mapper_cache_state`` attribute on
		# the returned MappedDocument (and a warning string) so callers can
		# record it on the IDP Document Log row.  When ``file_sha256`` is
		# not supplied the cache is bypassed entirely — small-file callers
		# can opt in by hashing once and passing the digest.
		cache_enabled, mapper_version, mapper_cache_ttl = _read_mapper_cache_settings()
		cache_key: str | None = None
		cache_state = "bypass"
		if cache_enabled and file_sha256:
			cache_key = _build_mapper_cache_key(file_sha256, target_doctype, mapper_version)
			cached = cache_get(cache_key)
			if cached is not None:
				try:
					hit = _dict_to_mapped(cached)
					hit.warnings.append("hybrid: mapper cache hit")
					setattr(hit, "mapper_cache_state", "hit")
					return hit
				except Exception as exc:  # pragma: no cover - defensive
					logger.warning(f"mapper cache decode failed; ignoring hit: {exc}")
			cache_state = "miss"

		# --- 1. Rule-based pass --------------------------------------------------
		rule_kwargs: dict[str, Any] = {"company": company}
		if source_lang:
			rule_kwargs["source_lang"] = source_lang
		try:
			rule_result = self.rule_mapper.map_fields(extracted, target_doctype, **rule_kwargs)
		except TypeError:
			# Backwards compatible with older mappers that don't accept
			# the ``source_lang`` keyword (e.g. tests with stub mappers).
			rule_result = self.rule_mapper.map_fields(extracted, target_doctype, company=company)
		_tag_provenance(rule_result, source="rule")

		# --- 2. Decide whether to invoke the LLM --------------------------------
		threshold = self._resolve_threshold(target_doctype)
		schema_required = _required_fieldnames(target_doctype)
		missing = [f for f in schema_required if f not in rule_result.header]
		avg_conf = _avg_confidence(rule_result)
		if not missing and avg_conf >= threshold:
			rule_result.warnings.append(
				f"hybrid: rule pass sufficient (avg_conf={avg_conf:.2f} >= {threshold:.2f})"
			)
			setattr(rule_result, "mapper_cache_state", cache_state)
			if cache_key is not None:
				try:
					cache_set(cache_key, _mapped_to_dict(rule_result), ttl_seconds=mapper_cache_ttl)
				except Exception as exc:  # pragma: no cover - defensive
					logger.warning(f"mapper cache write failed: {exc}")
			return rule_result

		# --- 3. LLM fallback -----------------------------------------------------
		try:
			llm_payload = self._call_llm(
				extracted,
				target_doctype,
				partial=rule_result,
				output_language=output_language,
				industry=industry,
				user=user,
			)
		except LLMError as exc:
			logger.warning(f"LLM fallback failed; returning rule-only mapping: {exc}")
			rule_result.warnings.append(f"hybrid: llm unavailable ({exc}); rule-only mapping returned")
			# Even when the mapping LLM call fails we still try the
			# narrative translator — it has its own retry / cache and is
			# typically a different prompt that can succeed independently.
			_translate_narrative(
				rule_result,
				source_lang=source_lang,
				output_language=output_language,
				llm_client=self.llm_client,
			)
			# Don't cache LLM-failure paths — they are typically transient
			# (rate limits, network) and we want the next call to retry.
			setattr(rule_result, "mapper_cache_state", cache_state)
			return rule_result

		# --- 4. Merge ------------------------------------------------------------
		merged = self._merge(rule_result, llm_payload)
		merged.warnings.append(
			f"hybrid: llm fallback applied (rule_avg={avg_conf:.2f}, missing_required={len(missing)})"
		)
		# --- 5. Phase 22 — translate narrative fields ---------------------------
		_translate_narrative(
			merged,
			source_lang=source_lang,
			output_language=output_language,
			llm_client=self.llm_client,
		)
		setattr(merged, "mapper_cache_state", cache_state)
		if cache_key is not None:
			try:
				cache_set(cache_key, _mapped_to_dict(merged), ttl_seconds=mapper_cache_ttl)
			except Exception as exc:  # pragma: no cover - defensive
				logger.warning(f"mapper cache write failed: {exc}")
		return merged

	# ------------------------------------------------------------------------
	# internals
	# ------------------------------------------------------------------------

	def _resolve_threshold(self, target_doctype: str) -> float:
		"""Return the LLM-fallback threshold to use for *target_doctype*.

		Precedence (highest first):

		1. Explicit override passed via the ``doctype_thresholds`` ctor arg
		   or set on the instance later (used by tests).
		2. ``IDP Extraction Template.llm_fallback_threshold`` for the
		   first template targeting this DocType (§TE.5).
		3. The instance-wide ``confidence_threshold``.
		"""

		if target_doctype in self.doctype_thresholds:
			return float(self.doctype_thresholds[target_doctype])

		try:
			import frappe  # noqa: F401
		except ImportError:
			return self.confidence_threshold

		try:
			import frappe

			rows = frappe.get_all(
				"IDP Extraction Template",
				filters={"target_doctype": target_doctype},
				fields=["llm_fallback_threshold"],
				order_by="modified desc",
				limit=1,
			)
		except Exception as exc:  # pragma: no cover - defensive
			logger.debug(f"extraction-template threshold lookup skipped: {exc}")
			return self.confidence_threshold

		if rows and rows[0].get("llm_fallback_threshold") is not None:
			value = float(rows[0]["llm_fallback_threshold"])
			# Cache so repeated calls within the request don't re-query
			# the DB.  Templates are edited infrequently; a per-request
			# memo is sufficient.
			self.doctype_thresholds[target_doctype] = value
			return value
		return self.confidence_threshold

	def _call_llm(
		self,
		extracted: Any,
		target_doctype: str,
		*,
		partial: MappedDocument,
		output_language: str,
		industry: str | None,
		user: str | None,
	) -> dict:
		system = build_system_prompt(target_doctype, industry=industry, output_language=output_language)
		user_msg = build_user_message(
			_extracted_to_dict(extracted),
			partial_mapping={"header": partial.header, "items": partial.items},
		)
		response = self.llm_client.chat(
			messages=[
				{"role": "system", "content": system},
				{"role": "user", "content": user_msg},
			],
			tools=[FIELD_MAPPING_TOOL_SCHEMA],
			tool_choice="auto",
			user=user,
		)
		if not response.tool_calls:
			# Some providers return JSON mode content instead of a tool call;
			# accept that path too.
			if response.content:
				import json as _json

				try:
					return _json.loads(response.content)
				except ValueError:
					pass
			raise LLMError("LLM returned no tool call and no parsable JSON content")
		return response.tool_calls[0].arguments or {}

	def _merge(self, rule_result: MappedDocument, llm_payload: dict) -> MappedDocument:
		llm_header = llm_payload.get("header") or {}
		llm_items = llm_payload.get("items") or []
		llm_conf = llm_payload.get("confidence_scores") or {}
		llm_warnings = llm_payload.get("warnings") or []

		merged = MappedDocument(
			doctype=rule_result.doctype,
			header=dict(rule_result.header),
			items=list(rule_result.items),
			unmapped_fields=list(rule_result.unmapped_fields),
			confidence_scores=dict(rule_result.confidence_scores),
			warnings=list(rule_result.warnings),
			link_resolutions=dict(rule_result.link_resolutions),
			# Phase 29 — preserve any provenance the rule mapper captured.
			# LLM-only fields leave the bbox unset.
			source_regions=dict(getattr(rule_result, "source_regions", {}) or {}),
		)

		for fieldname, value in llm_header.items():
			rule_score = _score_of(merged.confidence_scores.get(fieldname))
			llm_score = float(llm_conf.get(fieldname, 0.5) or 0.0)
			# Only overwrite when the LLM is meaningfully more confident OR
			# when the rule mapper produced no value at all.
			if fieldname not in merged.header or llm_score > rule_score:
				merged.header[fieldname] = value
				merged.confidence_scores[fieldname] = {"score": llm_score, "source": "llm"}

		# Items: trust the LLM only when the rule mapper produced none.
		# When the rules already found the row, keep that row and copy
		# the code and unit the rules missed.
		if not merged.items and llm_items:
			merged.items = list(llm_items)
		elif merged.items and llm_items:
			by_name = {
				str(row.get("item_name") or row.get("item_code") or "").strip().lower(): row
				for row in llm_items
				if isinstance(row, dict)
			}
			for rule_row in merged.items:
				if not isinstance(rule_row, dict):
					continue
				label = str(rule_row.get("item_name") or "").strip().lower()
				llm_row = by_name.get(label)
				if not llm_row:
					continue
				for key in ("item_code", "uom"):
					if not rule_row.get(key) and llm_row.get(key):
						rule_row[key] = llm_row[key]

		for w in llm_warnings:
			if isinstance(w, str):
				merged.warnings.append(f"llm: {w}")
		return merged


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tag_provenance(result: MappedDocument, *, source: str) -> None:
	"""Wrap each scalar score in ``{score, source}`` for downstream introspection."""

	for field, score in list(result.confidence_scores.items()):
		if isinstance(score, dict):
			score.setdefault("source", source)
		else:
			result.confidence_scores[field] = {"score": float(score or 0.0), "source": source}


def _score_of(entry: Any) -> float:
	if isinstance(entry, dict):
		return float(entry.get("score", 0.0) or 0.0)
	try:
		return float(entry or 0.0)
	except (TypeError, ValueError):
		return 0.0


def _avg_confidence(result: MappedDocument) -> float:
	scores = [_score_of(v) for v in result.confidence_scores.values()]
	if not scores:
		return 0.0
	return sum(scores) / len(scores)


def _required_fieldnames(target_doctype: str) -> list[str]:
	try:
		from idp.mappers.base import get_doctype_schema

		schema = get_doctype_schema(target_doctype)
	except Exception as exc:
		logger.debug(f"required-field lookup skipped for {target_doctype}: {exc}")
		return []
	return [f["fieldname"] for f in schema.get("fields") or [] if f.get("reqd")]


def _extracted_to_dict(extracted: Any) -> dict:
	"""Render an ExtractionResult-ish object as a JSON-safe dict for prompts."""

	if isinstance(extracted, dict):
		return extracted
	out: dict[str, Any] = {}
	for attr in ("text", "tables", "metadata", "key_values"):
		if hasattr(extracted, attr):
			val = getattr(extracted, attr)
			if val is not None:
				out[attr] = val
	return out or {"text": str(extracted)}


def _translate_narrative(
	mapped: MappedDocument,
	*,
	source_lang: str | list[str] | tuple[str, ...] | None,
	output_language: str,
	llm_client: Any,
) -> None:
	"""Translate narrative header fields in *mapped* in place.

	No-op when:
	* ``source_lang`` is missing, ambiguous (list with >1 entry,
	  ``"auto"``), or already matches ``output_language``.  We can't
	  honestly translate without knowing the source language; the
	  caller should detect a single source first.
	* The translation module isn't importable.
	* The LLM client is None or throws — see :mod:`translation` for the
	  graceful-degradation contract.
	"""

	if not source_lang or not output_language:
		return

	# Collapse list/tuple input to a single code if unambiguous.
	# Multiple distinct codes or ``"auto"`` → skip translation; the
	# rule mapper still benefited from the multilingual keyword merge.
	if isinstance(source_lang, str):
		if source_lang.strip().lower() == "auto":
			return
		single_lang: str | None = source_lang
	else:
		distinct = {str(s).strip().lower() for s in source_lang if s}
		if "auto" in distinct or len(distinct) != 1:
			return
		single_lang = next(iter(distinct))

	try:
		from idp.llm.translation import translate_mapping
		from idp.mappers.keywords_ml import normalize_language
	except Exception as exc:  # pragma: no cover - defensive
		logger.debug(f"translation skipped: import failed ({exc})")
		return

	src = normalize_language(single_lang)
	tgt = normalize_language(output_language)
	if not src or not tgt or src == tgt:
		return

	try:
		swaps = translate_mapping(
			mapped.header,
			source_lang=src,
			target_lang=tgt,
			llm_client=llm_client,
		)
	except Exception as exc:  # pragma: no cover - defensive
		logger.debug(f"translation aborted in hybrid mapper: {exc}")
		return

	if not swaps:
		return
	for fieldname, payload in swaps.items():
		# Mark provenance so the ConfirmationCard UI can flag the
		# translated copy and offer "show original".
		entry = mapped.confidence_scores.get(fieldname)
		if isinstance(entry, dict):
			entry["translated_from"] = src
			entry["translated_to"] = tgt
			entry["original_text"] = payload.get("original")
		else:
			mapped.confidence_scores[fieldname] = {
				"score": _score_of(entry),
				"source": "translation",
				"translated_from": src,
				"translated_to": tgt,
				"original_text": payload.get("original"),
			}
	mapped.warnings.append(f"hybrid: translated {len(swaps)} narrative field(s) {src} → {tgt}")


__all__ = ["HybridFieldMapper"]
