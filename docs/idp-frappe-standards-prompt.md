# IDP — Frappe-Standard Backend Brief

Restructure `apps/idp` so it follows Frappe conventions: **state and business rules live
in DocTypes and their controllers; whitelisted methods are thin, permission-checked entry
points.** TaxMate's React SPA is a UI only. It sends requests and renders responses; it
does not orchestrate the pipeline, re-validate, or hold business rules.

The bar: every request is validated, authorised, idempotent where it writes, and answered
with a response that is exactly what the database holds. Part 0 was verified by reading
the code on 2026-10-17. Start from it.

Companion brief: `apps/taxmate/docs/idp-scan-hardening-prompt.md` (pipeline accuracy,
capture, review UX). This brief owns the backend shape; that one owns the scan behaviour.
Where they overlap (the Scan record, save-from-scan), use the design here.

---

## Skills

| Skill set | Skill | Use it for |
|---|---|---|
| Frappe | frappe-doctype-development | DocType design, controllers, naming, child tables, `track_changes`, links |
| Frappe | frappe-api-development | Whitelisted methods, typed args, error responses, `frappe.enqueue`, realtime |
| Frappe | frappe-enterprise-patterns | Permission hooks, idempotency, audit, background jobs, retention |
| Frappe | frappe-testing | `FrappeTestCase`, fixtures, permission tests |
| Frappe | frappe semgrep rules | Must pass on every PR |
| Vercel | vercel-react-best-practices | Only for the TaxMate client that consumes the new API |
| Anthropic | doctype-development, api-development, testing (if installed) | Cross-check the Frappe skills |
| No AI slop | no-ai-slop | Docstrings, comments, error messages, commit messages, docs |
| gstack | `/plan-eng-review`, `/cso`, `/review`, `/qa`, `/investigate`, `/benchmark` | Plan review, security, diff review, live QA, root-causing, latency |

---

## Part 0 — Ground truth

**Work in progress.** `apps/idp` has uncommitted changes (OCR preprocess, call counter,
eval fixtures, new fields on `IDP Document Log`: `file_sha256`, `pipeline_version`,
`ocr_text`, `field_confidence_json`, `stage_timings_json`, `cancel_requested`, `error_key`,
`path_taken`). Run `git status` first. Build on those changes; do not revert or duplicate them.

**Where logic lives today.**
- 18 DocTypes. Controllers are thin (`IDP Document Log` 52 lines, mostly a token metric).
- Business logic sits in `idp/api/*` and `idp/tools/*`. `api/conversation.py` is 2,906
  lines with 24 whitelisted methods.
- 49 whitelisted methods across `api/`. 47 non-test sites use `ignore_permissions=True`,
  `frappe.db.commit()`, or raw `frappe.db.sql`. `api/conversation.py` alone has 9 explicit
  commits and 4 `ignore_permissions` inserts.
- TaxMate (`taxmate/api/idp_desk.py`, `taxmate/idp/*`) calls IDP internals directly
  (`idp.extractors`, `idp.mappers`, `idp.llm.client`, `idp.tools.*`) and re-orchestrates the
  pipeline: language detect, model read, salvage, master binding, validation refresh, save.
  The save path re-extracts instead of using what the user reviewed.

**Permissions (from DocType JSON and hooks).**

| DocType | IDP User | Risk |
|---|---|---|
| IDP Document Log | read + write, not owner-scoped, no query condition | Any IDP User reads and edits every user's extractions, OCR text, and `created_document` |
| IDP Extraction Correction | read + write, all rows | Any user can poison few-shot examples used for everyone |
| IDP Extraction Template | read + write, all rows | Any user changes extraction rules for everyone |
| IDP Message | read + write, query condition only | Check `has_permission` covers direct gets |
| IDP Conversation, Batch Job | owner-scoped (`if_owner`) + query condition | OK; keep |

- `api/background.get_job_status(job_id)` reads `IDP Document Log` by name with no
  permission check. Any logged-in user can poll any job.
- No company scoping on any IDP DocType. TaxMate scopes by company via User Permission;
  IDP must respect it.

**How Raven does it.** Controllers own validation and side effects; `after_insert` /
`on_update` enqueue heavy work with `enqueue_after_commit=True`; realtime goes to a doc
room. `apps/raven/AGENTS.md`: small functions, files 100–300 lines, no explicit commits,
never skip permission checks, semgrep must pass.

