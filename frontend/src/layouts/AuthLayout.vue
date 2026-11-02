<script setup lang="ts">
/**
 * AuthLayout -- the centred 440px card used by /login and /setup.
 *
 * Values come from tokens: card width 440, padding 32 (24 on phones). The page
 * states the product name, the current step and the action, and never shows a
 * default account hint.
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
    /** Shown above the title, e.g. "第 1 步，共 1 步". */
    eyebrow?: string
    footnote?: string
  }>(),
  { description: '', eyebrow: '', footnote: '' },
)
</script>

<template>
  <div class="auth-layout">
    <main class="auth-layout__card">
      <div class="auth-layout__brand">
        <span class="auth-layout__brand-mark" aria-hidden="true">AI</span>
        <span class="auth-layout__brand-name">AI Company OS</span>
      </div>

      <p v-if="eyebrow" class="auth-layout__eyebrow">{{ eyebrow }}</p>
      <h1 class="auth-layout__title">{{ title }}</h1>
      <p v-if="description" class="auth-layout__description">{{ description }}</p>

      <div class="auth-layout__body">
        <slot />
      </div>

      <p v-if="footnote" class="auth-layout__footnote">{{ footnote }}</p>
    </main>
  </div>
</template>

<style scoped>
.auth-layout {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--acos-space-24) var(--acos-space-16);
  background: var(--acos-color-bg);
}

.auth-layout__card {
  width: 100%;
  max-width: var(--acos-layout-login-card);
  padding: var(--acos-layout-login-padding);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  box-shadow: var(--acos-shadow-card);
}

.auth-layout__brand {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  margin-bottom: var(--acos-space-24);
  font-weight: 600;
  font-size: var(--acos-font-subtitle-size);
  color: var(--acos-color-text);
}

.auth-layout__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--acos-space-32);
  height: var(--acos-space-32);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-primary-subtle);
  color: var(--acos-color-primary);
  font-size: var(--acos-font-caption-size);
  font-weight: 700;
}

.auth-layout__eyebrow {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-primary);
  font-weight: 600;
  margin-bottom: var(--acos-space-4);
}

.auth-layout__title {
  margin-bottom: var(--acos-space-8);
}

.auth-layout__description {
  color: var(--acos-color-text-secondary);
  margin-bottom: var(--acos-space-24);
}

.auth-layout__body {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.auth-layout__footnote {
  margin-top: var(--acos-space-24);
  padding-top: var(--acos-space-16);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-muted);
}

@media (max-width: 767px) {
  .auth-layout__card {
    padding: var(--acos-layout-login-mobile-padding);
  }
}
</style>
