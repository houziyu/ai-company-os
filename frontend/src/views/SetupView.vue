<script setup lang="ts">
/**
 * SetupView -- first-run owner creation.
 *
 * Only reachable while the server reports setupComplete = false. On success the
 * server also issues the session, so the user lands authenticated in the
 * workbench. Company creation is a separate step in Phase 02 and is not offered
 * here.
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import FormField from '@/components/FormField.vue'
import AuthLayout from '@/layouts/AuthLayout.vue'
import { ApiError, NetworkError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const username = ref('')
const displayName = ref('')
const password = ref('')
const passwordConfirm = ref('')

const submitting = ref(false)
const formError = ref<string | null>(null)
const fieldErrors = ref<Record<string, string | null>>({})

const minLength = computed(() => session.passwordMinLength)

const canSubmit = computed(
  () =>
    username.value.trim().length >= 3 &&
    displayName.value.trim().length > 0 &&
    password.value.length >= minLength.value &&
    passwordConfirm.value.length >= minLength.value &&
    !submitting.value,
)

async function submit() {
  if (submitting.value) return
  formError.value = null
  fieldErrors.value = {}

  if (password.value !== passwordConfirm.value) {
    fieldErrors.value.passwordConfirm = '两次输入的密码不一致。'
    return
  }

  submitting.value = true
  try {
    await session.completeSetup({
      username: username.value.trim(),
      displayName: displayName.value.trim(),
      password: password.value,
      passwordConfirm: passwordConfirm.value,
    })
    await router.replace('/overview')
  } catch (error) {
    if (error instanceof ApiError) {
      formError.value = error.message
      for (const item of error.fieldErrors) {
        fieldErrors.value[item.field] = item.message
      }
      if (error.code === 'SETUP_ALREADY_COMPLETED') {
        session.setupComplete = true
        formError.value = '所有者账号已存在，请直接登录。'
        await router.replace({ path: '/login', query: { setup: 'completed' } })
      }
    } else if (error instanceof NetworkError) {
      formError.value = error.message
    } else {
      formError.value = '初始化时发生未知错误，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthLayout
    eyebrow="首次部署"
    title="创建所有者账号"
    description="本系统为单所有者部署。创建后该账号拥有公司的全部管理权限，请使用强密码并妥善保存。"
    footnote="此步骤只能执行一次；密码使用 Argon2id 哈希存储，不会以明文保存。"
  >
    <form class="setup-form" novalidate @submit.prevent="submit">
      <div
        v-if="formError"
        class="setup-form__error"
        role="alert"
        data-testid="setup-error"
      >
        {{ formError }}
      </div>

      <FormField
        label="登录名"
        field-name="setup-username"
        required
        hint="3 到 64 个字符，可使用字母、数字、下划线、点和连字符。"
        :error="fieldErrors.username ?? null"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="username"
            size="large"
            autocomplete="username"
            name="username"
            :described-by="describedBy"
            :invalid="invalid"
          />
        </template>
      </FormField>

      <FormField
        label="显示名称"
        field-name="setup-display-name"
        required
        hint="显示在工作台右上角和审计记录中。"
        :error="fieldErrors.displayName ?? null"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="displayName"
            size="large"
            autocomplete="name"
            name="displayName"
            :described-by="describedBy"
            :invalid="invalid"
          />
        </template>
      </FormField>

      <FormField
        label="密码"
        field-name="setup-password"
        required
        :hint="`至少 ${minLength} 个字符，不能全是数字，且首尾不能是空白。`"
        :error="fieldErrors.password ?? null"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="password"
            type="password"
            size="large"
            autocomplete="new-password"
            name="password"
            :described-by="describedBy"
            :invalid="invalid"
          />
        </template>
      </FormField>

      <FormField
        label="确认密码"
        field-name="setup-password-confirm"
        required
        :error="fieldErrors.passwordConfirm ?? null"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="passwordConfirm"
            type="password"
            size="large"
            autocomplete="new-password"
            name="passwordConfirm"
            :described-by="describedBy"
            :invalid="invalid"
          />
        </template>
      </FormField>

      <AppButton
        type="submit"
        size="large"
        block
        :loading="submitting"
        :disabled="!canSubmit"
        loading-text="创建中…"
      >
        创建所有者账号
      </AppButton>
    </form>
  </AuthLayout>
</template>

<style scoped>
.setup-form {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.setup-form__error {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}
</style>
