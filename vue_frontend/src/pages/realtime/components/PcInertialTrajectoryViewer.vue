<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MotionEvent, PcInertialTrajectory } from '@/types/motion'
import {
  prepareTrajectory,
  type TrajectoryView,
} from '@/utils/trajectory'
import { useTrajectoryChart } from '../trajectory/use-trajectory-chart'
import type { TrajectoryInterval, TrajectoryMarker } from '../trajectory/trajectory-chart-options'
import { isTrajectoryIntervalEvent, isTrajectoryMarkerEvent } from '../trajectory/trajectory-event-markers'

const props = withDefaults(defineProps<{
  trajectory: PcInertialTrajectory
  events?: MotionEvent[]
  analysis?: boolean
}>(), { events: () => [], analysis: false })

const selectedView = ref<TrajectoryView>('iso')
const showAngularActionMarkers = ref(false)
const viewOptions: Array<{ value: TrajectoryView; label: string }> = [
  { value: 'iso', label: '三维' },
  { value: 'xy', label: 'XY' },
  { value: 'xz', label: 'XZ' },
  { value: 'yz', label: 'YZ' },
]

const prepared = computed(() => prepareTrajectory(props.trajectory.points))
const points = computed(() => prepared.value.points)
const sourcePoints = computed(() => props.trajectory.points.filter((point) => (
  Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)
)))
const speeds = computed(() => props.analysis
  ? prepared.value.sourceIndexes.map((index) => props.trajectory.points[index]?.speedMps ?? 0)
  : [])
const hasSpeedAnalysis = computed(() => speeds.value.some(Number.isFinite))
const hasAngularActionEvents = computed(() => props.analysis && props.events.some(
  (event) => event.type === 'flip' || event.type === 'rotation',
))
const visibleEvents = computed(() => props.events.filter((event) => (
  showAngularActionMarkers.value || (event.type !== 'flip' && event.type !== 'rotation')
)))
const markerColors: Partial<Record<MotionEvent['type'], string>> = {
  pause: '#64748b', drop: '#dc2626', flip: '#7c3aed', rotation: '#f59e0b',
}
const markerPositions: Partial<Record<MotionEvent['type'], TrajectoryMarker['labelPosition']>> = {
  pause: 'top', drop: 'left', flip: 'bottom', rotation: 'right',
}

function markerDistance(left: TrajectoryMarker, right: TrajectoryMarker): number {
  return Math.hypot(...left.point.map((value, index) => value - right.point[index]))
}

function nearestSourceIndex(timestamp: number): number {
  let best = 0
  let difference = Number.POSITIVE_INFINITY
  sourcePoints.value.forEach((point, index) => {
    const next = Math.abs(point.timestamp - timestamp)
    if (next < difference) { difference = next; best = index }
  })
  return best
}

function nearestDisplayedIndex(sourceIndex: number): number {
  return prepared.value.sourceIndexes.reduce((best, index, current) => (
    Math.abs(index - sourceIndex) < Math.abs(prepared.value.sourceIndexes[best] - sourceIndex) ? current : best
  ), 0)
}

const intervals = computed<TrajectoryInterval[]>(() => {
  if (!props.analysis || !points.value.length) return []
  return visibleEvents.value.flatMap((event) => {
    if (!isTrajectoryIntervalEvent(event)) return []
    const sourceStart = nearestSourceIndex(Number(event.startTimestamp))
    const sourceEnd = nearestSourceIndex(Number(event.endTimestamp))
    const displayedStart = nearestDisplayedIndex(Math.min(sourceStart, sourceEnd))
    const displayedEnd = nearestDisplayedIndex(Math.max(sourceStart, sourceEnd))
    const intervalPoints = points.value.slice(displayedStart, displayedEnd + 1)
    if (intervalPoints.length < 2) return []
    return [{
      eventId: event.id,
      points: intervalPoints,
      title: event.title,
      description: event.description,
      color: markerColors[event.type] ?? '#475569',
    }]
  })
})

const markers = computed<TrajectoryMarker[]>(() => {
  if (!props.analysis || !props.trajectory.points.length) return []
  const source = sourcePoints.value
  const result: TrajectoryMarker[] = visibleEvents.value.flatMap((event) => {
    if (!isTrajectoryMarkerEvent(event)) return []
    const interval = intervals.value.find((item) => item.eventId === event.id)
    const sourceIndex = nearestSourceIndex(event.timestamp)
    const displayedIndex = interval
      ? Math.floor((nearestDisplayedIndex(nearestSourceIndex(Number(event.startTimestamp))) + nearestDisplayedIndex(nearestSourceIndex(Number(event.endTimestamp)))) / 2)
      : nearestDisplayedIndex(sourceIndex)
    const point = points.value[displayedIndex]
    return point ? [{
      point, title: event.title, description: event.description,
      color: markerColors[event.type] ?? '#475569', speedMps: source[sourceIndex]?.speedMps,
      // Keep the drop label at the interval midpoint without drawing a solid
      // event point over the speed-coloured path.
      symbolSize: event.type === 'drop' ? 1 : interval ? 8 : 11,
      labelOnly: event.type === 'drop',
      labelPosition: markerPositions[event.type] ?? 'top',
    }] : []
  })
  const maximum = speeds.value.length ? Math.max(...speeds.value) : Number.NaN
  if (Number.isFinite(maximum)) {
    const displayedIndex = speeds.value.indexOf(maximum)
    const point = points.value[displayedIndex]
    if (point) {
      const maximumMarker: TrajectoryMarker = {
        point, title: '最大速度', description: `${maximum.toFixed(4)} m/s`, color: '#dc2626',
        speedMps: maximum, labelPosition: 'right',
      }
      const existing = result.find((marker) => (
        !marker.labelOnly && markerDistance(marker, maximumMarker) <= 1e-9
      ))
      if (existing) {
        existing.title = `${existing.title} · 最大速度`
        existing.description = `${existing.description}；最大速度 ${maximum.toFixed(4)} m/s`
        existing.speedMps = maximum
      } else {
        result.push(maximumMarker)
      }
    }
  }
  return result
})
const hasTrajectory = computed(() => points.value.length > 0)

