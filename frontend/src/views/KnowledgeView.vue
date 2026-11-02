<script setup lang="ts">
import PhasePlaceholderView from './PhasePlaceholderView.vue'
import { findNavEntry } from '@/router/navigation'

const entry = findNavEntry('/knowledge')
if (!entry) {
  throw new Error('missing navigation entry for /knowledge')
}
</script>

<template>
  <PhasePlaceholderView :entry="entry" />
</template>
