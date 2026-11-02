<script setup lang="ts">
import { computed } from 'vue'

import type { StatusTone } from '@/styles/tokens'

/**
 * StatusBadge -- status pill with mandatory text.
 *
 * Colour never carries the meaning alone: the label is always rendered, and the
 * dot is decorative. Do not use for arbitrary labels; it represents business
 * status only (task state, employee state, connection state).
 */
const props = withDefaults(
  defineProps<{
    label: string
    tone?: StatusTone
    /** Adds a decorative dot; text remains the accessible signal. */
    showDot?: boolean
  }>(),
  { tone: 'neutral', showDot: true },
)

const classes = computed(() => ['status-badge', `status-badge--${props.tone}`])
</script>

<template>
  <span :class="classes">
    <span v-if="showDot" class="status-badge__dot" aria-hidden="true" />
    <span class="status-badge__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--acos-control-badge-gap);
  height: var(--acos-control-badge-height);
  padding: 0 var(--acos-control-badge-padding-x);
  border-radius: var(--acos-radius-pill);
  font-size: var(--acos-control-badge-font);
  line-height: 1.5;
  font-weight: 500;
  white-space: nowrap;
}

.status-badge__dot {
  width: var(--acos-control-badge-icon);
  height: var(--acos-control-badge-icon);
  border-radius: var(--acos-radius-pill);
  background: currentColor;
  flex: 0 0 auto;
}

.status-badge--neutral {
  background: var(--acos-color-neutral-subtle);
  color: var(--acos-color-neutral);
}

.status-badge--info {
  background: var(--acos-color-info-subtle);
  color: var(--acos-color-info);
}

.status-badge--warning {
  background: var(--acos-color-warning-subtle);
  color: var(--acos-color-warning);
}

.status-badge--success {
  background: var(--acos-color-success-subtle);
  color: var(--acos-color-success);
}

.status-badge--danger {
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
}
</style>
