<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->

<template>
	<div
		class="idp-confirmation-card rounded-lg border border-amber-300 bg-amber-50 p-4 shadow-sm dark:border-amber-700 dark:bg-amber-950"
		role="region"
		:aria-label="`Confirmation card: ${card.title || card.doctype || 'document'}`"
		tabindex="0"
		@keydown="onCardKeydown"
	>
		<!-- Phase 31 G17 — bulk-action header.  Renders once, above the
         first card of a multi-card turn (≥3 cards). -->
		<div
			v-if="showBulkHeader"
			class="mb-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-400 bg-amber-100 px-3 py-2 text-[11px] text-amber-900 dark:border-amber-700 dark:bg-amber-900 dark:text-amber-100"
		>
			<div class="flex items-center gap-2">
				<span aria-hidden="true">📋</span>
				<span class="font-medium">
					{{ turnCardSiblings.length }} confirmation cards in this turn
				</span>
			</div>
			<div class="flex flex-wrap items-center gap-1">
				<button
					type="button"
					class="rounded bg-emerald-600 px-2 py-0.5 text-white hover:bg-emerald-700 disabled:opacity-50"
					:disabled="bulkTurnBusy"
					@click="onTurnConfirmOK"
				>
					Confirm OK only
				</button>
				<button
					type="button"
					class="rounded bg-amber-600 px-2 py-0.5 text-white hover:bg-amber-700 disabled:opacity-50"
					:disabled="bulkTurnBusy"
					@click="onTurnConfirmAll"
				>
					Confirm all
				</button>
				<button
					type="button"
					class="rounded border border-amber-300 bg-white px-2 py-0.5 text-amber-900 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-700 dark:bg-gray-900 dark:text-amber-100"
					:disabled="bulkTurnBusy"
					@click="onTurnReviewWarnings"
				>
					Review warnings
				</button>
				<span v-if="bulkTurnResult" class="ml-1 text-[10px]">
					{{ bulkTurnResult }}
				</span>
			</div>
		</div>

		<div class="mb-3 flex items-start justify-between gap-3">
			<div>
				<div
					class="text-xs font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-200"
				>
					Confirmation required
				</div>
				<div class="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">
					{{ card.title || `Create ${card.doctype || "document"}` }}
				</div>
				<div v-if="card.subtitle" class="text-xs text-gray-600 dark:text-gray-400">
					{{ card.subtitle }}
				</div>
			</div>
			<div class="flex items-center gap-2">
				<span
					v-if="card.submitted"
					class="rounded bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white dark:bg-emerald-500 dark:text-white"
				>
					Submitted
				</span>
				<span
					v-else-if="card.draft_saved"
					class="rounded bg-gray-700 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white dark:bg-gray-200 dark:text-gray-900"
				>
					Draft Saved
				</span>
				<span
					class="rounded bg-amber-200 px-2 py-0.5 text-[10px] font-mono uppercase text-amber-900 dark:bg-amber-800 dark:text-amber-100"
				>
					v{{ card.version || "?" }}
				</span>
			</div>
		</div>

		<!-- Header fields -->
		<div v-if="headerFields.length" class="mb-4 grid gap-3 sm:grid-cols-2">
			<div v-for="field in headerFields" :key="field.fieldname">
				<label
					class="flex items-center gap-1.5 text-[11px] font-medium text-gray-600 dark:text-gray-400"
				>
					<!-- Phase 29 — header fields no longer render confidence dots
               (kept on items/taxes rows only). -->
					<button
						v-if="field.source_region"
						type="button"
						class="cursor-pointer text-left hover:underline focus:underline focus:outline-none"
						:title="`Open source on page ${field.source_region.page ?? '?'}`"
						@click="onFocusSource(field)"
					>
						{{ formatLabel(field) }}
					</button>
					<span v-else>{{ formatLabel(field) }}</span>
					<!-- Phase 31 G16 — per-field re-extract.  Asks the backend to
               re-pull this single field from the source attachment and
               patch it onto the persisted card.  Hidden when the card
               is read-only (submitted / no actions). -->
					<button
						v-if="reExtractEnabled && !card.submitted && actions.length"
						type="button"
						class="ml-auto rounded p-0.5 text-gray-400 hover:bg-amber-100 hover:text-amber-700 disabled:opacity-50 dark:hover:bg-amber-900 dark:hover:text-amber-200"
						:title="`Re-extract ${formatLabel(field)} from source`"
						:aria-label="`Re-extract ${formatLabel(field)} from source`"
						:disabled="!!reExtractBusy[field.fieldname]"
						@click="onReExtract(field)"
					>
						<span v-if="reExtractBusy[field.fieldname]" aria-hidden="true">⏳</span>
						<span v-else aria-hidden="true">↻</span>
					</button>
				</label>
				<input
					v-if="!editing || field.editable === false"
					class="mt-1 w-full rounded border border-gray-200 bg-white px-2 py-1 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
					:value="field.value ?? ''"
					disabled
				/>
				<input
					v-else
					v-model="edits.header[field.fieldname]"
					class="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm focus:border-amber-500 focus:outline-none dark:border-gray-700 dark:bg-gray-900"
				/>
			</div>
		</div>

		<!-- Items / generic child table (Phase 24 §24.0, Phase 25 §25.1) -->
		<div v-if="hasItemsBlock" class="mb-4">
			<div
				class="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-gray-700 dark:text-gray-300"
			>
				<span>
					{{ childTableTitle }}
					<span class="text-gray-500">
						({{ itemsTotal }} row{{ itemsTotal === 1 ? "" : "s" }})
					</span>
					<span
						v-if="sourcePages.length"
						class="ml-2 text-[10px] text-gray-500"
						:title="`Extracted from PDF page(s): ${sourcePages.join(', ')}`"
					>
						pages {{ sourcePages.join(", ") }}
					</span>
				</span>
				<div v-if="itemsTotal > pageSize" class="flex items-center gap-2">
					<button
						class="rounded border border-gray-300 px-2 py-0.5 text-[11px] disabled:opacity-50 dark:border-gray-700"
						:disabled="page <= 1 || itemsLoading"
						@click="loadPage(page - 1)"
					>
						Prev
					</button>
					<span class="text-[11px] text-gray-500">
						Page {{ page }} / {{ totalPages }}
					</span>
					<button
						class="rounded border border-gray-300 px-2 py-0.5 text-[11px] disabled:opacity-50 dark:border-gray-700"
						:disabled="page >= totalPages || itemsLoading"
						@click="loadPage(page + 1)"
					>
						Next
					</button>
				</div>
			</div>

			<!-- Phase 25 §25.4 — mass-edit toolbar for item tables only. -->
			<div
				v-if="isItemTable && itemsTotal > 1"
				class="mb-2 flex flex-wrap items-center gap-2 rounded bg-amber-100/50 px-2 py-1 text-[11px] text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
			>
				<button
					class="rounded bg-white px-2 py-0.5 text-[11px] text-gray-700 shadow-sm hover:bg-gray-100 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
					:disabled="bulkBusy"
					@click="onBulkMatch"
				>
					{{ bulkBusy ? "Working…" : "Auto-match all" }}
				</button>
				<button
					class="rounded bg-white px-2 py-0.5 text-[11px] text-gray-700 shadow-sm hover:bg-gray-100 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
					:disabled="bulkBusy"
					@click="onBulkAccept"
				>
					Accept all suggestions
				</button>
				<span class="ml-1 inline-flex items-center gap-1">
					<span class="text-gray-600 dark:text-gray-300">UOM →</span>
					<input
						v-model="bulkUom"
						type="text"
						placeholder="e.g. Nos"
						class="w-20 rounded border border-gray-300 bg-white px-1 py-0.5 text-[11px] dark:border-gray-700 dark:bg-gray-900"
					/>
					<button
						class="rounded bg-white px-2 py-0.5 text-[11px] text-gray-700 shadow-sm hover:bg-gray-100 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
						:disabled="bulkBusy || !bulkUom.trim()"
						@click="onApplyUomToAll"
					>
						Apply to all
					</button>
				</span>
				<span
					v-if="bulkResult"
					class="ml-auto text-[10px] text-amber-900 dark:text-amber-200"
				>
					{{ bulkResult }}
				</span>
			</div>

			<div
				v-if="!displayItems.length"
				class="rounded border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-[11px] italic text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
			>
				No items extracted
			</div>
			<ItemMappingTable
				v-else-if="isItemTable"
				:rows="displayItems"
				:editing="editing"
				@edit="onItemEdit"
				@edit-stock="onItemStockEdit"
			/>
			<GenericChildTable
				v-else
				:rows="displayItems"
				:schema="childRowSchema"
				:editing="editing"
				@edit-cell="onGenericCellEdit"
			/>
		</div>

		<!-- Taxes table (Phase 24 §24.4) -->
		<div v-if="hasTaxesBlock" class="mb-4">
			<div class="mb-1 text-xs font-medium text-gray-700 dark:text-gray-300">Taxes</div>
			<div
				v-if="!taxRows.length"
				class="rounded border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-[11px] italic text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
			>
				No taxes extracted
			</div>
			<TaxMappingTable
				v-else
				:rows="taxRows"
				:editing="editing"
				:company="card.company || null"
				@edit="onTaxEdit"
			/>
		</div>

		<!-- Warnings -->
		<div
			v-if="warningList.length"
			class="mb-4 rounded bg-yellow-100 p-2 text-xs text-yellow-900 dark:bg-yellow-900 dark:text-yellow-100"
		>
			<div class="mb-1 font-semibold">Warnings</div>
			<ul class="list-inside list-disc space-y-0.5">
				<li v-for="(w, idx) in warningList" :key="idx">{{ w }}</li>
			</ul>
		</div>

		<!-- Revalidation warnings (after edits / submit) -->
		<div
			v-if="revalidationWarnings.length"
			class="mb-4 rounded bg-orange-100 p-2 text-xs text-orange-900 dark:bg-orange-900 dark:text-orange-100"
		>
			<div class="mb-1 font-semibold">Revalidation</div>
			<ul class="list-inside list-disc space-y-0.5">
				<li v-for="(w, idx) in revalidationWarnings" :key="idx">{{ w }}</li>
			</ul>
		</div>

		<!-- Action buttons.  Phase 33 — sticky to the viewport bottom on
         mobile (respecting safe-area-inset-bottom) so the primary
         actions are always thumb-reachable on phones. -->
		<div
			v-if="actions.length"
			class="idp-card-actions flex flex-wrap gap-2"
			role="group"
			aria-label="Confirmation actions"
		>
			<button
				v-for="action in actions"
				:key="action.id"
				:disabled="busy"
				class="rounded px-3 py-1.5 text-xs font-medium disabled:opacity-50"
				:class="actionStyle(action)"
				:data-action-id="action.id"
				@click="invoke(action.id)"
			>
				{{ action.label || action.id }}
			</button>
		</div>
		<div v-else-if="card.submitted && card.created_doc">
			<div
				class="rounded bg-emerald-50 px-3 py-2 text-[11px] text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
			>
				Submitted as
				<a
					v-if="card.created_doc.url"
					:href="card.created_doc.url"
					target="_blank"
					rel="noopener"
					class="underline"
				>
					{{ card.created_doc.doctype }} {{ card.created_doc.name }}
				</a>
				<span v-else>{{ card.created_doc.doctype }} {{ card.created_doc.name }}</span
				>.
			</div>
			<UndoBanner :message="props.message" :card="card" @undone="onUndone" />
		</div>
		<div v-else-if="card.draft_saved && card.created_doc">
			<div
				class="rounded bg-gray-100 px-3 py-2 text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200"
			>
				Saved as draft
				<a
					v-if="card.created_doc.url"
					:href="card.created_doc.url"
					target="_blank"
					rel="noopener"
					class="underline"
				>
					{{ card.created_doc.doctype }} {{ card.created_doc.name }}
				</a>
				<span v-else>{{ card.created_doc.doctype }} {{ card.created_doc.name }}</span
				>.
			</div>
			<UndoBanner :message="props.message" :card="card" @undone="onUndone" />
		</div>
		<div
			v-else-if="card.draft_saved"
			class="rounded bg-gray-100 px-3 py-2 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300"
		>
			This proposal is parked as a draft. Re-open it to submit later.
		</div>

		<div v-if="lastError" class="mt-2 text-xs text-red-700 dark:text-red-400">
			{{ lastError }}
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useAgent } from "@/composables/useAgent";
import {
	getCardItemsPage,
	bulkMatchItems,
	bulkAcceptSuggestions,
	applyToAllRows,
	reExtractField,
} from "@/utils/api";
import { useConversationStore } from "@/stores/conversation";
import { useSettings } from "@/composables/useSettings";
import { confirm as confirmDialog } from "@/composables/useConfirm";
import ItemMappingTable from "./ItemMappingTable.vue";
import TaxMappingTable from "./TaxMappingTable.vue";
import GenericChildTable from "./GenericChildTable.vue";
import ConfidenceDot from "./ConfidenceDot.vue";
import UndoBanner from "./UndoBanner.vue";