---

## Part 1 — Target design

### 1.1 The Scan is a DocType, and it is the source of truth
Use `IDP Document Log` (rename the label to "IDP Scan" if that reads better; keep the
name to avoid a migration unless it is cheap). One row per read.

- **Status** is a `Select` with an explicit state machine:
  `Queued → Reading → Review → Saving → Saved | Failed | Cancelled`.
  The controller's `validate` rejects illegal transitions. Nothing else sets status.
- **Frozen read**: `extracted_json`, `ocr_text`, `field_confidence_json`, `pipeline_version`,
  `file_sha256`, `ocr_engine`, `path_taken` are written once by the worker and are read-only
  afterwards (`set_only_once` or controller check).
- **User edits** live in their own field (`edits_json`) or a child table
  (`IDP Scan Field`: fieldname, extracted, edited, confidence, flags). Prefer the child
  table if the review UI edits per field; it gives version history through `track_changes`.
- **Result**: `created_doctype` + `created_document` (Dynamic Link) set once on Saved.
- **Ownership**: `owner`, `company` (Link, required), `target_doctype` (Link).
- **Idempotency**: `idempotency_key` (unique per owner) for create; same key returns the
  existing row.

### 1.2 Controllers own the rules
`IDPDocumentLog` (or `IDPScan`) controller methods, each small:
- `validate`: state transitions, frozen fields, company/target consistency, file access.
- `after_insert`: enqueue the read (`enqueue_after_commit=True`, named queue, explicit
  timeout, `job_id=self.name` so duplicates are rejected by RQ).
- `run_read()`: called by the worker. Single OCR pass, map, validate, write the frozen
  fields, move to Review. Publishes stage events to the doc room.
- `cancel()`: sets `cancel_requested`; the worker checks it between stages.
- `apply_edits(edits)`: only in Review; validates field names against the target meta.
- `make_draft()`: only in Review. Builds the target document from frozen read + edits,
  inserts it **as draft with normal permissions**, sets `created_document`, moves to Saved.
  One transaction. Calling it twice returns the same draft.
- UAE rules (TRN binding, emirate, VAT category) are **not** in IDP. Expose hook points
  (`idp_after_extract`, `idp_before_make_draft` in `hooks.py`, resolved with
  `frappe.get_hooks`) and move TaxMate's logic into TaxMate hook handlers.

### 1.3 Thin whitelisted API (the only contract the SPA uses)
One module, `idp/api/scan.py`, under ~150 lines:

| Method | Does | Returns |
|---|---|---|
| `create_scan(file_url, target_doctype, company, idempotency_key)` | Insert Scan (permission-checked) | `{name, status}` |
| `get_scan(name)` | `frappe.get_doc` + `check_permission("read")` | Scan view model (below) |
| `save_edits(name, edits, modified)` | `apply_edits`, optimistic lock on `modified` | Scan view model |
| `make_draft(name, modified)` | `make_draft` | `{name, status, created_doctype, created_document, route}` |
| `cancel_scan(name)` | `cancel` | `{name, status}` |

Rules for every method:
- Typed arguments (Frappe v16 type-hint validation). Reject unknown fields.
- `frappe.get_doc(...)` then `doc.check_permission(ptype)`. No `frappe.db.get_value` on
  user-addressable names. No `ignore_permissions` in this module.
- No `frappe.db.commit()`; the request commits. Workers rely on the job's commit.
- Errors raise Frappe exceptions with a stable `error_key` (e.g. `frappe.ValidationError`
  subclass carrying `error_key`), never a bare string the client has to parse.
- The response is built from the saved doc after save, not from local variables, so it
  matches the database.
- `modified` is required on writes; stale `modified` ⇒ `TimestampMismatchError` ⇒ client
  reloads.

**View model.** One function, `scan_view(doc)`, builds the response: status, stage,
error_key, header fields and lines with `{value, edited, confidence, flags, source_box}`,
totals check, missing masters, duplicate candidates, `can_make_draft` + `blocked_reasons`.
The SPA renders this. It does not compute `can_make_draft` or totals itself.

Upload stays on Frappe's standard `upload_file` (`is_private=1`, attached to the Scan
after insert) or a thin wrapper that adds magic-byte sniffing (`taxmate/idp/upload_prep.py`
logic moves into IDP).

