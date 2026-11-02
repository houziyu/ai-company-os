<script setup lang="ts">
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <p class="not-found__code">404</p>
    <h1>页面不存在</h1>
    <p class="not-found__text">该地址没有对应的页面。可能是链接已过期，或地址输入有误。</p>
    <AppButton @click="router.push('/overview')">返回公司概览</AppButton>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 60vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--acos-space-12);
  text-align: center;
  padding: var(--acos-space-32);
}

.not-found__code {
  font-size: var(--acos-font-metric-size);
  line-height: var(--acos-font-metric-line);
  font-weight: var(--acos-font-metric-weight);
  color: var(--acos-color-text-muted);
}

.not-found__text {
  color: var(--acos-color-text-secondary);
  max-width: 44ch;
  margin-bottom: var(--acos-space-8);
}
</style>