const props = defineProps({
	message: { type: Object, required: true },
});
const emit = defineEmits(["confirmed", "focus-source"]);

const store = useConversationStore();
const { confirm, busy: agentBusy } = useAgent();
const { settings } = useSettings();
const clerkSurface = computed(() => !!settings.value?.clerk_mode);

// Phase 31 G16 — gate the per-field re-extract button.
const reExtractEnabled = computed(() => {
	const v = settings.value?.enable_bulk_actions;
	// ``enable_bulk_actions`` covers both bulk and re-extract per the
	// roadmap §31.4 / §31.5 acceptance.  Default on.
	if (v == null) return true;
	return !!Number(v);
});

// Phase 31 G17 — bulk-action header surfaces when 3+ ConfirmationCard
// messages appear consecutively in the same assistant turn.  We
// approximate "same turn" as "consecutive assistant card messages,
// uninterrupted by a user message".
const turnCardSiblings = computed(() => {
	const all = store.visibleMessages || [];
	const selfName = props.message.name;
	const idx = all.findIndex((m) => m.name === selfName);
	if (idx < 0) return [];
	// Walk backwards to the nearest user message.
	let start = idx;
	for (let i = idx - 1; i >= 0; i -= 1) {
		if (all[i].role === "user") break;
		start = i;
	}
	// Walk forwards to the next user message.
	let end = idx;
	for (let i = idx + 1; i < all.length; i += 1) {
		if (all[i].role === "user") break;
		end = i;
	}
	const out = [];
	for (let i = start; i <= end; i += 1) {
		const m = all[i];
		if (m.role !== "assistant") continue;
		if (!m.rendered_card_payload) continue;
		out.push(m);
	}
	return out;
});

