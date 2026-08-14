<script setup lang="ts">
import { computed } from 'vue'
import ResultFeatureSummary from '@/pages/results/components/ResultFeatureSummary.vue'
import MotionStatisticsSummary from '@/shared/components/MotionStatisticsSummary.vue'
import type { MovementDetail } from '@/types/motion'
import type { TaskSummary } from '@/types/task'
import { aggregateTaskMovements } from '../task-result-aggregate'

const props = defineProps<{
  currentTask: TaskSummary | null
  completedTask: TaskSummary | null
  movementDetails: MovementDetail[]
  resultLoading: boolean
}>()

const result = computed(() => props.completedTask)
const movements = computed(() => result.value?.movementReferences ?? [])
const aggregate = computed(() => aggregateTaskMovements(props.movementDetails))
const totalMovementSeconds = computed(() => movements.value.reduce(
  (sum, item) => sum + (item.durationSeconds ?? 0),
  0,
))
const movedBlockCount = computed(() => new Set(movements.value.map((item) => item.deviceId)).size)

function formatSeconds(value: number): string {
  return `${value.toFixed(1)} 秒`
}
</script>

<template>
  <div class="collection-result-stack">
    <section class="card collection-result-panel">
      <div class="section-head">
        <div>
          <div class="card-title">采集结果</div>
          <div class="card-subtitle">任务结束后生成本次采集的总评分和全部完整移动汇总</div>
        </div>
      </div>

      <div v-if="currentTask" class="collection-result-pending">
        <strong>正在采集</strong>
        <span>结束采集后显示评分和详细数据结果</span>
      </div>

      <template v-else-if="result">
        <div class="result-overview">
          <article class="score-card">
            <span>本次采集评分</span>
            <div v-if="result.totalScore !== null"><strong>{{ result.totalScore }}</strong><small>分</small></div>
            <b v-else>暂无评分</b>
          </article>
          <article><span>完整移动</span><b>{{ movements.length }} 次</b></article>
          <article><span>移动积木</span><b>{{ movedBlockCount }} 块</b></article>
          <article><span>累计移动时长</span><b>{{ formatSeconds(totalMovementSeconds) }}</b></article>
          <article><span>任务持续时长</span><b>{{ result.durationSeconds ?? 0 }} 秒</b></article>
        </div>
        <div v-if="resultLoading" class="result-loading-note">正在加载全部移动的详细结果…</div>
        <div v-if="movements.length && movementDetails.length < movements.length" class="partial-result-note">
          已加载 {{ movementDetails.length }} / {{ movements.length }} 次完整移动的详细数据。
        </div>
        <div v-else-if="!movements.length" class="no-movement-result">本次采集未记录到完整移动，因此不生成评分。</div>
      </template>
    </section>

    <template v-if="result && movements.length">
      <ResultFeatureSummary
        :statistics="aggregate.statistics"
        :speed="aggregate.speed"
        :tremor="aggregate.tremor"
      />
      <MotionStatisticsSummary
        v-if="aggregate.statistics"
        class="collection-motion-statistics"
        :statistics="aggregate.statistics"
      />
    </template>
  </div>
</template>

<style scoped>
.collection-result-stack{display:grid;gap:12px}.collection-result-panel{padding:18px}.collection-result-pending{min-height:130px;margin-top:12px;border:1px dashed #d5dfeb;border-radius:12px;background:#f8fafc;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;color:#7b879a}.collection-result-pending strong{color:#354259;font-size:20px}.result-overview{display:grid;grid-template-columns:1.25fr repeat(4,minmax(0,1fr));gap:10px;margin-top:14px}.result-overview article{min-height:88px;padding:14px;border:1px solid #e3eaf3;border-radius:11px;background:#f8fafc;display:flex;justify-content:center;flex-direction:column}.result-overview span{color:#7b879a;font-size:11px}.result-overview b{margin-top:7px;color:#1d2b42;font-size:20px}.score-card{background:linear-gradient(135deg,#fbf9ff,#f2ecff)!important;border-color:#e8ddff!important}.score-card>div{display:flex;align-items:baseline;gap:5px}.score-card strong{color:#6438c5;font-size:42px;line-height:1}.score-card small{color:#7e62bd;font-weight:800}.result-loading-note,.partial-result-note,.no-movement-result{margin-top:12px;padding:10px 12px;border-radius:9px;background:#f8fafc;color:#7b879a;font-size:11px;text-align:center}.result-loading-note{background:#eef6ff;color:#1769e8}.collection-motion-statistics{padding:16px;border-radius:14px;background:#fff;box-shadow:var(--shadow)}@media(max-width:1000px){.result-overview{grid-template-columns:repeat(2,1fr)}.score-card{grid-column:span 2}}@media(max-width:650px){.result-overview{grid-template-columns:1fr}.score-card{grid-column:auto}}
</style>
