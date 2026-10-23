<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->

<template>
	<div class="idp-chat-shell tm-ocr flex h-screen w-full overflow-hidden bg-gray-100 dark:bg-gray-950">
		<!-- Phase 33 — Skip link.  Anchors to the message region so a
         keyboard / screen-reader user can bypass the sidebar nav.  The
         link is visually hidden until focused, but always in the tab
         order. -->
		<a href="#idp-messages" class="idp-skip-link">Skip to messages</a>

		<!-- Phase 33 — Mobile drawer overlay.  Below 768px the sidebar is
         positioned fixed and slides in/out; the overlay catches taps
         outside to close it. -->
		<div
			v-if="mobileSidebarOpen"
			class="idp-drawer-overlay"
			aria-hidden="true"
			@click="mobileSidebarOpen = false"
		/>

		<ConversationList
			class="idp-sidebar"
			:class="{ 'idp-sidebar--open': mobileSidebarOpen }"
			:sessions="store.sessions"
			:active-id="store.currentId"
			:loading="store.sessionsLoading"
			:current-status="status"
			:starting="startingConversation"
			:collapsed="sidebarCollapsed"
			:search-enabled="sidebarSearchEnabled"
			:delete-enabled="conversationDeleteEnabled"
			:show-settings="isManager"
			:clerk-mode="clerkMode"
			@select="onSelectMobileClose"
			@new="startNewConversation"
			@status-change="onStatusChange"
			@toggle="sidebarCollapsed = !sidebarCollapsed"
			@search="onSidebarSearch"
			@delete="onDeleteConversation"
			@settings="onOpenSettings"
		/>

		<main class="relative flex flex-1 flex-col overflow-hidden">
			<!-- Mobile hamburger floats over the messages area; the persistent
           title header was removed per design. -->
			<button
				type="button"
				class="idp-hamburger absolute left-2 top-2 z-10 rounded bg-white/90 p-1 text-gray-500 shadow-sm hover:bg-gray-100 dark:bg-gray-900/90 dark:text-gray-300 dark:hover:bg-gray-800"
				aria-label="Open conversation list"
				:aria-expanded="mobileSidebarOpen ? 'true' : 'false'"
				aria-controls="idp-sidebar"
				@click="mobileSidebarOpen = !mobileSidebarOpen"
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="h-5 w-5"
					aria-hidden="true"
				>
					<path
						fill-rule="evenodd"
						d="M2 5a1 1 0 0 1 1-1h14a1 1 0 1 1 0 2H3a1 1 0 0 1-1-1Zm0 5a1 1 0 0 1 1-1h14a1 1 0 1 1 0 2H3a1 1 0 0 1-1-1Zm1 4a1 1 0 1 0 0 2h14a1 1 0 1 0 0-2H3Z"
						clip-rule="evenodd"
					/>
				</svg>
			</button>

			<div
				v-if="!store.currentId"
				class="flex flex-1 items-center justify-center bg-gray-50 p-6 text-sm text-gray-500 dark:bg-gray-950"
			>
				<div class="max-w-md text-center tm-ocr-welcome">
					<div class="tm-ocr-empty-icon">
						<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<path d="M14 2v6h6" />
							<path d="M8 13h8" />
							<path d="M8 17h5" />
						</svg>
					</div>
					<h1>Invoice OCR</h1>
					<p>
						{{ clerkMode ? "Drop a supplier PDF or photo to create a draft Purchase Invoice." : "How can I help you today?" }}
					</p>
					<button
						class="tm-ocr-scan mt-6"
						:disabled="startingConversation"
						@click="startNewConversation"
					>
						{{ startingConversation ? "Starting…" : clerkMode ? "Scan a bill" : "Start a new conversation" }}
					</button>
					<div class="mt-3 text-xs text-gray-400">
						{{ clerkMode ? "Or open a recent scan from the sidebar." : "Or pick an existing one from the sidebar." }}
					</div>
				</div>
			</div>

			<div
				v-else
				id="idp-messages"
				ref="scrollEl"
				role="region"
				aria-label="Conversation messages"
				tabindex="-1"
				class="idp-messages relative flex-1 space-y-4 overflow-y-auto bg-gray-100 p-4 dark:bg-gray-950"
				@scroll="onScroll"
			>
				<div
					v-if="!store.visibleMessages.length && !store.agentState.running"
					class="tm-ocr-empty"
				>
					<div class="tm-ocr-empty-icon">
						<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<path d="M14 2v6h6" />
							<path d="M12 18v-6" />
							<path d="M9 15h6" />
						</svg>
					</div>
					<h2>{{ clerkMode ? "Scan a supplier bill" : "New conversation ready" }}</h2>
					<p>
						<template v-if="clerkMode">
							Attach a PDF or photo below, then click Extract. Review the card and create a draft Purchase Invoice.
						</template>
						<template v-else>
						Drop a document into the box below or type a question — for example,
						<em>“extract data and create a Purchase Invoice from this PDF.”</em>
						</template>
					</p>
					<button
						v-for="(p, i) in suggestedPromptChips"
						:key="i"
						type="button"
						class="tm-ocr-chip"
						@click="onPickSuggested(p)"
					>
						{{ chipLabel(p) }}
					</button>
				</div>

				<MessageBubble
					v-for="msg in store.visibleMessages"
					:key="msg.name"
					:message="msg"
					@confirmed="onConfirmed"
					@focus-source="onFocusSource"
				/>
				<!-- Phase 30 — progress banner docks ABOVE the streaming
             bubble per roadmap §30.7. -->
				<ProgressBanner />
				<!-- Phase 30 — ephemeral streaming bubble.  Removed by the
             realtime handlers once the assistant ``IDP Message`` row
             lands (or the turn errors / completes / is cancelled). -->
				<div v-if="store.streamingState.active" class="flex justify-start">
					<div
						class="max-w-[80%] rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
					>
						<span class="whitespace-pre-wrap">{{ store.streamingState.text }}</span>
						<span
							class="ml-0.5 inline-block animate-pulse text-gray-400"
							aria-hidden="true"
							>▍</span
						>
						<div
							v-if="store.streamingState.cancelling"
							class="mt-1 text-[10px] italic text-gray-500"
						>
							Cancelling…
						</div>
					</div>
				</div>
				<ThinkingIndicator />
				<!-- Global error banner.  Hidden when the latest persisted
				     message already conveys the same error string (failed
				     tool result or ErrorCard) so we don't render the same
				     message two or three times in a row. -->
				<div
					v-if="store.agentState.lastError && !errorAlreadyInMessages"
					class="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
					role="alert"
				>
					{{ store.agentState.lastError }}
				</div>

				<!-- Phase 33 — Screen-reader announcement region for streaming
             assistant prose.  Updated at most every ~500ms so screen
             readers don't get spammed token-by-token.  Visually hidden
             but read aloud as content lands. -->
				<div class="sr-only" role="status" aria-live="polite" aria-atomic="false">
					{{ liveAnnouncement }}
				</div>

				<button
					v-if="showScrollButton"
					type="button"
					class="sticky bottom-4 ml-auto block rounded-full bg-gray-900 px-3 py-1 text-[11px] font-medium text-white shadow-md hover:bg-gray-800"
					@click="scrollToBottom(true)"
				>
					↓ Jump to latest
				</button>
			</div>

			<ChatInput
				v-if="store.currentId"
				:disabled="store.agentState.running"
				:cancellable="store.agentState.running"
				:cancelling="cancelling"
				:placeholder="clerkMode ? 'Attach the bill, then click Extract…' : undefined"
				:extract-prompt="extractPrompt"
				:send-label="clerkMode ? 'Extract' : undefined"
				:clerk-mode="clerkMode"
				@send="onSend"
				@cancel="onCancel"
			/>

			<!-- Phase 31 G12 — token / cost chip footer.  Falls back to the
           legacy plain footer when ``enable_cost_footer`` is disabled. -->
			<CostFooter v-if="costFooterEnabled" />
			<footer
				v-else-if="footerStats"
				class="border-t border-gray-200 bg-white px-4 py-1 text-[11px] text-gray-500 dark:border-gray-700 dark:bg-gray-900"
			>
				{{ footerStats }}
			</footer>
		</main>

		<!-- Phase 29 — click-to-source PDF preview panel.  Renders only
         when a field with a recorded bbox is clicked. -->
		<aside
			v-if="focusSource"
			class="flex w-[420px] max-w-[40vw] flex-col border-l border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
		>
			<div
				class="flex items-center justify-between border-b border-gray-200 px-3 py-2 dark:border-gray-700"
			>
				<div class="min-w-0">
					<div class="truncate text-xs font-semibold text-gray-700 dark:text-gray-200">
						Source: {{ focusSource.field || "document" }}
					</div>
					<div class="truncate text-[10px] text-gray-500 dark:text-gray-400">
						{{ focusSourceFileName }}
						<span v-if="focusSource.page">· page {{ focusSource.page }}</span>
					</div>
				</div>
				<button
					type="button"
					class="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label="Close source preview"
					@click="focusSource = null"
				>
					✕
				</button>
			</div>
			<div class="flex-1 overflow-auto p-3">
				<PdfPreview
					v-if="focusSourceFileUrl"
					:file-url="focusSourceFileUrl"
					:highlight="pdfHighlight"
				/>
				<div
					v-else
					class="rounded border border-dashed border-gray-300 p-4 text-xs text-gray-500 dark:border-gray-700"
				>
					Could not resolve the source attachment for this field.
				</div>
			</div>
		</aside>

		<NewConversationDialog
			:open="dialogOpen"
			:prefill="dialogPrefill"
			@close="dialogOpen = false"
			@created="onCreated"
		/>
	</div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ConversationList from "@/components/chat/ConversationList.vue";