### 1.4 Permissions
- `IDP Document Log`: `if_owner` for IDP User, `permission_query_conditions` +
  `has_permission` scoped to owner and the user's permitted companies (User Permission on
  Company). Read-only for IDP User except through the API methods above.
- `IDP Extraction Template`, `IDP Extraction Correction`: write limited to a new
  `IDP Manager` role. Corrections from users are recorded per user and only promoted to
  shared few-shot examples by IDP Manager or a reviewed job.
- `get_job_status`: remove or make it call `get_scan`.
- Realtime events go to the Scan doc room; confirm a user cannot subscribe to another
  user's room.
- File access: the user must be able to read the File; checked in `validate`, not only in
  the API.

### 1.5 Remove or shrink
- TaxMate's `idp_desk.py` becomes either nothing (SPA calls `idp.api.scan.*`) or a
  ≤ 50-line adapter for TaxMate-specific labels. All pipeline calls into IDP internals go.
- Explicit commits and `ignore_permissions` in `api/conversation.py`: each one removed or
  justified in a one-line comment and covered by a test. Split the module along its
  responsibilities (conversation, card, item search, bulk actions) into files < 300 lines.
  Do not change the IDP chat UI's behaviour while doing it.
- Raw SQL: replace with `frappe.qb` / `frappe.get_all` where possible; keep any that
  remains parameterised and permission-filtered.

### 1.6 Robustness
- Worker crash or timeout ⇒ a scheduler job (every 5 min) moves Scans stuck in Reading
  past `timeout + grace` to Failed with `error_key=worker_lost`.
- LLM provider error ⇒ rule-mapper result, lower confidence, `path_taken=rules`.
- `make_draft` failure (validation in the target doctype) ⇒ Scan stays in Review, response
  carries the target's validation message mapped to fields where possible.
- Rate limit `create_scan` per user (Frappe `rate_limit` decorator) to protect workers.
- Every state change logged via `track_changes`; no separate ad-hoc audit table for it.

---

## Part 2 — Tests

- Controller: every legal and illegal state transition; frozen fields cannot change;
  `make_draft` twice ⇒ one draft; draft equals frozen read + edits field by field.
- API: each method with no permission, other owner, other company, wrong status, stale
  `modified`, unknown field in edits, missing args. Assert error_key, not message text.
- Response accuracy: for each API write, reload the doc and assert the response equals
  `scan_view(reloaded)`.
- Concurrency: two `make_draft` calls in parallel (two DB connections) ⇒ one draft.
- Worker: crash mid-read ⇒ watchdog marks Failed; cancel between stages ⇒ Cancelled.
- Hooks: TaxMate's `idp_after_extract` handler runs and its failure does not fail the read
  (logged, flagged).
- Migration: existing Document Log rows still open; patch backfills `company` and status.
- Semgrep + ruff clean. Existing IDP tests still pass.
- gstack: `/plan-eng-review` on the plan, `/cso` on 1.3–1.4, `/review` per PR, `/qa` on
  the TaxMate scan flow (web + Android) and the IDP chat at `/idp`, `/benchmark` on
  `create_scan → Review`.

---

## Part 3 — Rules

- Frappe and ERPNext core untouched.
- Patches for every schema or permission change (`patches.txt`), idempotent.
- No behaviour change for the IDP chat UI unless listed in the plan.
- Comments say why. Docstrings one line unless the contract needs more. No filler.
- PR order: (1) permissions + `get_job_status` fix (security first), (2) Scan state machine
  + frozen fields + controller methods, (3) `idp.api.scan` + view model, (4) TaxMate
  hooks + switch the SPA to the new API + delete `idp_desk` pipeline code, (5) watchdog +
  rate limit, (6) `api/conversation.py` split and commit/permission cleanup.
  Each PR: tests, before/after list of whitelisted methods and permission bypasses.

## Deliverables

1. `tasks/idp-frappe-standards-plan.md`, reviewed with `/plan-eng-review` before code.
2. The PRs above.
3. `docs/API.md` in IDP: the five scan methods, args, view model, error keys.
4. Counts before/after: whitelisted methods, `ignore_permissions`, explicit commits, raw SQL.
5. What you did not do and why.
