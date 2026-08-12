<script setup lang="ts">
defineProps<{ label: string; value: string | number; icon: string; tone?: 'blue' | 'green' | 'orange' | 'purple' | 'red' }>()
</script>
<template><div class="card metric-card"><div class="metric-icon" :class="`metric-icon--${tone ?? 'blue'}`">{{ icon }}</div><div><div class="metric-label">{{ label }}</div><div class="metric-value">{{ value }}</div></div></div></template>