import MessageBubble from "@/components/chat/MessageBubble.vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import ThinkingIndicator from "@/components/chat/ThinkingIndicator.vue";
import ProgressBanner from "@/components/chat/ProgressBanner.vue";
import NewConversationDialog from "@/components/chat/NewConversationDialog.vue";
import PdfPreview from "@/components/chat/PdfPreview.vue";
import CostFooter from "@/components/chat/CostFooter.vue";

import { useConversationStore } from "@/stores/conversation";
import { useConversation } from "@/composables/useConversation";
import { useAgent } from "@/composables/useAgent";
import { useConversationRealtime } from "@/composables/useRealtimeEvents";
import { useSettings } from "@/composables/useSettings";
import { useCost } from "@/composables/useCost";
import { useSuggestedPrompts } from "@/composables/useSuggestedPrompts";
import { confirm } from "@/composables/useConfirm";
import { cancelTurn } from "@/utils/api";
import "@/assets/taxmate-ocr.css";

const store = useConversationStore();
const route = useRoute();
const router = useRouter();
const { refreshSessions, loadConversation, deleteConversation, searchSessions, quickStart } =
	useConversation();
const { send } = useAgent();
const { settings, load: loadSettings } = useSettings();
const { confirmIfNeeded } = useCost();
const { prompts: suggestedPrompts, load: loadSuggestedPrompts } = useSuggestedPrompts();

