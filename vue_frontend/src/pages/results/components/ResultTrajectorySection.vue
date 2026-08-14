<script setup lang="ts">
import type { MovementDetail } from '@/types/motion'
import PcInertialTrajectoryViewer from '@/pages/realtime/components/PcInertialTrajectoryViewer.vue'

defineProps<{
  trajectories: MovementDetail['trajectories'] | null
  events: MovementDetail['events']
}>()
</script>

<template>
  <section v-if="trajectories" class="card result-trajectory-card">
    <PcInertialTrajectoryViewer :trajectory="trajectories.pcInertial" :events="events" analysis />
  </section>
  <section v-else class="card result-empty"><strong>暂无轨迹数据</strong></section>
</template>

<style scoped>
.result-trajectory-card{min-height:520px;padding:0;overflow:hidden}.result-trajectory-card :deep(.trajectory-viewer){border:0;border-radius:inherit}.result-empty{min-height:150px;padding:24px;border-style:dashed;box-shadow:none;display:flex;align-items:center;justify-content:center;color:#7b879a}.result-empty strong{color:#445168}
</style>