const showBulkHeader = computed(() => {
	if (clerkSurface.value) return false;
	if (!reExtractEnabled.value) return false;
	if (!turnCardSiblings.value.length) return false;
	if (turnCardSiblings.value.length < 3) return false;
	// Only render the banner on the first sibling so we don't repeat it
	// above every card.
	return turnCardSiblings.value[0]?.name === props.message.name;
});

const bulkTurnBusy = ref(false);
const bulkTurnResult = ref("");

// Per-field re-extract spinner state.  Keyed by fieldname so multiple
// rows can spin independently.
const reExtractBusy = ref({});

const card = computed(() => parseJSON(props.message.rendered_card_payload) || {});

// Phase 29 — global toggle for the confidence-dot UI.  Defaults to on
// (matching ``IDP Settings.show_confidence_dots`` default) and can be
// flipped to fall back to numeric percentages without redeploying.
const showConfidenceDots = computed(() => {
	const s = store.settings?.show_confidence_dots;
	return s == null ? true : !!Number(s);
});

// Phase 29 — bubble the click-to-source intent up to ChatView so the
// right-hand PdfPreview panel can render the highlighted region.
function onFocusSource(field) {
	if (!field || !field.source_region) return;
	emit("focus-source", {
		file_id: card.value?.file_id || null,
		field: field.fieldname,
		page: field.source_region.page,
		bbox: field.source_region.bbox,
	});
}

