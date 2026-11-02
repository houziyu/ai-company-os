<script setup lang="ts">
/**
 * FormField -- persistent label, optional hint and error message.
 *
 * COMPONENTS.md: the label sits above the field, the error is associated with
 * the control through `aria-describedby` (ids are generated here), and the
 * placeholder never replaces the label. The slot receives the ids so the child
 * control can wire them without duplicating logic.
 */
import { computed, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    fieldName: string
    hint?: string
    error?: string | null
    required?: boolean
    /** Hide the visible label but keep it available to assistive tech. */
    labelHidden?: boolean
  }>(),
  { hint: '', error: null, required: false, labelHidden: false },
)

const id = useId()
const labelId = `${id}-label`
const hintId = `${id}-hint`
const errorId = `${id}-error`
const controlId = computed(() => `field-${props.fieldName}`)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(hintId)
  if (props.error) ids.push(errorId)
  return ids.length ? ids.join(' ') : undefined
})
</script>

<template>
  <div class="form-field" :class="{ 'form-field--error': !!error }">
    <label
      :id="labelId"
      :for="controlId"
      class="form-field__label"
      :class="{ 'acos-visually-hidden': labelHidden }"
    >
      {{ label }}
      <span v-if="required" class="form-field__required" aria-hidden="true">*</span>
      <span v-if="required" class="acos-visually-hidden">（必填）</span>
    </label>
    <slot
      :control-id="controlId"
      :described-by="describedBy"
      :invalid="!!error"
      :label-id="labelId"
    />
    <p v-if="hint && !error" :id="hintId" class="form-field__hint">{{ hint }}</p>
    <p v-if="error" :id="errorId" class="form-field__error" role="alert">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.form-field {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.form-field__label {
  font-size: var(--acos-font-label-size);
  line-height: var(--acos-font-label-line);
  font-weight: var(--acos-font-label-weight);
  color: var(--acos-color-text);
}

.form-field__required {
  color: var(--acos-color-danger);
  margin-left: var(--acos-space-4);
}

.form-field__hint {
  margin: 0;
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.form-field__error {
  margin: 0;
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-danger);
}
</style>
