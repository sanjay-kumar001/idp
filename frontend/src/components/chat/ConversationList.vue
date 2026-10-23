<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->

<template>
  <aside
    id="idp-sidebar"
    class="flex h-full flex-col border-r border-gray-200 bg-gray-50 transition-[width] duration-150 ease-out dark:border-gray-700 dark:bg-gray-950"
    :class="collapsed ? 'w-12' : 'w-72'"
    aria-label="Conversations"
  >
    <!-- ============================================================ -->
    <!-- Header — two rows:                                             -->
    <!--   Row 1: chat-bubble icon (left)  •  settings + collapse (right)-->
    <!--   Row 2: full-width "+ New Chat" button                        -->
    <!-- When collapsed only the icon rail is shown.                    -->
    <!-- ============================================================ -->
    <header class="border-b border-gray-200 dark:border-gray-700">
      <div
        class="flex items-center"
        :class="collapsed ? 'justify-center p-2' : 'justify-between gap-2 px-3 py-2'"
      >
        <div v-if="!collapsed" class="tm-ocr-brand min-w-0 flex-1">
          <img
            class="tm-ocr-mark"
            :src="brandMark"
            alt=""
          />
          <div class="min-w-0">
            <div class="tm-ocr-title">Invoice OCR</div>
            <div class="tm-ocr-sub">Bills to draft invoices</div>
          </div>
        </div>

        <!-- Right: settings + collapse -->
        <div class="flex items-center gap-1">
          <button
            v-if="!collapsed && showSettings"
            type="button"
            class="rounded-lg p-2 text-gray-600 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-800"
            title="Chatbot Settings"
            aria-label="Settings"
            @click="$emit('settings')"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              class="lucide lucide-settings-icon"
              aria-hidden="true"
            >
              <path
                d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"
              />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </button>

          <button
            type="button"
            class="rounded-lg p-2 text-gray-600 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-800"
            :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
            :aria-expanded="collapsed ? 'false' : 'true'"
            @click="$emit('toggle')"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              class="lucide lucide-panel-left-close-icon"
              aria-hidden="true"
            >
              <rect width="18" height="18" x="3" y="3" rx="2" />
              <path d="M9 3v18" />
              <path v-if="collapsed" d="m14 9 3 3-3 3" />
              <path v-else d="m16 15-3-3 3-3" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Row 2 — full-width "+ New Chat" button -->
      <div v-if="!collapsed" class="px-3 pb-3 pt-1">
        <button
          type="button"
          class="tm-ocr-scan"
          :disabled="starting"
          @click="$emit('new')"
        >
          <span aria-hidden="true">+</span>
          <span>{{ starting ? 'Starting…' : (clerkMode ? 'Scan a bill' : 'New Chat') }}</span>
        </button>
      </div>
    </header>

    <!-- When collapsed, render just a compact +New affordance -->
    <template v-if="collapsed">
      <button
        type="button"
        class="tm-ocr-scan m-2 px-1 py-1 text-xs"
        :disabled="starting"
        title="Scan a bill"
        aria-label="Scan a bill"
        @click="$emit('new')"
      >
        <span aria-hidden="true">+</span>
      </button>
    </template>

    <template v-else>
      <!-- ============================================================ -->
      <!-- Status pills (Active / Archived)                              -->
      <!-- ============================================================ -->
      <div
        v-if="!clerkMode"
        class="flex items-center gap-2 border-b border-gray-200 px-3 py-2 text-xs dark:border-gray-700"
      >
        <button
          v-for="opt in statusOptions"
          :key="opt.value"
          class="rounded px-2 py-0.5"
          :class="
            currentStatus === opt.value
              ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
              : 'text-gray-600 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-800'
          "
          @click="onStatusChange(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>

      <!-- ============================================================ -->
      <!-- Search box + filter chips (gated by enable_sidebar_search)    -->
      <!-- ============================================================ -->
      <div
        v-if="searchEnabled"
        class="space-y-2 border-b border-gray-200 px-3 py-2 dark:border-gray-700"
      >
        <div class="relative">
          <input
            v-model="query"
            type="text"
            placeholder="Search conversations…"
            class="w-full rounded border border-gray-300 bg-white px-2 py-1 pr-6 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
            @input="onSearchInput"
          />
          <button
            v-if="query"
            type="button"
            class="absolute right-1 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            title="Clear"
            @click="clearSearch"
          >
            ×
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-1 text-[11px]">
          <button
            v-for="opt in dateOptions"
            :key="opt.value"
            type="button"
            class="rounded-full px-2 py-0.5"
            :class="
              dateRange === opt.value
                ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
            "
            @click="setDateRange(opt.value)"
          >
            {{ opt.label }}
          </button>

          <label
            class="ml-1 inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-0.5"
            :class="
              hasAttachments
                ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
            "
          >
            <input
              v-model="hasAttachments"
              type="checkbox"
              class="hidden"
              @change="emitSearch"
            />
            📎 attached
          </label>
        </div>
      </div>

      <!-- ============================================================ -->
      <!-- Session list                                                  -->
      <!-- ============================================================ -->
      <div class="flex-1 overflow-y-auto">
        <div
          v-if="loading && !sessions.length"
          class="p-4 text-center text-xs text-gray-500"
        >
          Loading…
        </div>
        <div
          v-else-if="!sessions.length"
          class="p-4 text-center text-xs text-gray-500"
        >
          {{ query ? 'No matches.' : (clerkMode ? 'No scans yet.' : 'No conversations yet. Start a new one.') }}
        </div>
        <ul v-else class="divide-y divide-gray-100 dark:divide-gray-800">
          <li
            v-for="row in sessions"
            :key="row.name"
            class="tm-ocr-session group relative cursor-pointer px-3 py-2 pr-8"
            :class="row.name === activeId ? 'is-active' : ''"
            @click="$emit('select', row.name)"
          >
            <div class="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
              {{ row.title || `Conversation ${row.name}` }}
            </div>
            <div class="mt-0.5 flex items-center justify-between text-[11px] text-gray-500">
              <span>{{ formatTime(row.last_message_on || row.modified) }}</span>
              <span v-if="row.message_count">{{ row.message_count }} msg</span>
            </div>
            <div
              v-if="!clerkMode && (row.target_doctype || row.llm_model)"
              class="mt-0.5 flex items-center gap-1 text-[10px] text-gray-400"
            >
              <span v-if="row.target_doctype">{{ row.target_doctype }}</span>
              <span v-if="row.target_doctype && row.llm_model">·</span>
              <span v-if="row.llm_model">{{ row.llm_model }}</span>
            </div>

            <button
              v-if="deleteEnabled"
              type="button"
              class="absolute right-2 top-2 rounded p-1 text-gray-400 opacity-60 hover:bg-red-100 hover:text-red-600 hover:opacity-100 group-hover:opacity-100 dark:hover:bg-red-900 dark:hover:text-red-300"
              title="Delete conversation"
              aria-label="Delete conversation"
              @click.stop="onDelete(row)"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 20 20"
                fill="currentColor"
                class="h-3.5 w-3.5"
                aria-hidden="true"
              >
                <path
                  fill-rule="evenodd"
                  d="M8.75 1A2.75 2.75 0 0 0 6 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 1 0 .23 1.482l.149-.022.841 10.518A2.75 2.75 0 0 0 7.596 19h4.807a2.75 2.75 0 0 0 2.742-2.53l.841-10.52.149.023a.75.75 0 0 0 .23-1.482A41.03 41.03 0 0 0 14 4.193V3.75A2.75 2.75 0 0 0 11.25 1h-2.5ZM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4ZM8.58 7.72a.75.75 0 0 0-1.5.06l.3 7.5a.75.75 0 1 0 1.5-.06l-.3-7.5Zm4.34.06a.75.75 0 1 0-1.5-.06l-.3 7.5a.75.75 0 1 0 1.5.06l.3-7.5Z"
                  clip-rule="evenodd"
                />
              </svg>
            </button>
          </li>
        </ul>
      </div>
    </template>
    <a v-if="!collapsed" class="tm-ocr-desk" href="/desk/home">← TaxMate Desk</a>
  </aside>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  loading: { type: Boolean, default: false },
  currentStatus: { type: String, default: 'Active' },
  starting: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  searchEnabled: { type: Boolean, default: true },
  deleteEnabled: { type: Boolean, default: true },
  showSettings: { type: Boolean, default: true },
  clerkMode: { type: Boolean, default: false },
})