// Hide only the noisy mirror fields the user explicitly called out:
// party_name duplicates supplier/customer, conversion_rate is constant
// when currency==company currency, and base_total / base_net_total /
// base_grand_total mirror the totals already shown alongside the card.
// We deliberately keep `total`, `net_total`, `grand_total`, etc. so
// the user can review the headline numbers without scrolling.
const HIDDEN_HEADER_FIELDS = new Set([
	"supplier_name",
	"customer_name",
	"party_name",
	"conversion_rate",
	"plc_conversion_rate",
	"base_total",
	"base_net_total",
	"base_grand_total",
	"base_taxes_and_charges",
	"base_total_taxes_and_charges",
	"base_rounded_total",
	"base_rounding_adjustment",
	"base_in_words",
]);

function isHiddenHeaderField(fieldname) {
	if (!fieldname) return true;
	return HIDDEN_HEADER_FIELDS.has(fieldname);
}

const headerFields = computed(() => {
	const h = card.value.header;
	if (!Array.isArray(h)) return [];
	return h.filter((f) => f && typeof f === "object" && !isHiddenHeaderField(f.fieldname));
});

// Render a fieldname like ``posting_date`` as ``Posting Date``.  Uses
// the server-supplied ``label`` when it isn't just the fieldname echoed
// back, otherwise falls back to title-casing the snake_case field.
function formatLabel(field) {
	const fieldname = field?.fieldname || "";
	const label = field?.label || "";
	if (label && label !== fieldname) return label;
	if (!fieldname) return "";
	return fieldname
		.split(/[_\s]+/)
		.filter(Boolean)
		.map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
		.join(" ");
}
const itemsBlock = computed(() => card.value.items || {});
const itemsTotal = computed(
	() => itemsBlock.value.total || (itemsBlock.value.rows || []).length || 0,
);
// Show items section whenever the card carries an items block, even when
// the rows array is empty — keeps layout stable with an explicit
// placeholder instead of silently collapsing.
const hasItemsBlock = computed(() => !!card.value.items);
const pageSize = computed(() => itemsBlock.value.page_size || 10);
const totalPages = computed(() => Math.max(1, Math.ceil(itemsTotal.value / pageSize.value)));
const page = ref(1);
const itemsLoading = ref(false);
const fetchedRows = ref(null);

