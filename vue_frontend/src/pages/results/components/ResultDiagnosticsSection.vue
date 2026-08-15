<script setup lang="ts">
import type { SpeedStatistics } from '@/types/motion'

defineProps<{
  speed: SpeedStatistics | null
}>()
function speedText(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(3)} m/s`
}
</script>

<template>
  <section v-if="speed" class="card speed-review-card">
    <div class="section-head">
      <div><div class="card-title">速度统计</div><div class="card-subtitle">来自完整移动详情</div></div>
    </div>
    <div class="speed-review-grid">
      <div><span>最小速度</span><b>{{ speedText(speed.p05Mps) }}</b></div>
      <div><span>最大速度</span><b>{{ speedText(speed.maximumMps) }}</b></div>
      <div><span>平均速度</span><b>{{ speedText(speed.averageMps) }}</b></div>
      <div><span>中位速度</span><b>{{ speedText(speed.medianMps) }}</b></div>
      <div><span>标准差</span><b>{{ speedText(speed.standardDeviationMps) }}</b></div>
    </div>
  </section>
  <section v-else class="card result-empty"><strong>速度统计待接入</strong><span>当前任务没有可关联的完整移动记录。</span></section>

</template>

<style scoped>
.speed-review-card{padding:20px}.speed-review-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;margin-top:15px;overflow:hidden;border:1px solid #e4e9f1;border-radius:12px;background:#e4e9f1}.speed-review-grid>div{padding:14px;background:#f8fafc}.speed-review-grid span{display:block;color:#7b879a;font-size:11px}.speed-review-grid b{display:block;margin-top:6px;font-size:13px;overflow-wrap:anywhere}.result-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center;min-height:150px;padding:24px;border-style:dashed;box-shadow:none}.result-empty strong{color:#445168}@media(max-width:1350px){.speed-review-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){.speed-review-grid{grid-template-columns:1fr}}
</style>
