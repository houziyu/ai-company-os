<script setup lang="ts">
/**
 * ErrorState -- failure with a reason, a retry action and the request id.
 *
 * Used for page-level failures. A toast is never the only evidence of a failed
 * operation, so pages render this inline.
 */
import AppButton from './AppButton.vue'

withDefaults(
  defineProps<{
    title?: string
    description?: string
    requestId?: string | null
    retryLabel?: string
    retrying?: boolean
  }>(),
  {
    title: '加载失败',
    description: '无法获取数据，请稍后重试。',
    requestId: null,
    retryLabel: '重新加载',
    retrying: false,
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="error-state" role="alert">
    <div class="error-state__icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7">
        <path d="M12 8v5" />
        <circle cx="12" cy="16.5" r="0.9" fill="currentColor" stroke="none" />
        <path d="M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      </svg>
    </div>
    <div class="error-state__body">
      <h3 class="error-state__title">{{ title }}</h3>
      <p class="error-state__description">{{ description }}</p>
      <p v-if="requestId" class="error-state__request acos-caption">
        请求编号：<code>{{ requestId }}</code>
      </p>
    </div>
    <AppButton
      v-if="retryLabel"
      variant="secondary"
      :loading="retrying"
      loading-text="重试中…"
      @click="emit('retry')"
    >
      {{ retryLabel }}
    </AppButton>
  </div>
</template>

<style scoped>
.error-state {
  display: flex;
  align-items: flex-start;
  gap: var(--acos-space-16);
  padding: var(--acos-space-20);
  background: var(--acos-color-danger-subtle);
  border: var(--acos-control-border-width) solid var(--acos-color-danger);
  border-radius: var(--acos-radius-card);
  flex-wrap: wrap;
}

.error-state__icon {
  color: var(--acos-color-danger);
  flex: 0 0 auto;
  display: flex;
  padding-top: 2px;
}

.error-state__body {
  flex: 1 1 320px;
  min-width: 0;
}

.error-state__title {
  color: var(--acos-color-danger);
  margin-bottom: var(--acos-space-4);
}

.error-state__description {
  color: var(--acos-color-text-secondary);
}

.error-state__request {
  margin-top: var(--acos-space-4);
}

.error-state__request code {
  font-family: var(--acos-font-mono);
  font-size: var(--acos-font-caption-size);
}
</style>