// Phase 25 §25.1 — child-table routing.  Cards built before Phase 25
// don't carry ``child_table_name`` (default to ``items``) so the
// existing ItemMappingTable keeps rendering for legacy messages.
const childTableName = computed(() => card.value.child_table_name || "items");
const isItemTable = computed(() => childTableName.value === "items");
const childRowSchema = computed(() => {
	const s = card.value.child_row_schema;
	return Array.isArray(s) ? s : [];
});
const childTableTitle = computed(() => {
	const name = childTableName.value || "items";
	return name
		.split(/[_\s]+/)
		.filter(Boolean)
		.map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
		.join(" ");
});

// Phase 25 §25.2 — multi-page source breadcrumb.
const sourcePages = computed(() => {
	const sp = card.value.source_pages;
	return Array.isArray(sp) ? sp.filter((n) => Number.isFinite(n) && n > 0) : [];
});

const displayItems = computed(() => {
	if (page.value === 1 || !fetchedRows.value) {
		return itemsBlock.value.rows || [];
	}
	return fetchedRows.value;
});

const taxRows = computed(() => {
	const t = card.value.taxes;
	if (!t || !Array.isArray(t.rows)) return [];
	return t.rows.filter((r) => r && typeof r === "object");
});
// Show taxes section whenever the card declares a taxes block (even when
// the rows array is empty) so the user sees an explicit placeholder.
const hasTaxesBlock = computed(() => !!card.value.taxes);

const warningList = computed(() => {
	const w = card.value.warnings;
	if (!Array.isArray(w)) return [];
	return w.map((x) => (typeof x === "string" ? x : x?.message)).filter(Boolean);
});

const actions = computed(() => {
	const a = card.value.actions;
	if (!Array.isArray(a)) {
		return [];
	}
	let list = a.filter((x) => x && x.id);
	if (clerkSurface.value) {
		list = list
			.filter((x) => x.id !== "submit")
			.map((x) => {
				if (x.id === "save_draft") {
					return { ...x, label: "Create draft invoice", primary: true };
				}
				if (x.id === "cancel") {
					return { ...x, label: "Discard" };
				}
				return x;
			});
	}
	return list;
});

const editing = ref(false);
const edits = ref({
	header: {},
	item_mappings: {}, // {row_index: erpnext_item}
	account_mappings: {}, // {row_index: erpnext_account}
	item_stock_overrides: {}, // {row_index: bool}
	// Phase 25 §25.1 — per-row generic cell edits (any child-table fieldname).
	// Shape: ``{row_index: {fieldname: value, ...}}``
	row_edits: {},
});
const lastError = ref(null);
const revalidationWarnings = ref([]);
const busy = computed(() => agentBusy.value);

// Phase 25 §25.4 — bulk toolbar state.
const bulkBusy = ref(false);
const bulkUom = ref("");
const bulkResult = ref("");

// Phase 33 — Keyboard nav on the card itself.
//
//   * Enter on the card root (when no edit field has focus) toggles
//     Edit mode so a keyboard user can correct extractions without
//     reaching for a mouse.
//   * Esc cancels an open Edit session (matches the existing Cancel
//     action button semantics).
//   * Cmd/Ctrl+Enter triggers the primary action (submit if present,
//     otherwise save_draft, otherwise the first available action).
function preferredAction() {
	const ids = actions.value.map((a) => a.id);
	if (ids.includes("submit")) return "submit";
	if (ids.includes("save_draft")) return "save_draft";
	return ids[0];
}

