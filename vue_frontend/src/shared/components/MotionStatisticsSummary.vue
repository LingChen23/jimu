<script setup lang="ts">
import MetricCard from './MetricCard.vue'
import type { MotionStatistics } from '@/types/motion'

defineProps<{ statistics: MotionStatistics }>()
</script>

<template>
  <section class="review-section">
    <div class="review-section-head">
      <div><div class="card-title">运动统计</div><div class="card-subtitle">完成记录的动作与时长摘要</div></div>
    </div>
    <div class="metric-grid result-metric-grid">
      <MetricCard icon="🚶" label="移动时间" :value="`${statistics.movingSeconds.toFixed(1)}秒`" tone="blue" />
      <MetricCard icon="Ⅱ" label="静止时间" :value="`${statistics.stationarySeconds.toFixed(1)}秒`" tone="green" />
      <MetricCard icon="…" label="停顿" :value="`${statistics.pauseCount}次 / ${statistics.pauseTotalSeconds.toFixed(1)}秒`" tone="blue" />
      <MetricCard icon="⚡" label="剧烈晃动" :value="`${statistics.strongShakeCount}次`" tone="orange" />
      <MetricCard icon="↕" label="翻转" :value="`${statistics.flipCount}次`" tone="purple" />
      <MetricCard icon="⟳" label="旋转" :value="`${statistics.rotationCount}次`" tone="purple" />
      <MetricCard icon="↘" label="掉落" :value="`${statistics.dropCount}次`" tone="red" />
    </div>
  </section>
</template>

<style scoped>
.review-section{display:grid;gap:12px}.review-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.result-metric-grid{grid-template-columns:repeat(4,minmax(150px,1fr))}@media(max-width:1000px){.result-metric-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){.result-metric-grid{grid-template-columns:1fr}}
</style>
