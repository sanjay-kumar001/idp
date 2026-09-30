<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    @click.self="$emit('close')"
  >
    <div
      class="w-full max-w-md rounded-lg bg-white p-5 shadow-lg dark:bg-gray-900"
    >
      <div class="mb-1 text-base font-semibold text-gray-900 dark:text-gray-100">
        New conversation
      </div>
      <div
        v-if="missingFields.length && !clerkMode"
        class="mb-3 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
      >
        Please pick a value for: {{ missingFields.join(', ') }}.
        Set defaults in <strong>IDP Settings</strong> to skip this dialog
        next time.
      </div>

      <form class="space-y-3" @submit.prevent="onSubmit">
        <label class="block">
          <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
            Title (optional)
          </span>
          <input
            v-model="form.title"
            class="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
            placeholder="e.g. April supplier invoices"
          />
        </label>

        <p
          v-if="clerkMode"
          class="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800"
        >
          Ask an administrator to finish Invoice OCR setup in IDP Settings.
        </p>

        <div v-if="!clerkMode" class="space-y-3">
        <label class="block">
          <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
            Target DocType (optional)
          </span>
          <input
            v-model="form.targetDoctype"
            class="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
            placeholder="Purchase Invoice"
          />
        </label>

        <label class="block">
          <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
            Company (optional)
          </span>
          <input
            v-model="form.company"
            class="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
          />
        </label>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
              LLM provider
            </span>
            <select
              v-model="form.llmProvider"
              class="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
            >
              <option value="">Default</option>
              <option v-for="p in providers" :key="p" :value="p">{{ p }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
              LLM model
            </span>
            <select
              v-model="form.llmModel"
              class="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
            >
              <option value="">Default</option>
              <option v-for="m in filteredModels" :key="m.model_id" :value="m.model_id">
                {{ m.model_id }}
              </option>
            </select>
          </label>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
              OCR language
            </span>
            <input
              v-model="form.ocrLanguage"
              class="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
              placeholder="en"
            />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-300">
              Output language
            </span>
            <input
              v-model="form.outputLanguage"
              class="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
              placeholder="English"
            />
          </label>
        </div>
        </div>

        <div v-if="lastError" class="text-xs text-red-700">{{ lastError }}</div>

        <div class="flex justify-end gap-2 pt-2">
          <button
            type="button"
            class="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
            @click="$emit('close')"
          >
            Cancel
          </button>
          <button
            type="submit"
            :disabled="busy"
            class="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {{ busy ? 'Creating…' : 'Create' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useConversation } from '@/composables/useConversation'
import { listLLMProviders } from '@/utils/api'
import { useSettings } from '@/composables/useSettings'

const { settings } = useSettings()
const clerkMode = computed(() => !!settings.value?.clerk_mode)

const props = defineProps({
  open: { type: Boolean, default: false },
  // Optional snapshot of IDP Settings defaults; used to pre-populate the
  // form so the user only needs to fill in fields that are still empty.
  prefill: { type: Object, default: null },
})

const FIELD_LABELS = {
  llm_provider: 'LLM provider',
  llm_model: 'LLM model',
  target_doctype: 'Target DocType',
}

const missingFields = computed(() => {
  const arr = props.prefill?.missing
  if (!Array.isArray(arr) || !arr.length) return []
  return arr.map((k) => FIELD_LABELS[k] || k)
})

const emit = defineEmits(['close', 'created'])

const { createConversation } = useConversation()
const busy = ref(false)
const lastError = ref(null)

const providers = ref([])
const models = ref([])
const filteredModels = computed(() => {
  if (!form.llmProvider) return models.value
  return models.value.filter((m) => m.provider === form.llmProvider)
})

const form = reactive({
  title: '',
  targetDoctype: '',
  company: '',
  llmProvider: '',
  llmModel: '',
  ocrLanguage: '',
  outputLanguage: '',
})

async function loadProviders() {
  try {
    const out = await listLLMProviders()
    providers.value = Array.isArray(out?.providers) ? out.providers : []
    models.value = Array.isArray(out?.models) ? out.models : []
  } catch (err) {
    // Non-fatal: just leave the dropdowns empty.
    // eslint-disable-next-line no-console
    console.warn('[NewConversationDialog] could not load providers', err)
  }
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      const p = props.prefill || {}
      form.title = ''
      form.targetDoctype = p.target_doctype || ''
      form.company = p.company || ''
      form.llmProvider = p.llm_provider || ''
      form.llmModel = p.llm_model || ''
      form.ocrLanguage = p.ocr_language || ''
      form.outputLanguage = p.output_language || ''
      lastError.value = null
      if (!providers.value.length && !models.value.length) {
        loadProviders()
      }
    }
  },
)

// Reset model when provider changes so we don't ship a mismatched pair.
watch(
  () => form.llmProvider,
  () => {
    if (form.llmModel) {
      const stillValid = filteredModels.value.some(
        (m) => m.model_id === form.llmModel,
      )
      if (!stillValid) form.llmModel = ''
    }
  },
)

async function onSubmit() {
  busy.value = true
  lastError.value = null
  try {
    const out = await createConversation({
      title: form.title || undefined,
      targetDoctype: form.targetDoctype || undefined,
      company: form.company || undefined,
      llmProvider: form.llmProvider || undefined,
      llmModel: form.llmModel || undefined,
      ocrLanguage: form.ocrLanguage || undefined,
      outputLanguage: form.outputLanguage || undefined,
    })
    emit('created', out)
    emit('close')
  } catch (err) {
    lastError.value = err?.message || String(err)
  } finally {
    busy.value = false
  }
}
</script>
