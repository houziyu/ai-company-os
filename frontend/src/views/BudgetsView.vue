<script setup lang="ts">
import PhasePlaceholderView from './PhasePlaceholderView.vue'
import { findNavEntry } from '@/router/navigation'

const entry = findNavEntry('/budgets')
if (!entry) {
  throw new Error('missing navigation entry for /budgets')
}
</script>

<template>
  <PhasePlaceholderView :entry="entry" />
</template>
