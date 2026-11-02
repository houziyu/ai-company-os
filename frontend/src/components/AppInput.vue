<script setup lang="ts">
/**
 * AppInput -- text/password input matching the token control sizes.
 *
 * Wraps a native <input> rather than ElInput: Element's internals set their own
 * heights, and the 32/40/48px contract must be measurable directly.
 */
import { computed, ref } from 'vue'

import type { ControlSize } from '@/styles/tokens'

const props = withDefaults(
  defineProps<{
    modelValue: string
    id?: string
    type?: 'text' | 'password' | 'email'
    size?: ControlSize
    placeholder?: string
    disabled?: boolean
    invalid?: boolean
    autocomplete?: string
    describedBy?: string
    name?: string
  }>(),
  {
    id: undefined,
    type: 'text',
    size: 'medium',
    placeholder: '',
    disabled: false,
    invalid: false,
    autocomplete: 'off',
    describedBy: undefined,
    name: undefined,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  blur: []
}>()

const inputRef = ref<HTMLInputElement | null>(null)

/** Let forms focus the first invalid control without reaching into the DOM. */
function focus() {
  inputRef.value?.focus()
}

defineExpose({ focus })

const classes = computed(() => [
  'app-input',
  `app-input--${props.size}`,
  { 'app-input--invalid': props.invalid },
])

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <input
    :id="id"
    ref="inputRef"
    :name="name"
    :type="type"
    :value="modelValue"
    :placeholder="placeholder || undefined"
    :disabled="disabled"
    :autocomplete="autocomplete"
    :aria-invalid="invalid ? 'true' : undefined"
    :aria-describedby="describedBy"
    :class="classes"
    @input="onInput"
    @blur="emit('blur')"
  />
</template>

<style scoped>
.app-input {
  width: 100%;
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  font-family: inherit;
  font-size: var(--acos-font-body-size);
  transition: border-color var(--acos-motion-fast) ease;
}

.app-input--small {
  height: var(--acos-control-small-height);
  padding: 0 var(--acos-control-input-padding-x);
}

.app-input--medium {
  height: var(--acos-control-medium-height);
  padding: 0 var(--acos-control-input-padding-x);
}

.app-input--large {
  height: var(--acos-control-large-height);
  padding: 0 var(--acos-control-medium-padding-x);
  font-size: var(--acos-font-subtitle-size);
}

.app-input::placeholder {
  color: var(--acos-color-text-muted);
}

.app-input:hover:not(:disabled) {
  border-color: var(--acos-color-text-secondary);
}

.app-input:focus-visible {
  border-color: var(--acos-color-focus);
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

.app-input--invalid {
  border-color: var(--acos-color-danger);
}

.app-input:disabled {
  background: var(--acos-color-disabled-bg);
  border-color: var(--acos-color-border);
  color: var(--acos-color-disabled-text);
  cursor: not-allowed;
}

/* Mobile: 16px prevents iOS Safari from zooming on focus. */
@media (max-width: 767px) {
  .app-input {
    font-size: 16px;
    min-height: var(--acos-control-mobile-target);
  }
}
</style>
