<script setup lang="ts">
/**
 * BudgetMeter -- the four independent budget facts, plus a bar.
 *
 * COMPONENTS.md §8 requires 上限 / 已用 / 预留 / 可用 as separate values and says
 * a chart must have text. The bar is decorative: every number is also written
 * out, and a pending-reconciliation count is shown rather than folding unknown
 * usage into "used".
 */
import { computed } from 'vue'

import type { BudgetUsage } from '@/api/task-types'
import { formatTokens } from '@/api/task-presentation'

const props = withDefaults(
  defineProps<{
    budget: BudgetUsage
    label?: string
    /** Hide the limit row for compact in-card use. */
    compact?: boolean
  }>(),
  { label: '', compact: false },
)

const usedPercent = computed(() => {
  const { limitTokens, consumedTokens, reservedTokens } = props.budget
  if (limitTokens <= 0) return 0
  return Math.min(100, ((consumedTokens + reservedTokens) / limitTokens) * 100)
})

const consumedPercent = computed(() => {
  const { limitTokens, consumedTokens } = props.budget
  if (limitTokens <= 0) return 0
  return Math.min(100, (consumedTokens / limitTokens) * 100)
})
</script>

<template>
  <div class="budget-meter">
    <div v-if="label" class="budget-meter__label">{{ label }}</div>
    <dl class="budget-meter__values">
      <div>
        <dt>上限</dt>
        <dd>{{ formatTokens(budget.limitTokens) }}</dd>
      </div>
      <div>
        <dt>已用</dt>
        <dd>{{ formatTokens(budget.consumedTokens) }}</dd>
      </div>
      <div>
        <dt>预留</dt>
        <dd>{{ formatTokens(budget.reservedTokens) }}</dd>
      </div>
      <div>
        <dt>可用</dt>
        <dd>{{ formatTokens(budget.availableTokens) }}</dd>
      </div>
    </dl>
    <div
      class="budget-meter__bar"
      role="img"
      :aria-label="`额度使用 ${Math.round(usedPercent)}%，其中已用 ${Math.round(consumedPercent)}%，预留 ${Math.round(usedPercent - consumedPercent)}%`"
    >
      <span class="budget-meter__bar-consumed" :style="{ width: `${consumedPercent}%` }" />
      <span
        class="budget-meter__bar-reserved"
        :style="{ width: `${Math.max(usedPercent - consumedPercent, 0)}%` }"
      />
    </div>
    <p v-if="budget.needsReconciliationCount > 0" class="budget-meter__pending">
      {{ budget.needsReconciliationCount }} 笔预留的用量未获供应商确认，已按保守额度计入已用，标记为待核对。
    </p>
  </div>
</template>

<style scoped>
.budget-meter {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.budget-meter__label {
  font-size: var(--acos-font-label-size);
  line-height: var(--acos-font-label-line);
  font-weight: var(--acos-font-label-weight);
}

.budget-meter__values {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--acos-space-12);
  margin: 0;
}

.budget-meter__values dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.budget-meter__values dd {
  margin: var(--acos-space-4) 0 0;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  word-break: break-word;
}

.budget-meter__bar {
  display: flex;
  height: var(--acos-space-8);
  border-radius: var(--acos-radius-pill);
  background: var(--acos-color-surface-subtle);
  overflow: hidden;
}

.budget-meter__bar-consumed {
  background: var(--acos-color-primary);
}

.budget-meter__bar-reserved {
  background: var(--acos-color-info-subtle);
  border: var(--acos-control-border-width) solid var(--acos-color-primary);
}

.budget-meter__pending {
  margin: 0;
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-warning);
}

@media (max-width: 767px) {
  .budget-meter__values {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
