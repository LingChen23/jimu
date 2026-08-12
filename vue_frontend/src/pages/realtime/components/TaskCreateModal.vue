<script setup lang="ts">
const participant = defineModel<string>('participant', { required: true })
const note = defineModel<string>('note', { required: true })
const emit = defineEmits<{ close: []; confirm: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="modal">
      <div class="section-head">
        <div><h2>新建采集任务</h2></div>
        <button class="close" @click="emit('close')">×</button>
      </div>
      <label>体验者编号<input v-model="participant" /></label>
      <label>备注<input v-model="note" placeholder="请输入本次采集备注（选填）" /></label>
      <div class="modal-actions"><button class="btn secondary" @click="emit('close')">取消</button><button class="btn primary" :disabled="!participant.trim()" @click="emit('confirm')">确认并开始</button></div>
    </section>
  </div>
</template>