function onCardKeydown(e) {
	// Only act on keys that originated from the card root, not from a
	// child input/textarea where the user is typing.  We still want
	// Cmd/Ctrl+Enter to work everywhere though.
	const tag = (e.target?.tagName || "").toLowerCase();
	const isField = tag === "input" || tag === "textarea" || tag === "select";

	if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
		const id = preferredAction();
		if (!id || busy.value) return;
		e.preventDefault();
		invoke(id);
		return;
	}

	if (e.key === "Escape") {
		if (editing.value) {
			e.preventDefault();
			invoke("cancel");
		}
		return;
	}

	if (e.key === "Enter" && !isField && !e.shiftKey) {
		// Enter on the card root toggles Edit mode when an Edit action is
		// available.  Ignored while a child field is focused so typing
		// Enter in a textbox doesn't accidentally exit edit mode.
		const hasEdit = actions.value.some((a) => a.id === "edit");
		if (!hasEdit) return;
		e.preventDefault();
		invoke("edit");
	}
}

function actionStyle(a) {
	switch (a.id) {
		case "submit":
			return "bg-amber-600 text-white hover:bg-amber-700";
		case "save_draft":
			return "bg-gray-700 text-white hover:bg-gray-800";
		case "cancel":
			return "bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-200";
		case "edit":
			return "bg-blue-600 text-white hover:bg-blue-700";
		default:
			return "bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-100";
	}
}

function parseJSON(value) {
	if (!value) return null;
	if (typeof value === "object") return value;
	try {
		return JSON.parse(value);
	} catch (_) {
		return null;
	}
}

async function loadPage(target) {
	if (target < 1 || target > totalPages.value || itemsLoading.value) return;
	if (target === 1) {
		page.value = 1;
		fetchedRows.value = null;
		return;
	}
	itemsLoading.value = true;
	try {
		const out = await getCardItemsPage({
			conversationId: store.currentId,
			messageId: props.message.name,
			page: target,
			pageSize: pageSize.value,
		});
		fetchedRows.value = out?.rows || [];
		page.value = target;
	} catch (err) {
		lastError.value = err?.message || String(err);
	} finally {
		itemsLoading.value = false;
	}
}

// Phase 32 — when UndoBanner reports a successful reversal, patch the
// locally-held message payload so the banner disappears and the
// receipt re-renders with an "Undone" pill.  The server has already
// stamped ``card.undone = true`` and inserted a follow-up assistant
// message, but the realtime channel can lag — overlaying the change
// here makes the UI feel instant.
function onUndone(result) {
	try {
		const next = { ...(card.value || {}) };
		next.undone = true;
		next.undo_result = {
			action: result?.action || "cancelled",
			doctype: result?.doctype,
			docname: result?.docname,
		};
		next.actions = [];
		props.message.rendered_card_payload = JSON.stringify(next);
	} catch (err) {
		// Non-fatal — realtime will catch up shortly.
		lastError.value = err?.message || String(err);
	}
}

async function invoke(action) {
	lastError.value = null;
	if (action === "edit") {
		if (!editing.value) {
			// Seed edits.header from current header values so toggling Edit
			// shows the extracted data in the inputs (Phase 24 fix).
			const seeded = {};
			for (const f of headerFields.value) {
				if (f && f.fieldname) {
					seeded[f.fieldname] = f.value ?? "";
				}
			}
			edits.value.header = seeded;
		}
		editing.value = !editing.value;
		return;
	}

	if (action === "cancel") {
		// Phase 24 — Cancel resets all pending edits to the extracted data
		// currently held in the card payload.  No backend call is made;
		// the persisted card payload is the source of truth and is never
		// mutated on cancel.
		edits.value = {
			header: {},
			item_mappings: {},
			account_mappings: {},
			item_stock_overrides: {},
			row_edits: {},
		};
		revalidationWarnings.value = [];
		editing.value = false;
		emit("confirmed", { action, result: null });
		return;
	}

	try {
		const hasEdits =
			Object.keys(edits.value.header).length ||
			Object.keys(edits.value.item_mappings).length ||
			Object.keys(edits.value.account_mappings).length ||
			Object.keys(edits.value.item_stock_overrides).length ||
			Object.keys(edits.value.row_edits).length;
		const editsPayload =
			(editing.value && hasEdits) ||
			// Mapping/stock toggles are always live, even outside Edit mode.
			Object.keys(edits.value.item_mappings).length ||
			Object.keys(edits.value.account_mappings).length ||
			Object.keys(edits.value.item_stock_overrides).length ||
			Object.keys(edits.value.row_edits).length
				? buildEditsPayload()
				: null;
		const result = await confirm({
			messageId: props.message.name,
			action,
			edits: editsPayload,
		});
		revalidationWarnings.value = result?.revalidation_warnings || [];
		// Surface a server-side error envelope (e.g. insert failed) onto
		// the card itself in addition to the chat.
		if (result?.error?.friendly_message) {
			lastError.value = result.error.friendly_message;
		}
		// After a successful submit/save_draft the action set is cleared
		// server-side, so the card re-renders read-only.  Drop edit mode
		// locally too in case the user had it open.
		editing.value = false;
		emit("confirmed", { action, result });
	} catch (err) {
		lastError.value = err?.message || String(err);
	}
}