// Phase 31 — sidebar collapse / settings-gated UI flags.
const sidebarCollapsed = ref(false);

// Phase 33 — mobile drawer state.  On < 768px the sidebar is hidden by
// default and slides in when the hamburger is tapped; on desktop the
// drawer state is irrelevant (CSS resets ``transform`` to identity).
const mobileSidebarOpen = ref(false);

// Phase 33 — Throttled aria-live announcement of streaming assistant
// prose.  We mirror the streaming text into ``liveAnnouncement`` at
// most every 500ms so a screen-reader narrates the message as it
// lands without being spammed token-by-token.
const liveAnnouncement = ref("");
let liveAnnouncementTimer = null;
let lastAnnouncedText = "";

function scheduleLiveAnnouncement() {
	if (liveAnnouncementTimer != null) return;
	liveAnnouncementTimer = window.setTimeout(() => {
		liveAnnouncementTimer = null;
		const text = store.streamingState?.text || "";
		if (text && text !== lastAnnouncedText) {
			lastAnnouncedText = text;
			liveAnnouncement.value = text;
		}
	}, 500);
}

watch(
	() => store.streamingState?.text,
	() => scheduleLiveAnnouncement(),
);

function onSelectMobileClose(id) {
	mobileSidebarOpen.value = false;
	onSelect(id);
}

