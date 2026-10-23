# IDP Scan API

Contract for the TaxMate SPA (and any client). Controllers on **IDP Document Log**
own validation and side effects; these methods are thin, permission-checked entry
points.

Module: `idp.api.scan`

## Methods

### `create_scan(file_url, target_doctype, company, idempotency_key=None)`

Inserts a Scan in `Queued`. The worker runs after commit (`job_id=name`).

| Arg | Type | Notes |
|---|---|---|
| file_url | str | Private File URL; caller must be able to read the File |
| target_doctype | str | Link to DocType |
| company | str | Required; must be in the user's Company User Permissions |
| idempotency_key | str? | Unique per owner; repeat returns the existing row |

Returns: `{name, status}`

### `get_scan(name)`

`get_doc` + `check_permission("read")` → view model.

### `save_edits(name, edits, modified)`

Applies edits in Review. `modified` is required (optimistic lock).

`edits`: list of `{fieldname, edited, ...}` or `{fieldname: value}` map.

Returns: full view model from the reloaded doc.

### `make_draft(name, modified)`

Builds the target document from frozen extraction + edits, inserts as **draft**
with normal permissions, sets `created_*`, status `Saved`. Idempotent.

Returns: `{name, status, created_doctype, created_document, route, view}`

### `cancel_scan(name)`

Sets `cancel_requested`; Queued scans become `Cancelled` immediately.

Returns: `{name, status}`

## View model (`scan_view`)

```
name, status, stage, error_key, error_message, modified,
file_url, target_doctype, company, pipeline_version, file_sha256,
path_taken, ocr_engine, cancel_requested,
fields: [{fieldname, value, extracted, edited, confidence, flags, source_box}],
totals_check, missing_masters, duplicate_candidates,
can_make_draft, blocked_reasons,
created_doctype, created_document
```

The SPA renders this. It does not recompute `can_make_draft` or totals.

## Status machine

`Queued → Reading → Review → Saving → Saved | Failed | Cancelled`

## Error keys

Stable keys on the Scan (`error_key`) or raised exceptions:

| Key / exception | When |
|---|---|
| `TimestampMismatchError` | Stale `modified` on write |
| `PermissionError` | Other owner / company / no File read |
| `ValidationError` | Illegal status, unknown edit field, missing args |
| `cancelled` | User cancel |
| `read_failed` | Worker / extract failure |
| `make_draft_failed` | Target DocType validation (Scan stays Review) |
| `worker_lost` | Watchdog (PR5) |

Legacy: prefer `get_scan` over `idp.api.background.get_job_status`.

## Upload

Use Frappe `upload_file` with `is_private=1`, then `create_scan(file_url=...)`.
