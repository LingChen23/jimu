<script setup lang="ts">
import { computed } from 'vue'
import type { TaskMovementReference, TaskSummary } from '@/types/task'

const props = defineProps<{
  task: TaskSummary
  selected: TaskMovementReference | null
}>()

const emit = defineEmits<{ select: [reference: TaskMovementReference] }>()

const groups = computed(() => (props.task.deviceIds ?? [props.task.deviceId]).map((deviceId) => {
  const binding = props.task.blockBindings?.find((entry) => entry.deviceId === deviceId)
  const movements = (props.task.movementReferences ?? [])
    .filter((reference) => reference.deviceId === deviceId)
    .sort((left, right) => left.movementId - right.movementId)
  return { deviceId, blockId: binding?.blockId ?? movements[0]?.blockId ?? '', movements }
}))

function selected(reference: TaskMovementReference): boolean {
  return props.selected?.deviceId === reference.deviceId
    && props.selected?.movementId === reference.movementId
    && props.selected?.analysisMode === reference.analysisMode
}
</script>

<template>
  <section class="card task-movement-navigator">
    <div class="section-head">
      <div>
        <div class="card-title">任务移动记录</div>
        <div class="card-subtitle">一次任务覆盖设备 1～10；选择积木的一次完整移动查看详情</div>
      </div>
      <strong>{{ task.movementReferences?.length ?? 0 }} 次移动</strong>
    </div>
    <div class="movement-groups">
      <article v-for="group in groups" :key="group.deviceId" class="movement-group">
        <header><b>积木 {{ group.blockId || '—' }}</b><span>设备 {{ group.deviceId }} · {{ group.movements.length }} 次</span></header>
        <div v-if="group.movements.length" class="movement-buttons">
          <button
            v-for="(reference, index) in group.movements"
            :key="`${reference.deviceId}-${reference.analysisMode}-${reference.movementId}`"
            type="button"
            :class="{ active: selected(reference) }"
            @click="emit('select', reference)"
          >第 {{ reference.deviceMovementIndex ?? index + 1 }} 次</button>
        </div>
        <span v-else class="empty">本次任务未移动</span>
      </article>
    </div>
  </section>
</template>

<style scoped>
.task-movement-navigator{padding:16px}.section-head>strong{color:#1769e8;font-size:13px}.movement-groups{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:12px}.movement-group{min-width:0;padding:10px;border:1px solid #e4e9f1;border-radius:10px;background:#f8fafc}.movement-group header{display:flex;align-items:center;justify-content:space-between;gap:6px}.movement-group header b{font-size:12px}.movement-group header span,.empty{color:#7b879a;font-size:9px}.movement-buttons{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.movement-buttons button{padding:5px 8px;border:1px solid #d9e2ef;border-radius:7px;background:#fff;color:#43516a;font-size:10px;cursor:pointer}.movement-buttons button.active{border-color:#1769e8;background:#1769e8;color:#fff}.empty{display:block;margin-top:10px}@media(max-width:1100px){.movement-groups{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){.movement-groups{grid-template-columns:repeat(2,1fr)}}
</style>
