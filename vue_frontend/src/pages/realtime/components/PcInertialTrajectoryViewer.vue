<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PcInertialTrajectory } from '@/types/motion'
import {
  prepareTrajectory,
  type TrajectoryDisplayMode,
  type TrajectoryView,
} from '@/utils/trajectory'
import { useTrajectoryChart } from '../trajectory/use-trajectory-chart'

const props = defineProps<{ trajectory: PcInertialTrajectory }>()

const displayMode = ref<TrajectoryDisplayMode>('shape')
const selectedView = ref<TrajectoryView>('iso')
const viewOptions: Array<{ value: TrajectoryView; label: string }> = [
  { value: 'iso', label: '三维' },
  { value: 'xy', label: 'XY' },
  { value: 'xz', label: 'XZ' },
  { value: 'yz', label: 'YZ' },
]

const prepared = computed(() => prepareTrajectory(props.trajectory.points, displayMode.value))
const points = computed(() => prepared.value.points)
const hasTrajectory = computed(() => points.value.length > 0)
const displayUnit = computed(() => '无量纲')

const { chartElement, renderError, webglAvailable } = useTrajectoryChart({
  points,
  displayMode,
  selectedView,
  displayUnit,
  hasTrajectory,
})

function selectView(view: TrajectoryView): void {
  renderError.value = null
  selectedView.value = view
}
</script>

<template>
  <section class="trajectory-viewer" aria-label="轨迹查看器">
    <div class="trajectory-viewer-head">
      <div class="trajectory-title"><strong>轨迹</strong><span>{{ trajectory.points.length }} 个轨迹点</span></div>
      <div class="trajectory-controls">
        <div class="trajectory-toggle" role="group" aria-label="轨迹视角">
          <button v-for="option in viewOptions" :key="option.value" :class="{ active: selectedView === option.value }" :aria-pressed="selectedView === option.value" @click="selectView(option.value)">{{ option.label }}</button>
        </div>
      </div>
    </div>

    <div v-if="!hasTrajectory" class="trajectory-empty"><strong>暂无轨迹数据</strong></div>
    <div v-else-if="selectedView === 'iso' && !webglAvailable" class="trajectory-empty"><strong>当前浏览器不支持 WebGL</strong></div>
    <div v-else-if="renderError" class="trajectory-empty"><strong>轨迹渲染失败</strong></div>
    <div v-else ref="chartElement" class="trajectory-chart" role="img" :aria-label="`轨迹，${selectedView === 'iso' ? '三维' : selectedView.toUpperCase()}视图`"></div>
  </section>
</template>

<style scoped>
.trajectory-viewer{height:100%;padding:16px;border:1px solid #dce8f3;border-radius:14px;background:#f8fbff;display:flex;min-height:0;flex-direction:column}.trajectory-viewer-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.trajectory-title{display:flex;align-items:center;gap:8px}.trajectory-title strong{font-size:14px}.trajectory-title span{color:#7b879a;font-size:10px}.trajectory-controls{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}.trajectory-toggle{display:flex;gap:3px;padding:3px;border-radius:8px;background:#eaf0f7}.trajectory-toggle button{border:0;border-radius:6px;padding:6px 8px;background:transparent;color:#59667a;font-size:10px;font-weight:800;cursor:pointer}.trajectory-toggle button.active{background:#1769e8;color:#fff;box-shadow:0 3px 8px rgba(23,105,232,.2)}.trajectory-chart{width:100%;min-height:390px;margin-top:12px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;flex:1 1 auto}.trajectory-empty{min-height:300px;margin-top:12px;border:1px dashed #d5dfeb;border-radius:10px;background:#fff;display:flex;flex:1 1 auto;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#7b879a}.trajectory-empty strong{color:#354259}@media(max-width:560px){.trajectory-controls,.trajectory-toggle{width:100%}.trajectory-toggle button{flex:1;padding:7px 4px}.trajectory-chart{height:320px;min-height:320px;flex:none}}
</style>
