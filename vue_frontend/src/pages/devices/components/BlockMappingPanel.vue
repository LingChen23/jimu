<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { BlockMapping } from '@/types/device'

const props = defineProps<{ mapping: BlockMapping | null; saving: boolean }>()
const emit = defineEmits<{ save: [mapping: BlockMapping] }>()
const values = ref<Record<number, string>>({})
const blocks = Array.from({ length: 12 }, (_, index) => String.fromCharCode(65 + index))

watch(() => props.mapping, (mapping) => {
  values.value = Object.fromEntries(mapping?.devices.map((entry) => [entry.deviceId, entry.blockId]) ?? [])
}, { immediate: true, deep: true })

const complete = computed(() => Array.from({ length: 12 }, (_, index) => values.value[index + 1]).every(Boolean))
const unique = computed(() => new Set(Object.values(values.value)).size === 12)
const valid = computed(() => complete.value && unique.value)

function save(): void {
  if (!props.mapping || !valid.value) return
  const timestamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)
  emit('save', {
    mappingVersion: `vue-${timestamp}`,
    editableSource: props.mapping.editableSource,
    devices: Array.from({ length: 12 }, (_, index) => ({ deviceId: index + 1, blockId: values.value[index + 1] })),
  })
}
</script>

<template>
  <section class="card mapping-panel">
    <div class="section-head">
      <div><div class="card-title">设备与积木绑定</div><div class="card-subtitle">设备 1～12 必须与积木 A～L 一一对应；新映射只影响后续移动</div></div>
      <div class="mapping-actions"><StatusBadge v-if="!valid" tone="red" text="存在重复绑定" /><button class="btn primary" :disabled="saving || !valid" @click="save">{{ saving ? '保存中…' : '保存绑定' }}</button></div>
    </div>
    <div class="mapping-grid">
      <label v-for="deviceId in 12" :key="deviceId"><span>设备 {{ deviceId }}</span><select v-model="values[deviceId]"><option v-for="block in blocks" :key="block" :value="block">积木 {{ block }}</option></select></label>
    </div>
  </section>
</template>

<style scoped>
.mapping-panel{padding:15px 16px}.mapping-actions{display:flex;align-items:center;gap:8px}.mapping-actions .btn{padding:8px 12px}.mapping-grid{display:grid;grid-template-columns:repeat(12,minmax(72px,1fr));gap:7px;margin-top:12px}.mapping-grid label{display:grid;gap:4px}.mapping-grid span{color:#7b879a;font-size:9px}.mapping-grid select{min-width:0;padding:8px 7px;font-size:11px}@media(max-width:1400px){.mapping-grid{grid-template-columns:repeat(6,1fr)}}@media(max-width:760px){.section-head{align-items:flex-start;flex-direction:column}.mapping-actions{width:100%;justify-content:space-between}.mapping-grid{grid-template-columns:repeat(3,1fr)}}
</style>
