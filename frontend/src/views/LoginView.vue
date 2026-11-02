<script setup lang="ts">
/**
 * LoginView.
 *
 * Behaviour:
 * - Submit is disabled while the request is in flight; Enter submits (native
 *   form), so the keyboard path is: Tab to username, Tab to password, Enter.
 * - A wrong password shows one message that does not reveal whether the account
 *   exists; the username stays filled.
 * - An unknown network state is reported as an error, never as "wrong password".
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import FormField from '@/components/FormField.vue'
import AuthLayout from '@/layouts/AuthLayout.vue'
import { ApiError, NetworkError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const formError = ref<string | null>(null)
const fieldErrors = ref<{ username: string | null; password: string | null }>({
  username: null,
  password: null,
})

const canSubmit = computed(
  () =>
    username.value.trim().length > 0 &&
    password.value.length > 0 &&
    !submitting.value,
)

async function submit() {
  if (submitting.value) return

  formError.value = null
  fieldErrors.value = { username: null, password: null }

  if (!username.value.trim()) {
    fieldErrors.value.username = '请输入登录名。'
    return
  }
  if (!password.value) {
    fieldErrors.value.password = '请输入密码。'
    return
  }

  submitting.value = true
  try {
    await session.login(username.value.trim(), password.value)
    const next =
      typeof route.query.next === 'string' ? route.query.next : '/overview'
    await router.replace(next.startsWith('/') ? next : '/overview')
  } catch (error) {
    password.value = ''
    if (error instanceof ApiError) {
      formError.value = error.message
      fieldErrors.value.username = error.fieldErrorFor('username')
    } else if (error instanceof NetworkError) {
      formError.value = error.message
    } else {
      formError.value = '登录时发生未知错误，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthLayout
    title="登录"
    description="使用所有者账号进入中文工作台。"
    footnote="首次部署时先创建所有者账号；系统不提供默认账号或默认密码。"
  >
    <form class="login-form" novalidate @submit.prevent="submit">
      <p v-if="route.query.setup === 'completed'" role="status">
        所有者账号已存在，请直接登录。
      </p>
      <div
        v-if="formError"
        class="login-form__error"
        role="alert"
        data-testid="login-error"
      >
        {{ formError }}
      </div>

      <FormField
        label="登录名"
        field-name="username"
        required
        :error="fieldErrors.username"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="username"
            :described-by="describedBy"
            :invalid="invalid"
            size="large"
            autocomplete="username"
            name="username"
          />
        </template>
      </FormField>

      <FormField
        label="密码"
        field-name="password"
        required
        :error="fieldErrors.password"
      >
        <template #default="{ controlId, describedBy, invalid }">
          <AppInput
            :id="controlId"
            v-model="password"
            type="password"
            :described-by="describedBy"
            :invalid="invalid"
            size="large"
            autocomplete="current-password"
            name="password"
          />
        </template>
      </FormField>

      <AppButton
        type="submit"
        size="large"
        block
        :loading="submitting"
        :disabled="!canSubmit"
        loading-text="登录中…"
      >
        登录
      </AppButton>
    </form>
  </AuthLayout>
</template>

<style scoped>
.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.login-form__error {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}
</style>
