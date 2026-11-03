<script setup lang="ts">
import AppButton from './AppButton.vue'
defineProps<{ notice: string; warning: boolean; stale: boolean }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="live-status" :class="{ 'live-status--warning': warning }" role="status">
    <span>{{ notice }}</span>
    <AppButton v-if="stale" variant="ghost" size="small" @click="$emit('retry')">重新读取</AppButton>
  </div>
</template>

<style scoped>
.live-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--acos-space-8);
  padding: var(--acos-space-12) var(--acos-space-16);
  color: var(--acos-color-text-secondary);
  background: var(--acos-color-surface-subtle);
  border-radius: var(--acos-radius-control);
}
.live-status--warning {
  color: var(--acos-color-warning);
  background: var(--acos-color-warning-subtle);
}
</style>
