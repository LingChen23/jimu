<script setup lang="ts">
import AxisChart from '@/shared/components/AxisChart.vue'
import MotionStatisticsSummary from '@/shared/components/MotionStatisticsSummary.vue'
import type { AxisPoint, MotionStatistics } from '@/types/motion'

defineProps<{
  statistics: MotionStatistics | null
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
}>()
</script>

<template>
  <MotionStatisticsSummary v-if="statistics" :statistics="statistics" />
  <section v-else class="card result-empty"><strong>运动统计暂无数据</strong><span>等待移动分析结果接入。</span></section>

  <AxisChart v-if="acceleration.length" title="完整三轴加速度曲线" subtitle="" unit="m/s²" :data="acceleration" />
  <section v-else class="card result-empty"><strong>加速度暂无数据</strong><span>未返回可绘制的三轴采样点。</span></section>
  <AxisChart v-if="gyroscope.length" title="完整三轴角速度曲线" subtitle="" unit="°/s" :data="gyroscope" />
  <section v-else class="card result-empty"><strong>角速度暂无数据</strong><span>未返回可绘制的三轴采样点。</span></section>
</template>

<style scoped>
.result-empty{min-height:150px;padding:24px;border-style:dashed;box-shadow:none;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center}.result-empty strong{color:#445168}
</style>
