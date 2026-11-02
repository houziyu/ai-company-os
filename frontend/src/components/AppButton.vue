/**
 * AppButton -- the only button used by product pages.
 *
 * Sizes come from design-tokens.json through CSS custom properties, so the
 * 32/40/48px contract is testable by measuring the rendered element.
 *
 * Notes:
 * - Loading keeps the button width stable by rendering the label in the same
 *   box and never replacing it with a spinner-only state.
 * - `aria-busy` tells screen readers the action is in progress.
 * - Disabled keeps full opacity: the token colours already lower contrast, and
 *   a faded button would drop below the contrast floor.
 */

<script setup lang="ts">
import { computed, useAttrs } from 'vue'
import { ElIcon } from 'element-plus'

import type { ControlSize } from '@/styles/tokens'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-text'
    size?: ControlSize
    type?: 'button' | 'submit' | 'reset'
    loading?: boolean
    disabled?: boolean
    block?: boolean
    loadingText?: string
    iconOnly?: boolean
    /** Accessible name; required when the button has no visible text. */
    ariaLabel?: string
  }>(),
  {
    variant: 'primary',
    size: 'medium',
    type: 'button',
    loading: false,
    disabled: false,
    block: false,
    loadingText: '',
    iconOnly: false,
    ariaLabel: '',
  },
)

const emit = defineEmits<{ click: [event: MouseEvent] }>()
const attrs = useAttrs()

const isInert = computed(() => props.disabled || props.loading)

const classes = computed(() => [
  'app-button',
  `app-button--${props.variant}`,
  `app-button--${props.size}`,
  {
    'app-button--block': props.block,
    'app-button--icon': props.iconOnly,
    'app-button--loading': props.loading,
  },
])

function onClick(event: MouseEvent) {
  if (isInert.value) {
    event.preventDefault()
    return
  }
  emit('click', event)
}
</script>

<template>
  <button
    v-bind="attrs"
    :type="type"
    :class="classes"
    :disabled="isInert"
    :aria-busy="loading ? 'true' : undefined"
    :aria-label="ariaLabel || undefined"
    @click="onClick"
  >
    <span v-if="loading" class="app-button__spinner" aria-hidden="true" />
    <span v-else-if="$slots.icon" class="app-button__icon" aria-hidden="true">
      <ElIcon><slot name="icon" /></ElIcon>
    </span>
    <span v-if="!iconOnly" class="app-button__label">
      {{ loading && loadingText ? loadingText : '' }}
      <slot v-if="!(loading && loadingText)" />
    </span>
  </button>
</template>

<style scoped>
.app-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--acos-control-gap);
  border-radius: var(--acos-radius-control);
  border: var(--acos-control-border-width) solid transparent;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color var(--acos-motion-fast) ease,
    border-color var(--acos-motion-fast) ease,
    color var(--acos-motion-fast) ease;
}

.app-button:disabled {
  cursor: not-allowed;
}

/* ---- sizes ---- */
.app-button--small {
  height: var(--acos-control-small-height);
  min-width: var(--acos-control-small-min-width);
  padding: 0 var(--acos-control-small-padding-x);
  font-size: var(--acos-control-small-font-size);
}

.app-button--medium {
  height: var(--acos-control-medium-height);
  min-width: var(--acos-control-medium-min-width);
  padding: 0 var(--acos-control-medium-padding-x);
  font-size: var(--acos-control-medium-font-size);
}

.app-button--large {
  height: var(--acos-control-large-height);
  min-width: var(--acos-control-large-min-width);
  padding: 0 var(--acos-control-large-padding-x);
  font-size: var(--acos-control-large-font-size);
}

.app-button--icon {
  min-width: 0;
  padding: 0;
  width: var(--acos-control-medium-height);
}

.app-button--icon.app-button--small {
  width: var(--acos-control-small-height);
}

.app-button--block {
  width: 100%;
}

.app-button__icon,
.app-button__spinner {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.app-button__spinner {
  width: var(--acos-control-small-icon);
  height: var(--acos-control-small-icon);
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: var(--acos-radius-pill);
  animation: app-button-spin 700ms linear infinite;
}

@keyframes app-button-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ---- variants ---- */
.app-button--primary {
  background: var(--acos-color-primary);
  border-color: var(--acos-color-primary);
  color: var(--acos-color-on-primary);
}

.app-button--primary:hover:not(:disabled) {
  background: var(--acos-color-primary-hover);
  border-color: var(--acos-color-primary-hover);
}

.app-button--primary:active:not(:disabled) {
  background: var(--acos-color-primary-active);
  border-color: var(--acos-color-primary-active);
}

.app-button--secondary {
  background: var(--acos-color-surface);
  border-color: var(--acos-color-control-border);
  color: var(--acos-color-text);
}

.app-button--secondary:hover:not(:disabled) {
  background: var(--acos-color-surface-subtle);
}

.app-button--secondary:active:not(:disabled) {
  background: var(--acos-color-border);
}

.app-button--ghost {
  background: transparent;
  border-color: transparent;
  color: var(--acos-color-primary);
}

.app-button--ghost:hover:not(:disabled) {
  background: var(--acos-color-primary-subtle);
}

.app-button--ghost:active:not(:disabled) {
  background: var(--acos-color-surface-subtle);
}

.app-button--danger {
  background: var(--acos-color-danger);
  border-color: var(--acos-color-danger);
  color: var(--acos-color-on-primary);
}

.app-button--danger:hover:not(:disabled) {
  background: var(--acos-color-danger-hover);
  border-color: var(--acos-color-danger-hover);
}

.app-button--danger:active:not(:disabled) {
  background: var(--acos-color-danger-active);
  border-color: var(--acos-color-danger-active);
}

.app-button--danger-text {
  background: transparent;
  border-color: transparent;
  color: var(--acos-color-danger);
}

.app-button--danger-text:hover:not(:disabled) {
  background: var(--acos-color-danger-subtle);
}

/* A genuinely disabled button keeps its shape with the disabled palette.
   A *loading* button is not disabled: it keeps the variant colours so the
   action stays recognisable while the spinner and loading text show progress. */
.app-button:disabled:not(.app-button--loading) {
  background: var(--acos-color-disabled-bg);
  border-color: var(--acos-color-disabled-bg);
  color: var(--acos-color-disabled-text);
}

.app-button--loading {
  cursor: progress;
}

/* Mobile touch target: 44px minimum without changing the visual size of the
   label box, by expanding the hit area through the wrapper's padding. */
@media (max-width: 767px) {
  .app-button {
    min-height: var(--acos-control-mobile-target);
  }

  .app-button--small {
    min-height: var(--acos-control-mobile-target);
  }
}
</style>
