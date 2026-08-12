<script setup lang="ts">
import { computed } from 'vue'
import type { MotionSnapshot, RealtimeMotionData } from '@/types/motion'
import type { TaskSummary } from '@/types/task'
import PcInertialTrajectoryViewer from './PcInertialTrajectoryViewer.vue'
import RealtimeScorePanel from './RealtimeScorePanel.vue'

const props = defineProps<{
  isTested: boolean
  loading: boolean
  snapshot: MotionSnapshot | null
  realtime: RealtimeMotionData | null
  currentTask: TaskSummary | null
  motionLabel: string
}>()

const ready = computed(() => Boolean(props.snapshot?.available))
</script>

<template>
  <section v-if="!isTested" class="card realtime-empty-state">
    <div class="realtime-empty-icon">⌁</div>
    <div><strong>暂无实时运动数据</strong></div>
  </section>
  <section v-else-if="loading || !ready" class="card realtime-empty-state">
    <div class="realtime-empty-icon">···</div>
    <div><strong>正在同步设备数据</strong></div>
  </section>
  <div v-else-if="snapshot" class="realtime-telemetry-stack">
    <section class="realtime-primary-grid">
      <PcInertialTrajectoryViewer :trajectory="realtime?.trajectories.pcInertial ?? snapshot.trajectories.pcInertial" />
      <RealtimeScorePanel :current-task="currentTask" :motion-label="motionLabel" />
    </section>

  </div>
</template>

<style scoped>
.realtime-telemetry-stack{display:flex;min-height:0;flex:1 1 auto}.realtime-empty-state{min-height:190px;padding:28px;display:flex;align-items:center;justify-content:center;gap:18px;border-style:dashed;background:linear-gradient(135deg,#fff,#f7f9fc);text-align:left}.realtime-empty-icon{width:54px;height:54px;border-radius:16px;display:grid;place-items:center;background:#eef2f6;color:#7b879a;font-size:25px;font-weight:900}.realtime-primary-grid{display:grid;min-height:520px;flex:1 1 auto;grid-template-columns:minmax(0,2fr) minmax(300px,.9fr);gap:12px;align-items:stretch}@media(max-width:900px){.realtime-telemetry-stack{display:block;flex:0 0 auto}.realtime-primary-grid{grid-template-columns:1fr}}
</style>
