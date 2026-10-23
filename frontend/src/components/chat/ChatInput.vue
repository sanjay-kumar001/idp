<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->

<template>
  <form
    class="relative border-t border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900"
    :class="{
      'tm-ocr-composer': clerkMode,
      'ring-2 ring-inset': dragOver,
      'ring-[#12715B]': dragOver && clerkMode,
      'ring-blue-400': dragOver && !clerkMode,
    }"
    @submit.prevent="onSubmit"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <!-- Drag overlay -->
    <div
      v-if="dragOver"
      class="pointer-events-none absolute inset-0 flex items-center justify-center rounded bg-blue-50/80 text-sm font-medium text-blue-700 dark:bg-blue-950/70 dark:text-blue-200"
    >
      Drop files to attach
    </div>

    <!-- Pending attachments -->
    <div v-if="pending.length" class="mb-2 flex flex-wrap gap-2">
      <span
        v-for="(att, idx) in pending"
        :key="att.file_url || idx"
        class="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-gray-100 px-3 py-1 text-xs dark:border-gray-700 dark:bg-gray-800"
      >
        <span aria-hidden="true">📎</span>
        <span class="max-w-[160px] truncate">
          {{ att.file_name || att.file_url }}
        </span>
        <button
          type="button"
          class="text-gray-500 hover:text-red-600"
          aria-label="Remove attachment"
          @click="remove(idx)"
        >
          ×
        </button>
      </span>
    </div>

    <div class="flex items-end gap-2">
      <label
        class="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
        :class="{ 'opacity-50': uploading }"
        title="Attach file (or drag and drop into this box)"
        aria-label="Attach file"
      >
        <input
          ref="fileInput"
          type="file"
          class="hidden"
          multiple
          :disabled="uploading || disabled"
          aria-label="Attach file"
          @change="onFileChange"
        />
        <span aria-hidden="true">📎</span>
      </label>

      <textarea
        v-model="draft"
        :disabled="disabled"
        rows="2"
        class="flex-1 resize-none rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
        :placeholder="placeholder"
        aria-label="Type a message"
        @keydown="onKeydown"
      />

      <button
        v-if="!canCancel"
        type="submit"
        :disabled="!canSubmit"
        class="h-9 rounded bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        :class="{ 'tm-ocr-extract': clerkMode }"
      >
        {{ uploading ? 'Uploading…' : disabled ? 'Working…' : (sendLabel || 'Send') }}
      </button>
      <button
        v-else
        type="button"
        :disabled="cancelling"
        class="h-9 rounded border border-red-300 bg-white px-4 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:bg-gray-900 dark:text-red-300 dark:hover:bg-red-950"
        @click="onCancel"
      >
        {{ cancelling ? 'Cancelling…' : 'Cancel' }}
      </button>
    </div>

    <div class="mt-1.5 flex items-center justify-between">
      <span class="text-[11px] text-gray-400" :class="{ 'tm-ocr-hint': clerkMode }">
        {{ clerkMode ? 'PDF, JPG or PNG · Extract creates a draft invoice' : 'Enter (or ⌘/Ctrl+Enter) to send · Shift+Enter for newline · drag & drop files anywhere in this box' }}
      </span>
      <span
        v-if="uploading"
        class="text-[11px] font-medium text-blue-700 dark:text-blue-300"
      >
        Uploading…
      </span>
    </div>

    <div
      v-if="lastError"
      class="mt-2 text-xs text-red-700 dark:text-red-400"
    >
      {{ lastError }}
    </div>
  </form>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { uploadDocument } from '@/utils/api'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  // Phase 30 — when true, the Send button morphs into Cancel and
  // clicking it emits ``cancel`` so the parent can call
  // ``idp.api.conversation.cancel_turn``.
  cancellable: { type: Boolean, default: false },
  cancelling: { type: Boolean, default: false },
  placeholder: {
    type: String,
    default: 'Ask something or attach a document…',
  },
  extractPrompt: { type: String, default: '' },
  sendLabel: { type: String, default: '' },
  clerkMode: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'cancel'])

