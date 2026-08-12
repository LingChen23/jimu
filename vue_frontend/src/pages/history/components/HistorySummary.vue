<script setup lang="ts">
defineProps<{
  total: number
  latestCount: number
  latestDate: string
  averageScore: number | null
  completedCount: number
}>()
</script>

<template>
  <section class="summary-grid">
    <div class="card summary"><span>总任务数</span><b>{{ total }}</b><small>由 Task Store 提供</small></div>
    <div class="card summary"><span>最新批次任务</span><b>{{ latestCount }}</b><small>{{ latestDate }}</small></div>
    <div class="card summary"><span>平均评分</span><b>{{ averageScore === null ? '—' : averageScore.toFixed(1) }}</b><small>已评分任务</small></div>
    <div class="card summary"><span>已完成任务</span><b>{{ completedCount }}</b><small>按任务状态统计</small></div>
  </section>
</template>
