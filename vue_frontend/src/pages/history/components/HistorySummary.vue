<script setup lang="ts">
defineProps<{
  total: number
  latestCount: number
  latestDate: string
  averageScore: number | null
  completedCount: number
  totalCaption?: string
  latestLabel?: string
  averageCaption?: string
  completedLabel?: string
  completedCaption?: string
}>()
</script>

<template>
  <section class="summary-grid">
    <div class="card summary"><span>总任务数</span><b>{{ total }}</b><small>{{ totalCaption ?? '由 Task Store 提供' }}</small></div>
    <div class="card summary"><span>{{ latestLabel ?? '最新批次任务' }}</span><b>{{ latestCount }}</b><small>{{ latestDate }}</small></div>
    <div class="card summary"><span>平均评分</span><b>{{ averageScore === null ? '—' : averageScore.toFixed(1) }}</b><small>{{ averageCaption ?? '已评分任务' }}</small></div>
    <div class="card summary"><span>{{ completedLabel ?? '已完成任务' }}</span><b>{{ completedCount }}</b><small>{{ completedCaption ?? '按任务状态统计' }}</small></div>
  </section>
</template>
