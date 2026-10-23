# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Canonical Scan status machine for IDP Document Log."""

from __future__ import annotations

QUEUED = "Queued"
READING = "Reading"
REVIEW = "Review"
SAVING = "Saving"
SAVED = "Saved"
FAILED = "Failed"
CANCELLED = "Cancelled"

CANONICAL = frozenset({QUEUED, READING, REVIEW, SAVING, SAVED, FAILED, CANCELLED})
LEGACY = frozenset(
	{"Uploaded", "Extracting", "Extracted", "Creating", "Created", "Updated", "Deleted", "Ready"}
)

TRANSITIONS: dict[str, frozenset[str]] = {
	QUEUED: frozenset({READING, FAILED, CANCELLED}),
	READING: frozenset({REVIEW, FAILED, CANCELLED}),
	REVIEW: frozenset({SAVING, FAILED, CANCELLED}),
	SAVING: frozenset({SAVED, REVIEW, FAILED}),
	SAVED: frozenset(),
	FAILED: frozenset(),
	CANCELLED: frozenset(),
	"Uploaded": frozenset({"Extracting", READING, FAILED, CANCELLED}),
	"Extracting": frozenset({"Extracted", "Ready", REVIEW, FAILED, CANCELLED}),
	"Extracted": frozenset({"Creating", SAVING, "Created", SAVED, FAILED}),
	"Ready": frozenset({SAVING, REVIEW, FAILED, CANCELLED, "Creating"}),
	"Creating": frozenset({"Created", SAVED, "Ready", REVIEW, FAILED}),
	"Created": frozenset(),
	"Updated": frozenset(),
	"Deleted": frozenset(),
}

FROZEN_FIELDS = (
	"extraction_data",
	"ocr_text",
	"field_confidence_json",
	"pipeline_version",
	"file_sha256",
	"ocr_engine",
	"path_taken",
)

REVIEW_STATUSES = frozenset({REVIEW, "Ready", "Extracted"})

STATUS_BACKFILL = {
	"Uploaded": QUEUED,
	"Extracting": READING,
	"Ready": REVIEW,
	"Extracted": REVIEW,
	"Creating": SAVING,
	"Created": SAVED,
	"Updated": SAVED,
	"Deleted": FAILED,
}
