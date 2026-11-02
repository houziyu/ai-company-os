<script setup lang="ts">
/**
 * PageHeader -- page title, description and the single primary action group.
 *
 * PAGES.md allows one primary action group per page; everything else belongs in
 * a filter bar or row actions. The action slot is therefore a single slot.
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
  }>(),
  { description: '' },
)
</script>

<template>
  <header class="page-header">
    <div class="page-header__text">
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="description" class="page-header__description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--acos-space-16);
  margin-bottom: var(--acos-space-24);
  flex-wrap: wrap;
}

.page-header__text {
  min-width: 0;
}

.page-header__title {
  margin: 0;
}

.page-header__description {
  margin-top: var(--acos-space-8);
  color: var(--acos-color-text-secondary);
  max-width: 68ch;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

@media (max-width: 767px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
