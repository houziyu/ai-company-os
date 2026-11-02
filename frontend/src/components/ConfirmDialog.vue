<script setup lang="ts">
/**
 * ConfirmDialog -- one blocking confirmation at a time.
 *
 * Requirements from COMPONENTS.md:
 * - Width 480 for confirmations, 640 for forms, capped at viewport - 32.
 * - Cancel left, confirm right; DOM order matches visual order.
 * - Escape closes a non-blocking dialog; focus returns to the trigger (Element
 *   Plus handles focus trapping and restore).
 * - The confirm button reflects the real action text, never a generic "确定".
 */
import { ElDialog } from 'element-plus'

import AppButton from './AppButton.vue'

withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    description?: string
    confirmLabel?: string
    cancelLabel?: string
    variant?: 'primary' | 'danger'
    loading?: boolean
    width?: 480 | 640 | 880
  }>(),
  {
    description: '',
    confirmLabel: '确认',
    cancelLabel: '取消',
    variant: 'primary',
    loading: false,
    width: 480,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: []
  cancel: []
}>()

function close() {
  emit('update:modelValue', false)
  emit('cancel')
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="title"
    :width="width"
    align-center
    :close-on-click-modal="false"
    class="confirm-dialog"
    @update:model-value="emit('update:modelValue', $event)"
    @close="emit('cancel')"
  >
    <div class="confirm-dialog__body">
      <p v-if="description" class="confirm-dialog__description">
        {{ description }}
      </p>
      <slot />
    </div>
    <template #footer>
      <div class="confirm-dialog__footer">
        <AppButton variant="secondary" :disabled="loading" @click="close">
          {{ cancelLabel }}
        </AppButton>
        <AppButton
          :variant="variant"
          :loading="loading"
          :disabled="loading"
          @click="emit('confirm')"
        >
          {{ confirmLabel }}
        </AppButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.confirm-dialog__body {
  color: var(--acos-color-text-secondary);
}

.confirm-dialog__description {
  margin: 0;
}

.confirm-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--acos-control-danger-action-gap);
}

:global(.confirm-dialog.el-dialog) {
  border-radius: var(--acos-radius-dialog);
  max-width: calc(100vw - var(--acos-space-32));
  max-height: var(--acos-layout-dialog-max-height-vh);
  overflow: auto;
}

:global(.confirm-dialog .el-dialog__header) {
  padding: var(--acos-space-24) var(--acos-space-24) 0;
  margin-right: 0;
}

:global(.confirm-dialog .el-dialog__title) {
  font-size: var(--acos-font-section-size);
  line-height: var(--acos-font-section-line);
  font-weight: var(--acos-font-section-weight);
  color: var(--acos-color-text);
}

:global(.confirm-dialog .el-dialog__body) {
  padding: var(--acos-space-16) var(--acos-space-24);
}

:global(.confirm-dialog .el-dialog__footer) {
  padding: var(--acos-space-16) var(--acos-space-24);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

:global(.confirm-dialog .el-dialog__headerbtn) {
  width: var(--acos-control-icon-button);
  height: var(--acos-control-icon-button);
}
</style>