function flag(key, fallback = true) {
	const v = settings.value?.[key];
	if (v == null) return fallback;
	return !!Number(v);
}

const clerkMode = computed(() => settings.value?.clerk_mode !== false);
const isManager = computed(() => !!settings.value?.is_manager);
const extractPrompt = computed(
	() => settings.value?.extract_prompt || "Extract this supplier bill into a draft Purchase Invoice.",
);

const sidebarSearchEnabled = computed(() => (clerkMode.value ? false : flag("enable_sidebar_search")));
const conversationDeleteEnabled = computed(() => flag("enable_conversation_delete"));
const costFooterEnabled = computed(() => (clerkMode.value ? false : flag("enable_cost_footer")));

function chipLabel(p) {
	if (p && typeof p === "object") {
		return p.description || p.text || "Extract this bill";
	}
	const s = String(p || "").trim();
	if (s.startsWith("{")) {
		try {
			const o = JSON.parse(s);
			return o.description || o.text || s;
		} catch (_) {
			return s;
		}
	}
	return s;
}

function chipText(p) {
	if (p && typeof p === "object") {
		return p.text || p.description || "";
	}
	const s = String(p || "").trim();
	if (s.startsWith("{")) {
		try {
			const o = JSON.parse(s);
			return o.text || o.description || s;
		} catch (_) {
			return s;
		}
	}
	return s;
}

const suggestedPromptChips = computed(() => {
	if (clerkMode.value) {
		return [{ text: extractPrompt.value, description: "Extract this bill" }];
	}
	if (!flag("enable_suggested_prompts")) return [];
	const list = suggestedPrompts.value;
	return Array.isArray(list) ? list.slice(0, 6) : [];
});

// Track the last-applied sidebar search payload so refreshSessions and
// status-change handlers can re-run it (otherwise the unfiltered list
// would silently replace the filtered results).
const activeSearch = ref(null);

const dialogPrefill = ref(null);
const startingConversation = ref(false);

// Greeting on the empty-state welcome card.  Pulls the user's full name
// from Frappe's global session bootstrap when available; falls back to
// "there" so the greeting still reads naturally for guest sessions.
const userFullname = computed(() => {
	const fp = typeof window !== "undefined" ? window.frappe : null;
	return fp?.session?.user_fullname || fp?.boot?.user?.fullname || fp?.session?.user || "there";
});

// Avatar image — uses the user's uploaded Frappe avatar when present.
// When absent we fall back to a grey circle showing the first letter
// of `userFullname` (see template).
const userImage = computed(() => {
	const fp = typeof window !== "undefined" ? window.frappe : null;
	return fp?.session?.user_image || fp?.boot?.user?.user_image || "";
});

const userInitial = computed(() => {
	const name = (userFullname.value || "").trim();
	if (!name || name === "there") return "A";
	return name[0].toUpperCase();
});

