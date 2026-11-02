<script setup lang="ts">
/**
 * EmptyState -- "nothing here yet" with an explanation and one real action.
 *
 * Rules from COMPONENTS.md: never show a zero value or a fake row to fill space;
 * give the user one actionable next step. When no action exists yet (because a
 * later phase implements it) the component shows a phase note instead of a
 * disabled fake button.
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
    /** Explains why nothing is shown yet, e.g. "阶段 02 开放". */
    phaseNote?: string
    tone?: 'neutral' | 'error'
  }>(),
  { description: '', phaseNote: '', tone: 'neutral' },
)
</script>

<template>
  <div class="empty-state" :class="`empty-state--${tone}`">
    <div class="empty-state__icon" aria-hidden="true">
      <slot name="icon">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6">
          <rect x="3.5" y="5" width="17" height="14" rx="2" />
          <path d="M3.5 10h17" />
        </svg>
      </slot>
    </div>
    <h2 class="empty-state__title">{{ title }}</h2>
    <p class="empty-state__description">{{ description }}</p>
    <p v-if="phaseNote" class="empty-state__phase">{{ phaseNote }}</p>
    <div v-if="$slots.action" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--acos-space-8);
  padding: var(--acos-space-48) var(--acos-space-24);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.empty-state__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--acos-space-48);
  height: var(--acos-space-48);
  margin-bottom: var(--acos-space-4);
  border-radius: var(--acos-radius-pill);
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
}

.empty-state--error .empty-state__icon {
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
}

.empty-state__title {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  color: var(--acos-color-text);
}

.empty-state__description {
  max-width: 52ch;
  color: var(--acos-color-text-secondary);
}

.empty-state__phase {
  max-width: 52ch;
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-muted);
}

.empty-state__action {
  margin-top: var(--acos-space-12);
  display: flex;
  gap: var(--acos-space-8);
}
</style>
