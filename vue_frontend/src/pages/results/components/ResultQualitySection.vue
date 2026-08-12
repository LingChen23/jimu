<script setup lang="ts">
import type { SessionTremorAverage, TremorMetrics } from '@/types/motion'

defineProps<{
  tremor: TremorMetrics | null
  sessionTremor: SessionTremorAverage | null
}>()

function nullable(value: number | null | undefined, suffix = '', digits = 2): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(digits)}${suffix}`
}
</script>

<template>
  <section v-if="tremor" class="card tremor-review-card">
      <div class="section-head">
        <div><div class="card-title">震颤审核</div><div class="card-subtitle">频域结果及会话平均</div></div>
        <StatusBadge :tone="tremor.valid ? 'green' : 'gray'" :text="tremor.valid ? '结果有效' : '结果无效'" />
      </div>
      <div class="tremor-primary"><strong>{{ nullable(tremor.dominantFrequencyHz, ' Hz') }}</strong><span>主频 · 分析频段 {{ tremor.frequencyBandHz[0] }}–{{ tremor.frequencyBandHz[1] }} Hz</span></div>
      <div class="tremor-review-grid">
        <div><span>震颤强度</span><b>{{ nullable(tremor.intensityDps, ' °/s') }}</b></div>
        <div><span>频带 RMS</span><b>{{ nullable(tremor.bandRmsDps, ' °/s') }}</b></div>
        <div><span>有效移动</span><b>{{ sessionTremor?.validMovementCount ?? '—' }} / {{ sessionTremor?.totalMovementCount ?? '—' }}</b></div>
        <div><span>会话平均主频</span><b>{{ nullable(sessionTremor?.meanFrequencyHz, ' Hz') }}</b></div>
        <div><span>会话平均强度</span><b>{{ nullable(sessionTremor?.meanIntensityDps, ' °/s') }}</b></div>
      </div>
      <p>{{ tremor.reason ?? sessionTremor?.aggregation ?? '暂无聚合说明' }}</p>
  </section>
</template>

<style scoped>
.tremor-review-card{padding:20px}.tremor-primary{display:flex;align-items:baseline;gap:10px;margin:20px 0}.tremor-primary strong{color:#1769e8;font-size:36px}.tremor-primary span{color:#7b879a;font-size:11px}.tremor-review-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.tremor-review-grid>div{padding:14px;border-radius:10px;background:#f8fafc}.tremor-review-grid span{display:block;color:#7b879a;font-size:11px}.tremor-review-grid b{display:block;margin-top:6px;font-size:13px;overflow-wrap:anywhere}.tremor-review-card p{margin:10px 0 0;color:#7b879a;font-size:11px;line-height:1.6}@media(max-width:680px){.tremor-review-grid{grid-template-columns:1fr}.tremor-primary{align-items:flex-start;flex-direction:column}}
</style>