// Phase 29 — click-to-source side panel.
// Holds `{ file_id, file_url?, field, page, bbox }` of the most-recently
// clicked confidence dot.  Cleared when the user closes the panel or
// switches conversation so we don't leak the previous PDF render.
const focusSource = ref(null);

const status = ref("Active");
const dialogOpen = ref(false);
const scrollEl = ref(null);
const showScrollButton = ref(false);
const SCROLL_PINNED_THRESHOLD = 80;

// True when the most recent persisted message already surfaces the
// same error string carried by ``store.agentState.lastError`` — either
// as an ErrorCard payload or as the ``error`` field on a failed tool
// result.  The MessageBubble renderer covers both, so the global
// banner becomes redundant noise in those cases.
const errorAlreadyInMessages = computed(() => {
	const err = store.agentState.lastError
	if (!err) return false
	const target = String(err).trim()
	if (!target) return false
	const msgs = store.visibleMessages || []
	const tail = msgs.slice(-6)
	for (const m of tail) {
		if (m.error && String(m.error).trim() === target) return true
		if (
			m.rendered_card_type === 'ErrorCard' &&
			typeof m.rendered_card_payload === 'string' &&
			m.rendered_card_payload.includes(target)
		) {
			return true
		}
		if (
			m.role === 'tool' &&
			typeof m.tool_result === 'string' &&
			m.tool_result.includes(target)
		) {
			return true
		}
	}
	return false
})

const footerStats = computed(() => {
	const d = store.currentDetail;
	if (!d) return "";
	const parts = [];
	if (d.message_count) parts.push(`${d.message_count} msg`);
	if (d.total_tokens_used) parts.push(`${d.total_tokens_used} tokens`);
	if (d.estimated_cost_usd) {
		parts.push(`$${Number(d.estimated_cost_usd).toFixed(4)}`);
	}
	return parts.join(" · ");
});

// -- realtime wiring -------------------------------------------------------
const conversationIdRef = computed(() => store.currentId);
useConversationRealtime(conversationIdRef, {
	idp_conversation_thinking(payload) {
		store.setAgentThinking(payload.iteration || 0);
	},
	idp_conversation_tool_start(payload) {
		store.setAgentTool(payload.name || null);
	},
	idp_conversation_tool_end() {
		store.setAgentTool(null);
	},
	idp_conversation_message(payload) {
		if (payload.message) store.appendMessage(payload.message);
		// The persisted row supersedes the streaming buffer for this seq.
		store.resetStreaming();
		store.resetProgress();
		nextTick(scrollToBottom);
	},
	idp_conversation_error(payload) {
		store.setAgentError(payload.error || `Error (${payload.error_code || "unknown"})`);
		store.resetStreaming();
		store.resetProgress();
	},
	idp_conversation_complete() {
		store.setAgentSummary(null);
		store.resetStreaming();
		store.resetProgress();
	},
	// Phase 30 — incremental assistant prose deltas.
	idp_conversation_token(payload) {
		store.appendStreamToken(payload);
		nextTick(scrollToBottom);
	},
	idp_conversation_tool_call_start(payload) {
		if (payload?.tool_name) store.setAgentTool(payload.tool_name);
	},
	idp_conversation_progress(payload) {
		store.setProgress(payload);
	},
});

// -- Phase 30: cancel handling --------------------------------------------
const cancelling = ref(false);
async function onCancel() {
	if (cancelling.value) return;
	if (!store.currentId) return;
	cancelling.value = true;
	store.markStreamCancelling();
	try {
		await cancelTurn(store.currentId);
	} catch (err) {
		// eslint-disable-next-line no-console
		console.warn("[ChatView] cancel_turn failed", err);
	} finally {
		// Reset the local flag once the agent loop emits ``complete`` /
		// ``message`` — fall back to a short timeout in case the run had
		// already terminated server-side before our request landed.
		setTimeout(() => {
			cancelling.value = false;
		}, 800);
	}
}

