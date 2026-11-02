<script setup lang="ts">
/**
 * AppDrawer -- side panel for detail views.
 *
 * Width 560 by default, 800 for wide content, full-screen on phones. Title and
 * footer stay fixed while the body scrolls, per COMPONENTS.md.
 */
import { ElDrawer } from 'element-plus'

withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    size?: 'default' | 'wide'
  }>(),
  { size: 'default' },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()
</script>

<template>
  <ElDrawer
    :model-value="modelValue"
    :title="title"
    :size="size === 'wide' ? 'var(--acos-layout-drawer-wide)' : 'var(--acos-layout-drawer)'"
    class="app-drawer"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header="{ titleId, titleClass }">
      <h2 :id="titleId" :class="titleClass" class="app-drawer__title">{{ title }}</h2>
    </template>
    <slot />
    <template v-if="$slots.footer" #footer>
      <div class="app-drawer__footer">
        <slot name="footer" />
      </div>
    </template>
  </ElDrawer>
</template>

<style scoped>
.app-drawer__title {
  font-size: var(--acos-font-section-size);
  line-height: var(--acos-font-section-line);
  font-weight: var(--acos-font-section-weight);
  color: var(--acos-color-text);
  margin: 0;
}

.app-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--acos-control-gap);
}

:deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: var(--acos-space-20) var(--acos-space-24);
  border-bottom: var(--acos-control-border-width) solid var(--acos-color-border);
}

:deep(.el-drawer__body) {
  padding: var(--acos-space-24);
}

:deep(.el-drawer__footer) {
  padding: var(--acos-space-16) var(--acos-space-24);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

:deep(.el-drawer__close-btn) {
  width: var(--acos-control-icon-button);
  height: var(--acos-control-icon-button);
}

@media (max-width: 767px) {
  /* The drawer root is teleported: it is not a descendant of our scoped
     element. Override the width input on this named public component only. */
  :global(.app-drawer.el-drawer) {
    --acos-layout-drawer: 100%;
    --acos-layout-drawer-wide: 100%;
  }
}
</style>
