<script setup lang="ts">
import AxisChart from '@/shared/components/AxisChart.vue'
import MetricCard from '@/shared/components/MetricCard.vue'
import type { AxisPoint, MotionStatistics } from '@/types/motion'

defineProps<{
  statistics: MotionStatistics | null
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
}>()
</script>

<template>
  <section v-if="statistics" class="review-section">
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
      <MetricCard icon="↘" label="掉落 / 疑似" :value="`${statistics.dropCount} / ${statistics.suspectedDropCount}次`" tone="red" />
    </div>
  </section>
  <section v-else class="card result-empty"><strong>运动统计暂无数据</strong><span>等待移动分析结果接入。</span></section>

  <AxisChart v-if="acceleration.length" title="完整三轴加速度曲线" subtitle="" unit="m/s²" :data="acceleration" />
  <section v-else class="card result-empty"><strong>加速度暂无数据</strong><span>未返回可绘制的三轴采样点。</span></section>
  <AxisChart v-if="gyroscope.length" title="完整三轴角速度曲线" subtitle="用于复核翻转、旋转和剧烈晃动事件" unit="°/s" :data="gyroscope" />
  <section v-else class="card result-empty"><strong>角速度暂无数据</strong><span>未返回可绘制的三轴采样点。</span></section>
</template>

<style scoped>
.review-section{display:grid;gap:12px}.review-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.result-metric-grid{grid-template-columns:repeat(4,minmax(150px,1fr))}.result-empty{min-height:150px;padding:24px;border-style:dashed;box-shadow:none;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center}.result-empty strong{color:#445168}@media(max-width:1000px){.result-metric-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){.result-metric-grid{grid-template-columns:1fr}}
</style>
