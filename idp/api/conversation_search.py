# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Item / account search for the IDP chat mapping UI (split from conversation.py)."""

from __future__ import annotations


def search_item_candidates(query: str, top_n: int = 10) -> list[dict]:
	query = (query or "").strip()
	if not query:
		return []
	try:
		top_n = max(1, min(int(top_n or 10), 50))
	except (TypeError, ValueError):
		top_n = 10
	from idp.mappers.item_matcher import match_single_item

	result = match_single_item(
		{"item_code": query, "item_name": query},
		match_threshold=0.99,
		floor_threshold=0.3,
		top_n=top_n,
	)
	return [c.to_dict() for c in result.matches]


def search_account_candidates(
	query: str, company: str | None = None, top_n: int = 10
) -> list[dict]:
	query = (query or "").strip()
	if not query:
		return []
	try:
		top_n = max(1, min(int(top_n or 10), 50))
	except (TypeError, ValueError):
		top_n = 10
	from idp.mappers.tax_matcher import match_single_tax

	result = match_single_tax(
		{"account": query},
		company=company,
		match_threshold=0.99,
		floor_threshold=0.3,
		top_n=top_n,
	)
	return [c.to_dict() for c in result.matches]