function buildEditsPayload() {
	const out = {};
	if (Object.keys(edits.value.header).length) out.header = { ...edits.value.header };
	if (Object.keys(edits.value.item_mappings).length) {
		out.item_mappings = { ...edits.value.item_mappings };
	}
	if (Object.keys(edits.value.account_mappings).length) {
		out.account_mappings = { ...edits.value.account_mappings };
	}
	if (Object.keys(edits.value.item_stock_overrides).length) {
		out.item_stock_overrides = { ...edits.value.item_stock_overrides };
	}
	if (Object.keys(edits.value.row_edits).length) {
		out.row_edits = JSON.parse(JSON.stringify(edits.value.row_edits));
	}
	return out;
}

function onItemEdit({ index, value }) {
	if (index == null) return;
	edits.value.item_mappings = {
		...edits.value.item_mappings,
		[index]: value,
	};
}

function onItemStockEdit({ index, value }) {
	if (index == null) return;
	edits.value.item_stock_overrides = {
		...edits.value.item_stock_overrides,
		[index]: !!value,
	};
}

function onTaxEdit({ index, value }) {
	if (index == null) return;
	edits.value.account_mappings = {
		...edits.value.account_mappings,
		[index]: value,
	};
}

// Phase 25 §25.1 — generic child-table cell edit.  Folds into the
// ``row_edits`` patch keyed by row index.
function onGenericCellEdit({ index, fieldname, value }) {
	if (index == null || !fieldname) return;
	const current = edits.value.row_edits[index] || {};
	edits.value.row_edits = {
		...edits.value.row_edits,
		[index]: { ...current, [fieldname]: value },
	};
}

// Phase 25 §25.4 — bulk action handlers.  All three mutate the
// persisted card on the server, then reload the page so the local
// view reflects the new state.

async function onBulkMatch() {
	if (bulkBusy.value) return;
	bulkBusy.value = true;
	bulkResult.value = "";
	try {
		const out = await bulkMatchItems({
			conversationId: store.currentId,
			messageId: props.message.name,
			matchThreshold: 0.6,
			onlyUnmatched: true,
		});
		bulkResult.value = `${out?.matched || 0} matched, ${out?.refreshed || 0} refreshed.`;
		applyCardPatch(out);
	} catch (err) {
		lastError.value = err?.message || String(err);
	} finally {
		bulkBusy.value = false;
	}
}

async function onBulkAccept() {
	if (bulkBusy.value) return;
	bulkBusy.value = true;
	bulkResult.value = "";
	try {
		const out = await bulkAcceptSuggestions({
			conversationId: store.currentId,
			messageId: props.message.name,
		});
		bulkResult.value = `Accepted ${out?.accepted || 0} suggestion(s).`;
		applyCardPatch(out);
	} catch (err) {
		lastError.value = err?.message || String(err);
	} finally {
		bulkBusy.value = false;
	}
}

async function onApplyUomToAll() {
	const value = bulkUom.value.trim();
	if (!value || bulkBusy.value) return;
	bulkBusy.value = true;
	bulkResult.value = "";
	try {
		const out = await applyToAllRows({
			conversationId: store.currentId,
			messageId: props.message.name,
			fieldname: "uom",
			value,
			rowKind: "items",
		});
		bulkResult.value = `UOM = ${value} applied to ${out?.updated || 0} row(s).`;
		applyCardPatch(out);
	} catch (err) {
		lastError.value = err?.message || String(err);
	} finally {
		bulkBusy.value = false;
	}
}

function applyCardPatch(out) {
	// The bulk endpoints return the freshly-persisted card payload —
	// patch it onto the local message so the table re-renders without
	// a round-trip to the conversation endpoint.
	const payload = out?.rendered_card_payload;
	if (!payload || typeof store.patchMessage !== "function") return;
	store.patchMessage(props.message.name, { rendered_card_payload: payload });
}

// ---------------------------------------------------------------------------
// Phase 31 G16 — per-field re-extract
// ---------------------------------------------------------------------------

