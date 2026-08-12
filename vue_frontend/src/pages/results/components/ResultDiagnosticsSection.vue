<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { MotionEvent, OrientationSnapshot, SpeedStatistics } from '@/types/motion'

defineProps<{
  speed: SpeedStatistics | null
  events: MotionEvent[]
  orientation: OrientationSnapshot | null
}>()

function eventTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
}
function eventTone(event: MotionEvent): 'red' | 'orange' | 'blue' {
  return event.level === 'danger' ? 'red' : event.level === 'warning' ? 'orange' : 'blue'
}
function eventLevel(event: MotionEvent): string {
  return event.level === 'danger' ? '危险' : event.level === 'warning' ? '注意' : '记录'
}
function speedText(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(3)} m/s`
}
</script>

<template>
  <section v-if="speed" class="card speed-review-card">
    <div class="section-head">
      <div><div class="card-title">速度统计</div><div class="card-subtitle">来自完整移动详情</div></div>
      <StatusBadge tone="orange" text="惯导估算" />
    </div>
    <div class="speed-review-grid">
      <div><span>最小速度</span><b>{{ speedText(speed.minimumMps) }}</b></div>
      <div><span>最大速度</span><b>{{ speedText(speed.maximumMps) }}</b></div>
      <div><span>平均速度</span><b>{{ speedText(speed.averageMps) }}</b></div>
      <div><span>中位速度</span><b>{{ speedText(speed.medianMps) }}</b></div>
      <div><span>标准差</span><b>{{ speedText(speed.standardDeviationMps) }}</b></div>
      <div><span>有效样本</span><b>{{ speed.sampleCount }}</b></div>
    </div>
  </section>
  <section v-else class="card result-empty"><strong>速度统计待接入</strong><span>当前任务没有可关联的完整移动记录。</span></section>

  <section class="card table-card event-review-card">
    <div class="section-head">
      <div><div class="card-title">事件时间轴</div><div class="card-subtitle">移动、停顿、晃动、翻转、旋转及疑似掉落</div></div>
      <StatusBadge tone="blue" :text="`${events.length} 条`" />
    </div>
    <div v-if="events.length" class="table-wrap">
      <table><thead><tr><th>时间</th><th>事件</th><th>说明</th><th>级别</th></tr></thead><tbody><tr v-for="event in events" :key="event.id"><td>{{ eventTime(event.timestamp) }}</td><td>{{ event.title }}</td><td>{{ event.description }}</td><td><StatusBadge :tone="eventTone(event)" :text="eventLevel(event)" /></td></tr></tbody></table>
    </div>
    <div v-else class="result-empty-copy">当前结果没有事件记录。</div>
  </section>

  <section v-if="orientation" class="card orientation-review-card">
    <div class="section-head">
      <div><div class="card-title">姿态</div><div class="card-subtitle">相对 Yaw、四元数、位置与速度</div></div>
      <StatusBadge :tone="orientation.initialized ? 'green' : 'gray'" :text="orientation.initialized ? '已初始化' : '未初始化'" />
    </div>
    <div class="orientation-review-grid">
      <div><span>Roll</span><b>{{ orientation.rollDegrees.toFixed(1) }}°</b></div>
      <div><span>Pitch</span><b>{{ orientation.pitchDegrees.toFixed(1) }}°</b></div>
      <div><span>Yaw（相对）</span><b>{{ orientation.yawDegrees.toFixed(1) }}°</b></div>
      <div><span>四元数 W / X / Y / Z</span><b>{{ orientation.quaternion.w.toFixed(4) }} / {{ orientation.quaternion.x.toFixed(4) }} / {{ orientation.quaternion.y.toFixed(4) }} / {{ orientation.quaternion.z.toFixed(4) }}</b></div>
      <div><span>相对位置 X / Y / Z</span><b>{{ orientation.positionM.x.toFixed(3) }} / {{ orientation.positionM.y.toFixed(3) }} / {{ orientation.positionM.z.toFixed(3) }} m</b></div>
      <div><span>速度向量 X / Y / Z</span><b>{{ orientation.velocityMps.x.toFixed(3) }} / {{ orientation.velocityMps.y.toFixed(3) }} / {{ orientation.velocityMps.z.toFixed(3) }} m/s</b></div>
    </div>
  </section>
</template>

<style scoped>
.speed-review-card,.event-review-card,.orientation-review-card{padding:20px}.speed-review-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:1px;margin-top:15px;overflow:hidden;border:1px solid #e4e9f1;border-radius:12px;background:#e4e9f1}.speed-review-grid>div,.orientation-review-grid>div{padding:14px;background:#f8fafc}.speed-review-grid span,.orientation-review-grid span{display:block;color:#7b879a;font-size:11px}.speed-review-grid b,.orientation-review-grid b{display:block;margin-top:6px;font-size:13px;overflow-wrap:anywhere}.orientation-review-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:15px}.orientation-review-grid>div{border:1px solid #e4e9f1;border-radius:11px}.result-empty,.result-empty-copy{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center}.result-empty{min-height:150px;padding:24px;border-style:dashed;box-shadow:none}.result-empty strong{color:#445168}.result-empty-copy{min-height:120px}@media(max-width:1350px){.speed-review-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:1000px){.orientation-review-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){.speed-review-grid,.orientation-review-grid{grid-template-columns:1fr}}
</style>