const emit = defineEmits([
  'select',
  'new',
  'status-change',
  'toggle',
  'search',
  'delete',
  'settings',
])

const brandMark = '/assets/ascra_theme_2/images/taxmate-mark.svg'

const statusOptions = [
  { value: 'Active', label: 'Active' },
  { value: 'Archived', label: 'Archived' },
]

const dateOptions = [
  { value: 'all', label: 'All' },
  { value: 'today', label: 'Today' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
]

const query = ref('')
const dateRange = ref('all')
const hasAttachments = ref(false)

let debounceId = null

function emitSearch() {
  emit('search', {
    query: query.value.trim(),
    dateRange: dateRange.value,
    hasAttachments: hasAttachments.value,
    status: props.currentStatus,
  })
}

function onSearchInput() {
  if (debounceId) clearTimeout(debounceId)
  debounceId = setTimeout(() => emitSearch(), 220)
}

function clearSearch() {
  query.value = ''
  emitSearch()
}

function setDateRange(value) {
  dateRange.value = value
  emitSearch()
}

function onStatusChange(value) {
  emit('status-change', value)
  // Keep the search context aligned to the new status.
  if (query.value || dateRange.value !== 'all' || hasAttachments.value) {
    setTimeout(() => emitSearch(), 0)
  }
}

function onDelete(row) {
  emit('delete', row)
}

// When the parent switches status externally, do not strand the filters.
watch(
  () => props.currentStatus,
  () => {
    if (query.value || dateRange.value !== 'all' || hasAttachments.value) {
      emitSearch()
    }
  },
)

function formatTime(value) {
  if (!value) return ''
  try {
    const d = new Date(value)
    const now = new Date()
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString()
  } catch (_) {
    return value
  }
}
</script>
