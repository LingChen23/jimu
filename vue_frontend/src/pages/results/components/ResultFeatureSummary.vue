<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { MotionStatistics, SpeedStatistics, TremorMetrics } from '@/types/motion'

const props = defineProps<{
  statistics: MotionStatistics | null
  speed: SpeedStatistics | null
  tremor: TremorMetrics | null
}>()

interface FeatureItem {
  key: string
  label: string
  value: string
}

function measurement(value: number | null | undefined, digits: number, unit: string): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(digits)} ${unit}`
}

function spatialRotation(statistics: MotionStatistics | null): string {
  if (!statistics || !Number.isFinite(statistics.spatialRotationTurns) || !Number.isFinite(statistics.spatialRotationDegrees)) return '—'
  return `${statistics.spatialRotationTurns.toFixed(2)} 圈 / ${statistics.spatialRotationDegrees.toFixed(0)}°`
}

const tremorFeatures = computed<FeatureItem[]>(() => [
  { key: 'frequency', label: '震颤频率', value: measurement(props.tremor?.dominantFrequencyHz, 2, 'Hz') },
  { key: 'intensity', label: '震颤强度', value: measurement(props.tremor?.intensityDps, 2, '°/s') },
])

const speedFeatures = computed<FeatureItem[]>(() => [
  { key: 'minimum', label: '最小速度', value: measurement(props.speed?.p05Mps, 4, 'm/s') },
  { key: 'maximum', label: '最大速度', value: measurement(props.speed?.maximumMps, 4, 'm/s') },
  { key: 'average', label: '平均速度', value: measurement(props.speed?.averageMps, 4, 'm/s') },
  { key: 'median', label: '速度中位数', value: measurement(props.speed?.medianMps, 4, 'm/s') },
  { key: 'deviation', label: '速度标准差', value: measurement(props.speed?.standardDeviationMps, 4, 'm/s') },
])

const motionFeatures = computed<FeatureItem[]>(() => {
  const statistics = props.statistics
  return [
    { key: 'moving', label: '移动时间', value: statistics ? `${statistics.movingSeconds.toFixed(1)} 秒` : '—' },
    { key: 'shake', label: '剧烈晃动', value: statistics ? `${statistics.strongShakeCount} 次` : '—' },
    { key: 'rotation', label: '完整一轴旋转', value: statistics ? `${statistics.rotationCount} 次` : '—' },
    { key: 'spatial-rotation', label: '累计空间旋转', value: spatialRotation(statistics) },
    { key: 'flip', label: '翻转', value: statistics ? `${statistics.flipCount} 次` : '—' },
    { key: 'pause-count', label: '停顿（≥0.8秒后恢复）', value: statistics ? `${statistics.pauseCount} 次` : '—' },
    { key: 'pause-time', label: '累计停顿时长', value: statistics ? `${statistics.pauseTotalSeconds.toFixed(1)} 秒` : '—' },
    { key: 'drop', label: '掉落', value: statistics ? `${statistics.dropCount} 次` : '—' },
  ]
})
</script>

<template>
  <section class="card result-feature-summary">
    <div class="result-feature-head">
      <div class="card-title">运动特征</div>
      <StatusBadge :tone="tremor?.valid ? 'green' : 'gray'" :text="tremor?.valid ? '震颤特征可用' : '暂无震颤特征'" />
    </div>

    <div class="result-feature-section">
      <h3>震颤</h3>
      <div class="result-feature-group result-feature-group--primary">
        <article v-for="item in tremorFeatures" :key="item.key"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></article>
      </div>
    </div>

    <div class="result-feature-section">
      <h3>速度特征</h3>
      <div class="result-feature-group result-feature-group--speed">
        <article v-for="item in speedFeatures" :key="item.key"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></article>
      </div>
    </div>

    <div class="result-feature-section">
      <h3>动作特征</h3>
      <div class="result-feature-group result-feature-group--motion">
        <article v-for="item in motionFeatures" :key="item.key"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.result-feature-summary{padding:14px 16px}.result-feature-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.result-feature-section{display:grid;grid-template-columns:54px minmax(0,1fr);align-items:center;gap:8px;margin-top:8px}.result-feature-section h3{margin:0;color:#59667a;font-size:11px}.result-feature-group{display:grid;gap:6px}.result-feature-group--primary{grid-template-columns:repeat(2,minmax(0,1fr))}.result-feature-group--speed{grid-template-columns:repeat(5,minmax(0,1fr))}.result-feature-group--motion{grid-template-columns:repeat(8,minmax(0,1fr))}.result-feature-group article{min-width:0;padding:8px 9px;border:1px solid #e6ebf2;border-radius:8px;background:#f8fafc}.result-feature-group--primary article{border-color:#d8e6f8;background:#f5f9ff}.result-feature-group span{display:block;color:#7b879a;font-size:9px;white-space:nowrap}.result-feature-group strong{display:block;margin-top:4px;color:#354259;font-size:12px;overflow-wrap:anywhere}.result-feature-group--primary strong{color:#1769e8}@media(max-width:1250px){.result-feature-group--motion{grid-template-columns:repeat(4,1fr)}}@media(max-width:900px){.result-feature-group--speed{grid-template-columns:repeat(3,1fr)}}@media(max-width:620px){.result-feature-summary{padding:12px}.result-feature-head{align-items:flex-start;flex-direction:column}.result-feature-section{grid-template-columns:1fr}.result-feature-group--primary,.result-feature-group--speed,.result-feature-group--motion{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