// -- routing sync ----------------------------------------------------------
watch(
	() => route.params.id,
	async (id) => {
		if (id && id !== store.currentId) {
			try {
				await loadConversation(id);
				nextTick(scrollToBottom);
			} catch (err) {
				// If load fails (e.g. archived/no permission) bounce to /chat.
				// eslint-disable-next-line no-console
				console.error("[ChatView] failed to load conversation", err);
				router.replace({ name: "ChatHome" });
			}
		} else if (!id) {
			store.clearCurrent();
		}
	},
	{ immediate: true },
);

watch(
	() => store.visibleMessages.length,
	() => {
		nextTick(scrollToBottom);
	},
);

function scrollToBottom(force = false) {
	const el = scrollEl.value;
	if (!el) return;
	if (!force) {
		// Only auto-scroll when the user is already near the bottom; this
		// prevents yanking them away while they're reading older messages.
		const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
		if (distance > SCROLL_PINNED_THRESHOLD) {
			showScrollButton.value = true;
			return;
		}
	}
	el.scrollTop = el.scrollHeight;
	showScrollButton.value = false;
}

function onScroll() {
	const el = scrollEl.value;
	if (!el) return;
	const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
	showScrollButton.value = distance > SCROLL_PINNED_THRESHOLD;
}

async function onSelect(id) {
	if (id === store.currentId) return;
	router.push({ name: "ChatConversation", params: { id } });
}

async function onStatusChange(value) {
	status.value = value;
	if (activeSearch.value) {
		await searchSessions({ ...activeSearch.value, status: value });
	} else {
		await refreshSessions({ status: value });
	}
}

// Phase 31 G15 — sidebar search/filter.  An empty payload (no query +
// default chips) reverts to the plain list to keep parity with the
// pre-Phase-31 behaviour.
async function onSidebarSearch(payload) {
	const isEmpty = !payload?.query && payload?.dateRange === "all" && !payload?.hasAttachments;
	if (isEmpty) {
		activeSearch.value = null;
		await refreshSessions({ status: status.value });
		return;
	}
	activeSearch.value = payload;
	await searchSessions(payload);
}

// Sidebar cog → IDP Settings.  Opens the Frappe desk form in a new tab
// so the chat session isn't disrupted.  Falls back to the relative URL
// when the Frappe global isn't loaded (e.g. local dev preview).
function onOpenSettings() {
	const url = "/app/idp-settings";
	if (typeof window !== "undefined") {
		window.open(url, "_blank", "noopener");
	}
}

async function onDeleteConversation(row) {
	if (!row?.name) return;
	const title = row.title || `Conversation ${row.name}`;
	const proceed = await confirm(`Delete "${title}"? This cannot be undone.`, {
		title: "Delete conversation",
		okLabel: "Delete",
		variant: "danger",
	});
	if (!proceed) return;
	try {
		await deleteConversation(row.name);
		if (route.params.id === row.name) {
			router.replace({ name: "ChatHome" });
		}
		if (activeSearch.value) {
			await searchSessions({ ...activeSearch.value, status: status.value });
		} else {
			await refreshSessions({ status: status.value });
		}
	} catch (err) {
		// eslint-disable-next-line no-console
		console.error("[ChatView] delete failed", err);
		window.alert(err?.message || "Failed to delete conversation.");
	}
}

// Phase 31 G14 — clicking a suggested prompt chip drops the text into
// the composer.  ChatInput exposes a custom event API, so we instead
// dispatch a window-level event that the input listens for; this keeps
// the chip free of tight coupling to ChatInput's internals.
function onPickSuggested(prompt) {
	const text = chipText(prompt);
	if (!text) return;
	window.dispatchEvent(new CustomEvent("idp:chat-input:prefill", { detail: { text } }));
}