async function onReExtract(field) {
	if (!field?.fieldname) return;
	if (reExtractBusy.value[field.fieldname]) return;
	reExtractBusy.value = {
		...reExtractBusy.value,
		[field.fieldname]: true,
	};
	try {
		const out = await reExtractField({
			conversationId: store.currentId,
			messageId: props.message.name,
			fieldName: field.fieldname,
		});
		applyCardPatch(out);
	} catch (err) {
		lastError.value = err?.message || String(err);
	} finally {
		const next = { ...reExtractBusy.value };
		delete next[field.fieldname];
		reExtractBusy.value = next;
	}
}

// ---------------------------------------------------------------------------
// Phase 31 G17 — bulk-turn confirmation actions.
// ---------------------------------------------------------------------------

function hasBlockingWarnings(msg) {
	const payload = parseJSON(msg?.rendered_card_payload);
	const warnings = payload?.warnings;
	if (!Array.isArray(warnings)) return false;
	// A "blocking" warning is anything tagged ``severity: 'error'`` OR
	// anything in the legacy ``revalidation_warnings`` bucket.  Plain
	// string warnings are treated as informational.
	for (const w of warnings) {
		if (typeof w === "string") continue;
		if (w?.severity === "error") return true;
		if (w?.blocking) return true;
	}
	return false;
}

function turnSiblingIds(filterFn) {
	const out = [];
	for (const m of turnCardSiblings.value) {
		if (filterFn && !filterFn(m)) continue;
		out.push(m.name);
	}
	return out;
}

async function submitMany(ids) {
	if (!ids.length) return { ok: 0, fail: 0 };
	let ok = 0;
	let fail = 0;
	for (const id of ids) {
		try {
			await confirm({ messageId: id, action: "submit", edits: null });
			ok += 1;
		} catch (_err) {
			fail += 1;
		}
	}
	return { ok, fail };
}

async function onTurnConfirmOK() {
	if (bulkTurnBusy.value) return;
	bulkTurnBusy.value = true;
	bulkTurnResult.value = "";
	try {
		const ids = turnSiblingIds((m) => !hasBlockingWarnings(m));
		if (!ids.length) {
			bulkTurnResult.value = "No clean cards.";
			return;
		}
		const { ok, fail } = await submitMany(ids);
		bulkTurnResult.value = `Submitted ${ok}${fail ? ` · ${fail} failed` : ""}.`;
		emit("confirmed", { action: "submit", result: null });
	} finally {
		bulkTurnBusy.value = false;
	}
}

async function onTurnConfirmAll() {
	if (bulkTurnBusy.value) return;
	bulkTurnBusy.value = true;
	bulkTurnResult.value = "";
	try {
		const ids = turnSiblingIds();
		if (!ids.length) {
			bulkTurnResult.value = "Nothing to confirm.";
			return;
		}
		const blocking = turnSiblingIds(hasBlockingWarnings).length;
		if (blocking) {
			const msg = `${blocking} card(s) have warnings. Submit anyway?`;
			const proceed = await confirmDialog(msg, {
				title: "Submit with warnings",
				okLabel: "Submit anyway",
			});
			if (!proceed) return;
		}
		const { ok, fail } = await submitMany(ids);
		bulkTurnResult.value = `Submitted ${ok}${fail ? ` · ${fail} failed` : ""}.`;
		emit("confirmed", { action: "submit", result: null });
	} finally {
		bulkTurnBusy.value = false;
	}
}

function onTurnReviewWarnings() {
	// Scroll to the first sibling that carries a blocking warning so the
	// user can inspect it manually.  Falls back to the first card with
	// any warnings, then to the first card.
	const blocking = turnCardSiblings.value.find(hasBlockingWarnings);
	const target =
		blocking ||
		turnCardSiblings.value.find((m) => {
			const p = parseJSON(m.rendered_card_payload);
			return Array.isArray(p?.warnings) && p.warnings.length;
		}) ||
		turnCardSiblings.value[0];
	if (!target) return;
	// The DOM id pattern follows MessageBubble — fall back to a generic
	// ``[data-message-id]`` lookup if needed.
	const el =
		document.getElementById(`msg-${target.name}`) ||
		document.querySelector(`[data-message-id="${target.name}"]`);
	if (el && typeof el.scrollIntoView === "function") {
		el.scrollIntoView({ behavior: "smooth", block: "center" });
	}
	bulkTurnResult.value = "";
}
</script>
