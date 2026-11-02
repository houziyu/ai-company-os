<script setup lang="ts">
import PhasePlaceholderView from './PhasePlaceholderView.vue'
import { findNavEntry } from '@/router/navigation'

const entry = findNavEntry('/projects')
if (!entry) {
  throw new Error('missing navigation entry for /projects')
}
</script>

<template>
  <PhasePlaceholderView :entry="entry" />
</template>
