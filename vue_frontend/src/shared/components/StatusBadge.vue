<script setup lang="ts">
defineProps<{ tone: 'green' | 'blue' | 'orange' | 'red' | 'gray' | 'purple'; text: string }>()
</script>
<template><span class="badge" :class="`badge--${tone}`">{{ text }}</span></template>
