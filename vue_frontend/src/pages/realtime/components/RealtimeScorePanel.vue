<script setup lang="ts">
import { computed } from 'vue'
import type { TaskSummary } from '@/types/task'
import { getScoreView } from '../realtime.view-model'

const props = defineProps<{
  currentTask: TaskSummary | null
  motionLabel: string
}>()

const score = computed(() => getScoreView(props.currentTask))
</script>

<template>
  <section class="card realtime-score-panel">
    <div class="realtime-score-head">
      <div class="card-title">评分</div>
    </div>

    <div class="realtime-score-status">
      <span>预估评分</span>
      <div v-if="score.demoScore !== null" class="realtime-score-value"><strong>{{ score.demoScore }}</strong><small>分</small></div>
      <strong v-else class="realtime-score-empty">{{ score.title }}</strong>
    </div>

    <div class="realtime-score-summary">
      <div><span>运动状态</span><b>{{ motionLabel }}</b></div>
    </div>
  </section>
</template>

<style scoped>
.realtime-score-panel{width:100%;padding:18px;display:flex;min-height:100%;flex-direction:column}.realtime-score-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.realtime-score-status{display:flex;min-height:clamp(210px,22vw,260px);flex:1 1 auto;flex-direction:column;justify-content:center;margin:10px 0 12px;padding:14px 16px;border:1px solid #e8ddff;border-radius:14px;background:linear-gradient(135deg,#fbf9ff,#f2ecff)}.realtime-score-status>span,.realtime-score-summary span{color:#7b879a;font-size:11px}.realtime-score-value{display:flex;align-items:baseline;gap:6px;margin-top:4px}.realtime-score-value strong{color:#6438c5;font-size:72px;line-height:1;font-variant-numeric:tabular-nums}.realtime-score-value small{color:#7e62bd;font-size:22px;font-weight:800}.realtime-score-empty{margin-top:8px;color:#6438c5;font-size:31px;letter-spacing:.02em}.realtime-score-status p{margin:13px 0 0;color:#625b71;font-size:12px;line-height:1.6}.realtime-score-summary{display:grid;gap:8px}.realtime-score-summary>div{min-width:0;padding:10px 11px;border-radius:9px;background:#f8fafc}.realtime-score-summary b{display:block;margin-top:4px;color:#354259;font-size:13px;line-height:1.5;overflow-wrap:anywhere}@media(max-width:760px){.realtime-score-head{align-items:flex-start;flex-direction:column}}
</style>
