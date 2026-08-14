<script setup lang="ts">
import { computed } from 'vue'
import type { MotionSnapshot, RealtimeMotionData, MovementDetail } from '@/types/motion'
import type { TaskSummary } from '@/types/task'
import PcInertialTrajectoryViewer from './PcInertialTrajectoryViewer.vue'
import RealtimeScorePanel from './RealtimeScorePanel.vue'

const props = defineProps<{
  isTested: boolean
  loading: boolean
  snapshot: MotionSnapshot | null
  realtime: RealtimeMotionData | null
  currentTask: TaskSummary | null
  completedTask: TaskSummary | null
  completedMovementDetails: MovementDetail[]
  resultLoading: boolean
}>()

const ready = computed(() => Boolean(props.realtime?.available ?? props.snapshot?.available))
</script>

<template>
  <div class="realtime-telemetry-stack">
    <section v-if="!isTested" class="card realtime-empty-state">
      <div class="realtime-empty-icon">⌁</div>
      <div><strong>暂无实时运动数据</strong></div>
    </section>
    <section v-else-if="loading || !ready || !snapshot" class="card realtime-empty-state">
      <div class="realtime-empty-icon">···</div>
      <div><strong>正在同步设备数据</strong></div>
    </section>
    <section v-else class="realtime-primary-grid">
      <PcInertialTrajectoryViewer :trajectory="realtime?.trajectories.pcInertial ?? snapshot.trajectories.pcInertial" />
    </section>
    <div v-if="currentTask || completedTask" id="realtime-collection-result" class="realtime-result-anchor">
      <RealtimeScorePanel
        :current-task="currentTask"
        :completed-task="completedTask"
        :movement-details="completedMovementDetails"
        :result-loading="resultLoading"
      />
    </div>
  </div>
</template>

<style scoped>
.realtime-telemetry-stack{display:flex;min-height:0;flex:1 1 auto;flex-direction:column;gap:12px}.realtime-result-anchor{scroll-margin-top:12px}.realtime-empty-state{min-height:190px;padding:28px;display:flex;align-items:center;justify-content:center;gap:18px;border-style:dashed;background:linear-gradient(135deg,#fff,#f7f9fc);text-align:left}.realtime-empty-icon{width:54px;height:54px;border-radius:16px;display:grid;place-items:center;background:#eef2f6;color:#7b879a;font-size:25px;font-weight:900}.realtime-primary-grid{display:grid;min-height:520px;flex:1 1 auto;grid-template-columns:minmax(0,1fr);align-items:stretch}@media(max-width:900px){.realtime-telemetry-stack{display:block;flex:0 0 auto}.realtime-primary-grid{grid-template-columns:1fr}.realtime-result-anchor{margin-top:12px}}
</style>
