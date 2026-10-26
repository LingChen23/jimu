<script setup lang="ts">
import { computed } from 'vue'
import type { ParticipantProfile } from '@/types/task'

const profile = defineModel<ParticipantProfile>('profile', { required: true })
const note = defineModel<string>('note', { required: true })
const props = defineProps<{ participants: ParticipantProfile[] }>()
const emit = defineEmits<{ close: []; confirm: [] }>()

const canConfirm = computed(() => Boolean(
  profile.value.participantId.trim()
  && profile.value.name.trim(),
))

function applyExisting(): void {
  const selected = props.participants.find((item) => (
    item.participantId === profile.value.participantId
  ))
  if (selected) profile.value = { ...selected }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="modal participant-modal">
      <div class="section-head">
        <div><h2>新建采集任务</h2><p>记录体验者信息，并采集十种积木的全部完整移动。</p></div>
        <button class="close" @click="emit('close')">×</button>
      </div>

      <div class="profile-grid">
        <label><span class="field-label">体验者编号 <i class="required">必填</i></span>
          <input v-model="profile.participantId" list="participant-options" @change="applyExisting" />
          <datalist id="participant-options">
            <option v-for="item in participants" :key="item.participantId" :value="item.participantId">{{ item.name }}</option>
          </datalist>
        </label>
        <label><span class="field-label">姓名 <i class="required">必填</i></span><input v-model="profile.name" /></label>
        <label>性别
          <select v-model="profile.gender"><option value="">未填写</option><option>男</option><option>女</option><option>未说明</option></select>
        </label>
        <label>年龄<input v-model.number="profile.age" type="number" min="0" max="130" /></label>
        <label>出生日期<input v-model="profile.birthDate" type="date" /></label>
        <label>文化程度
          <select v-model="profile.educationLevel"><option value="">未填写</option><option>文盲</option><option>小学</option><option>初中</option><option>高中/中专</option><option>大专</option><option>本科</option><option>硕士</option><option>博士</option></select>
        </label>
        <label>利手
          <select v-model="profile.dominantHand"><option value="">未填写</option><option>右手</option><option>左手</option><option>双手</option><option>未知</option></select>
        </label>
        <label>联系电话<input v-model="profile.phone" /></label>
        <label>视力情况<input v-model="profile.visionStatus" placeholder="例如：正常、佩戴眼镜" /></label>
        <label>听力情况<input v-model="profile.hearingStatus" placeholder="例如：正常、佩戴助听器" /></label>
        <label>上肢活动情况<input v-model="profile.upperLimbStatus" placeholder="例如：正常、右肩活动受限" /></label>
        <label>已知认知情况<input v-model="profile.cognitiveStatus" placeholder="选填" /></label>
        <label class="wide">档案备注<textarea v-model="profile.profileNote" rows="2" /></label>
        <label class="wide">本次采集备注<input v-model="note" placeholder="请输入本次采集备注（选填）" /></label>
      </div>

      <div class="modal-actions"><button class="btn secondary" @click="emit('close')">取消</button><button class="btn primary" :disabled="!canConfirm" @click="emit('confirm')">确认并开始</button></div>
    </section>
  </div>
</template>

<style scoped>
.participant-modal{width:min(860px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto}.profile-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.profile-grid label{display:flex;flex-direction:column;gap:7px}.profile-grid .field-label{display:flex;align-items:center;gap:6px}.profile-grid .required{color:#d14343;font-size:10px;font-style:normal;font-weight:700}.profile-grid .wide{grid-column:1/-1}.profile-grid textarea{resize:vertical}@media(max-width:760px){.profile-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:500px){.profile-grid{grid-template-columns:1fr}}
</style>