const chart = useTrajectoryChart({
  points,
  selectedView,
  hasTrajectory,
  speeds,
  markers,
  intervals,
})

function selectView(view: TrajectoryView): void {
  chart.renderError.value = null
  selectedView.value = view
}

function toggleAngularActionMarkers(): void {
  chart.renderError.value = null
  showAngularActionMarkers.value = !showAngularActionMarkers.value
}
</script>

<template>
  <section class="trajectory-viewer" aria-label="轨迹查看器">
    <div class="trajectory-viewer-head">
      <div class="trajectory-title"><strong>轨迹</strong><span>{{ trajectory.points.length }} 个轨迹点</span></div>
      <div class="trajectory-controls">
        <button
          v-if="hasAngularActionEvents"
          class="trajectory-action-toggle"
          :class="{ active: showAngularActionMarkers }"
          :aria-pressed="showAngularActionMarkers"
          aria-label="显示翻转和完整旋转标记"
          @click="toggleAngularActionMarkers"
        >翻转/旋转</button>
        <div class="trajectory-toggle" role="group" aria-label="轨迹视角">
          <button v-for="option in viewOptions" :key="option.value" :class="{ active: selectedView === option.value }" :aria-pressed="selectedView === option.value" @click="selectView(option.value)">{{ option.label }}</button>
        </div>
      </div>
    </div>

    <div v-if="!hasTrajectory" class="trajectory-empty"><strong>暂无轨迹数据</strong></div>
    <div v-else-if="selectedView === 'iso' && !chart.webglAvailable.value" class="trajectory-empty"><strong>当前浏览器不支持 WebGL</strong></div>
    <div v-else-if="chart.renderError.value" class="trajectory-empty"><strong>轨迹渲染失败</strong></div>
    <div v-else :ref="chart.setChartElement" class="trajectory-chart" role="img" :aria-label="`轨迹，${selectedView === 'iso' ? '三维' : selectedView.toUpperCase()}视图`"></div>
    <div v-if="hasSpeedAnalysis && hasTrajectory" class="trajectory-speed-legend" aria-label="轨迹速度由慢到快">
      <strong>速度</strong><span>慢</span><span class="trajectory-speed-gradient"></span><span>快</span>
    </div>
  </section>
</template>

<style scoped>
.trajectory-viewer{position:relative;height:100%;padding:16px;border:1px solid #dce8f3;border-radius:14px;background:#f8fbff;display:flex;min-height:0;flex-direction:column}.trajectory-viewer-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.trajectory-title{display:flex;align-items:center;gap:8px}.trajectory-title strong{font-size:14px}.trajectory-title span{color:#7b879a;font-size:10px}.trajectory-controls{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}.trajectory-action-toggle{border:1px solid #d5dfeb;border-radius:7px;padding:7px 9px;background:#fff;color:#59667a;font-size:10px;font-weight:800;cursor:pointer}.trajectory-action-toggle.active{border-color:#7c3aed;background:#f3efff;color:#6d28d9}.trajectory-toggle{display:flex;gap:3px;padding:3px;border-radius:8px;background:#eaf0f7}.trajectory-toggle button{border:0;border-radius:6px;padding:6px 8px;background:transparent;color:#59667a;font-size:10px;font-weight:800;cursor:pointer}.trajectory-toggle button.active{background:#1769e8;color:#fff;box-shadow:0 3px 8px rgba(23,105,232,.2)}.trajectory-chart{width:100%;min-height:390px;margin-top:12px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;flex:1 1 auto}.trajectory-speed-legend{position:absolute;right:32px;bottom:28px;z-index:8;display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid #dce4ee;border-radius:8px;background:rgba(255,255,255,.94);box-shadow:0 2px 8px rgba(31,47,70,.08);color:#59667a;font-size:10px;line-height:1;pointer-events:none}.trajectory-speed-legend strong{color:#24324a;font-size:10px}.trajectory-speed-gradient{display:block;width:112px;height:8px;border-radius:999px;background:linear-gradient(90deg,#2563eb 0%,#14b8a6 36%,#f59e0b 70%,#dc2626 100%)}.trajectory-empty{min-height:300px;margin-top:12px;border:1px dashed #d5dfeb;border-radius:10px;background:#fff;display:flex;flex:1 1 auto;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#7b879a}.trajectory-empty strong{color:#354259}@media(max-width:560px){.trajectory-controls,.trajectory-toggle{width:100%}.trajectory-action-toggle{flex:1}.trajectory-toggle button{flex:1;padding:7px 4px}.trajectory-chart{height:320px;min-height:320px;flex:none}.trajectory-speed-legend{right:24px;bottom:24px}.trajectory-speed-gradient{width:72px}}
</style>