const canCancel = computed(() => props.cancellable && props.disabled)

function onCancel() {
  if (props.cancelling) return
  emit('cancel')
}

const draft = ref('')
const pending = ref([])
const uploading = ref(false)
const lastError = ref(null)
const fileInput = ref(null)
const dragDepth = ref(0)
const dragOver = computed(() => dragDepth.value > 0)

const canSubmit = computed(() => {
  if (props.disabled) return false
  if (uploading.value) return false
  if (draft.value.trim().length) return true
  if (pending.value.length) return true
  return false
})

function onKeydown(e) {
  // Phase 33 — a11y keyboard nav.  Cmd/Ctrl+Enter always submits, even
  // when the cursor is on a continuation line (we don't strip the
  // newline since the textarea state hasn't been mutated yet).  Plain
  // Enter still submits (Shift+Enter inserts a newline).
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    onSubmit()
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    onSubmit()
  }
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return
  uploading.value = true
  lastError.value = null
  try {
    for (const file of files) {
      try {
        const res = await uploadDocument(file)
        // frappeRequest returns the unwrapped message; raw fetch gives { message: ... }
        const data = res?.message || res?.data || res
        if (data?.file_url) {
          pending.value.push({
            file_url: data.file_url,
            file_id: data.file_id || data.name,
            file_name: data.file_name || file.name,
            mime_type: data.mime_type || file.type,
          })
        } else if (data?.error) {
          lastError.value = friendlyUploadError(data.error, file.name)
        }
      } catch (err) {
        lastError.value = friendlyUploadError(err, file.name)
      }
    }
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function friendlyUploadError(err, fileName) {
  const base = `Couldn't upload ${fileName || 'this file'}.`
  // Don't surface raw URLs / exception classes; just give the user a hint.
  return `${base} Please try a different file or check your connection.`
}

async function onFileChange(event) {
  await uploadFiles(event.target.files)
}

function onDragEnter(event) {
  if (props.disabled) return
  if (!hasFiles(event)) return
  dragDepth.value += 1
}

function onDragOver(event) {
  if (props.disabled) return
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy'
  }
}

function onDragLeave() {
  if (dragDepth.value > 0) dragDepth.value -= 1
}

async function onDrop(event) {
  dragDepth.value = 0
  if (props.disabled) return
  const files = event.dataTransfer?.files
  if (!files || !files.length) return
  await uploadFiles(files)
}

function hasFiles(event) {
  const types = event.dataTransfer?.types
  if (!types) return false
  // Some browsers expose a DOMStringList, others an array; both have
  // .includes / .contains semantics for "Files".
  if (typeof types.includes === 'function') return types.includes('Files')
  for (let i = 0; i < types.length; i += 1) {
    if (types[i] === 'Files') return true
  }
  return false
}

function remove(idx) {
  pending.value.splice(idx, 1)
}

function onSubmit() {
  if (!canSubmit.value) return
  let content = draft.value.trim()
  if (!content && pending.value.length && props.extractPrompt) {
    content = props.extractPrompt
  }
  const payload = {
    content,
    attachments: [...pending.value],
  }
  draft.value = ''
  pending.value = []
  emit('send', payload)
}

// Phase 31 G14 — listen for suggested-prompt chip clicks dispatched
// from ChatView.  We pre-fill the composer so the user can review and
// edit before sending.
function onPrefillEvent(ev) {
  const text = ev?.detail?.text
  if (typeof text === 'string' && text.length) {
    draft.value = text
  }
}

onMounted(() => {
  window.addEventListener('idp:chat-input:prefill', onPrefillEvent)
})

onBeforeUnmount(() => {
  window.removeEventListener('idp:chat-input:prefill', onPrefillEvent)
})
</script>
