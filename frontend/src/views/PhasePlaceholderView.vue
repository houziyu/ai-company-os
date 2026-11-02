<script setup lang="ts">
/**
 * PhasePlaceholderView -- shared shell for pages whose phase has not arrived.
 *
 * Instead of a fake button, the page states: what it will contain, which phase
 * activates it, and what is available today. This keeps the navigation honest
 * while satisfying the requirement that every nav entry has a real page.
 */
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import AppButton from '@/components/AppButton.vue'
import { useRouter } from 'vue-router'
import type { NavEntry } from '@/router/navigation'
import { phaseNote } from '@/router/navigation'

defineProps<{ entry: NavEntry }>()

const router = useRouter()
</script>

<template>
  <div>
    <PageHeader :title="entry.label" :description="entry.purpose" />
    <EmptyState
      :title="`${entry.label}尚未接入`"
      :description="`本页将在阶段 0${entry.readyPhase} 提供真实数据与操作。当前阶段只验证导航、布局与空状态，不展示模拟内容。`"
      :phase-note="phaseNote(entry)"
    >
      <template #action>
        <AppButton variant="secondary" @click="router.push('/overview')">
          返回公司概览
        </AppButton>
      </template>
    </EmptyState>
  </div>
</template>
