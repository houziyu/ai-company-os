<script setup lang="ts">
import PhasePlaceholderView from './PhasePlaceholderView.vue'
import { findNavEntry } from '@/router/navigation'

const entry = findNavEntry('/proposals')
if (!entry) {
  throw new Error('missing navigation entry for /proposals')
}
</script>

<template>
  <PhasePlaceholderView :entry="entry" />
</template>
