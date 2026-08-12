<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { MovementDetail, TrajectoryPoint } from '@/types/motion'

defineProps<{ trajectories: MovementDetail['trajectories'] | null }>()

function trajectoryPolyline(points: TrajectoryPoint[]): string {
  if (!points.length) return ''
  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const rangeX = maxX - minX || 1
  const rangeY = maxY - minY || 1
  return points.map((point) => {
    const x = 18 + ((point.x - minX) / rangeX) * 284
    const y = 160 - ((point.y - minY) / rangeY) * 140
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
</script>

<template>
  <section v-if="trajectories" class="trajectory-review-grid">
    <article class="card trajectory-review-card">
      <div class="section-head">
        <div><div class="card-title">设备轨迹</div><div class="card-subtitle">{{ trajectories.device.coordinateSystem }} · {{ trajectories.device.unit }}</div></div>
        <StatusBadge tone="orange" text="相对坐标" />
      </div>
      <svg viewBox="0 0 320 180" role="img" aria-label="设备轨迹二维审核投影"><path d="M18 20V160H302" /><line x1="18" y1="90" x2="302" y2="90" /><polyline :points="trajectoryPolyline(trajectories.device.points)" /></svg>
      <p>{{ trajectories.device.points.length }} 个轨迹点；仅作二维审核投影，不代表正式三维轨迹。</p>
    </article>
    <article class="card trajectory-review-card trajectory-review-card--pc">
      <div class="section-head">
        <div><div class="card-title">PC 惯导轨迹</div><div class="card-subtitle">{{ trajectories.pcInertial.coordinateSystem }} · {{ trajectories.pcInertial.unit }}</div></div>
        <StatusBadge tone="blue" text="非可靠度量" />
      </div>
      <svg viewBox="0 0 320 180" role="img" aria-label="PC惯导轨迹二维审核投影"><path d="M18 20V160H302" /><line x1="18" y1="90" x2="302" y2="90" /><polyline :points="trajectoryPolyline(trajectories.pcInertial.points)" /></svg>
      <p>{{ trajectories.pcInertial.points.length }} 个轨迹点；米制结果仍标记为非可靠度量。</p>
    </article>
  </section>
  <section v-else class="card result-empty"><strong>轨迹数据待接入</strong><span>当前结果没有可展示的设备或 PC 惯导轨迹。</span></section>
</template>

<style scoped>
.trajectory-review-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.trajectory-review-card{padding:18px}.trajectory-review-card svg{width:100%;height:240px;margin-top:12px;border-radius:11px;background:#f8fbff}.trajectory-review-card svg path,.trajectory-review-card svg line{fill:none;stroke:#dce5ef;stroke-width:1}.trajectory-review-card svg polyline{fill:none;stroke:#ed8b22;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.trajectory-review-card--pc svg polyline{stroke:#1769e8}.trajectory-review-card p{margin:10px 0 0;color:#7b879a;font-size:11px;line-height:1.6}.result-empty{min-height:150px;padding:24px;border-style:dashed;box-shadow:none;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center}.result-empty strong{color:#445168}@media(max-width:1000px){.trajectory-review-grid{grid-template-columns:1fr}}
</style>