async function onSend({ content, attachments }) {
	// Phase 31 G13 — pre-flight cost check.  Cancelling the modal aborts
	// the send and consumes no token budget.
	const proceed = await confirmIfNeeded({
		conversationId: store.currentId,
		content,
		attachments,
	});
	if (!proceed) return;
	try {
		await send({ content, attachments });
		if (activeSearch.value) {
			await searchSessions({ ...activeSearch.value, status: status.value });
		} else {
			await refreshSessions({ status: status.value });
		}
	} catch (err) {
		// surfaced via store.agentState.lastError
	}
}

function onConfirmed() {
	// ConfirmationCardUI calls confirm() which already triggers run_agent;
	// this hook lets us refresh sidebar metadata after submit.
	refreshSessions({ status: status.value });
}

// Phase 29 — receive `focus-source` events from ConfirmationCardUI via
// MessageBubble.  Payload shape: { file_id, field, page, bbox }.
function onFocusSource(payload) {
	if (!payload) {
		focusSource.value = null;
		return;
	}
	focusSource.value = { ...payload };
}

const focusAttachment = computed(() => {
	const fs = focusSource.value;
	if (!fs) return null;
	const attachments = store.currentDetail?.attachments || [];
	if (fs.file_url) {
		// Direct URL provided — find a name for the header label.
		return (
			attachments.find((a) => a.file_url === fs.file_url) || {
				file_url: fs.file_url,
				file_name: fs.file_url,
			}
		);
	}
	if (fs.file_id) {
		return attachments.find((a) => a.file_id === fs.file_id) || null;
	}
	return null;
});

const focusSourceFileUrl = computed(() => focusAttachment.value?.file_url || "");
const focusSourceFileName = computed(
	() => focusAttachment.value?.file_name || focusAttachment.value?.file_url || "",
);

// PdfPreview expects a single ``highlight`` prop shaped { page, bbox }
// — assemble it from the focusSource payload emitted by the card.
const pdfHighlight = computed(() => {
	const fs = focusSource.value;
	if (!fs) return null;
	const page = Number(fs.page) || 1;
	const bbox = Array.isArray(fs.bbox) ? fs.bbox : null;
	if (!bbox) return { page, bbox: null };
	return { page, bbox };
});

// Clear the source panel whenever the active conversation changes — the
// previously focused field belongs to a different document.
watch(
	() => store.currentId,
	() => {
		focusSource.value = null;
	},
);

async function onCreated(out) {
	if (out?.conversation_id) {
		await refreshSessions({ status: status.value });
		router.push({
			name: "ChatConversation",
			params: { id: out.conversation_id },
		});
	}
}

/**
 * Start a new conversation.  Prefer the IDP Settings defaults so the
 * user never sees the picker; only fall back to the modal when the
 * settings are incomplete.
 */
async function startNewConversation() {
	if (startingConversation.value) return;
	startingConversation.value = true;
	try {
		const out = await quickStart();
		if (out?.needsModal) {
			dialogPrefill.value = out.defaults || null;
			dialogOpen.value = true;
			return;
		}
		if (out?.conversation_id) {
			await refreshSessions({ status: status.value });
			router.push({
				name: "ChatConversation",
				params: { id: out.conversation_id },
			});
		}
	} catch (err) {
		// Fallback: open the picker so the user can supply values manually.
		// eslint-disable-next-line no-console
		console.warn("[ChatView] quickStart failed, opening picker", err);
		dialogPrefill.value = null;
		dialogOpen.value = true;
	} finally {
		startingConversation.value = false;
	}
}

onMounted(async () => {
	// Load IDP Settings before fetching sessions so the feature-flag
	// computeds resolve before the sidebar renders.
	try {
		await loadSettings();
		document.title = "Invoice OCR";
	} catch (_) {
		// Non-fatal — gated UI falls back to its built-in defaults.
	}
	await refreshSessions({ status: status.value });
	// Fire-and-forget; chips render once the cache populates.
	loadSuggestedPrompts();
});
</script>
