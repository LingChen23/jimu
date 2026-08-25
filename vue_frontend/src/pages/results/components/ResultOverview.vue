<script setup lang="ts">
import { computed, ref } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import { useTaskStore } from '@/stores/task.store'
import type { DeviceSnapshot } from '@/types/device'
import type { AnalysisMode, MovementDetail } from '@/types/motion'
import type { ParticipantProfile, TaskMovementReference, TaskResult, TaskSummary } from '@/types/task'

const props = defineProps<{
  resultKind: 'task' | 'movement'
  taskResult: TaskResult | null
  task: TaskSummary | null
  device: DeviceSnapshot | null
  movement: MovementDetail | null
  movementReference: TaskMovementReference | null
  enrichmentNotice: string | null
}>()
const taskStore = useTaskStore()
const editingProfile = ref(false)
const savingProfile = ref(false)
const profileError = ref<string | null>(null)
const profileDraft = ref<ParticipantProfile | null>(null)

function taskStatus(value: TaskSummary['status'] | undefined): string {
  if (!value) return props.movement?.state === 'completed' ? '移动已完成' : '移动进行中'
  return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value]
}
function analysisMode(value: AnalysisMode | undefined): string {
  if (!value) return '—'
  return value === 'pc' ? 'PC 独立判定' : '设备状态驱动'
}
function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}
function durationText(): string {
  const seconds = props.task?.durationSeconds ?? props.movement?.durationSeconds
  return seconds === null || seconds === undefined ? '—' : `${seconds}秒`
}
function profileText(value: string | number | null | undefined): string {
  return value === null || value === undefined || String(value).trim() === ''
    ? '未填写'
    : String(value)
}
function birthDate(value: string | null | undefined): string {
  if (!value) return '未填写'
  const matched = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  return matched ? `${matched[1]}/${matched[2]}/${matched[3]}` : value
}
const participant = computed<ParticipantProfile | null>(() => props.task?.participantSnapshot ?? null)
const selectedDeviceId = computed(() => props.movement?.deviceId ?? props.movementReference?.deviceId ?? props.task?.deviceId ?? null)
const selectedBlockId = computed(() => {
  if (props.movement) {
    return props.movementReference?.blockId
      ?? props.task?.blockBindings?.find((binding) => binding.deviceId === selectedDeviceId.value)?.blockId
      ?? props.device?.blockId
      ?? null
  }
  return props.task?.blockId || null
})
const displayedScore = computed(() => props.resultKind === 'task'
  ? props.task?.totalScore ?? props.taskResult?.score.totalScore ?? null
  : props.movementReference?.score ?? null)
const canSaveProfile = computed(() => Boolean(
  profileDraft.value?.participantId.trim()
  && profileDraft.value?.name.trim(),
))

function startProfileEdit(): void {
  if (!props.task) return
  profileError.value = null
  profileDraft.value = props.task.participantSnapshot
    ? { ...props.task.participantSnapshot }
    : {
        participantId: props.task.participantId,
        name: '',
        gender: '',
        age: null,
        birthDate: '',
        educationLevel: '',
        dominantHand: '',
        phone: '',
        visionStatus: '',
        hearingStatus: '',
        upperLimbStatus: '',
        cognitiveStatus: '',
        profileNote: '',
      }
  profileDraft.value.participantId = props.task.participantId
  editingProfile.value = true
}

function cancelProfileEdit(): void {
  if (savingProfile.value) return
  editingProfile.value = false
  profileDraft.value = null
  profileError.value = null
}

async function saveProfile(): Promise<void> {
  if (!props.task || !profileDraft.value || !canSaveProfile.value || savingProfile.value) return
  savingProfile.value = true
  profileError.value = null
  try {
    await taskStore.updateTaskParticipant(props.task.taskId, profileDraft.value)
    editingProfile.value = false
    profileDraft.value = null
  } catch (error) {
    profileError.value = error instanceof Error ? error.message : String(error)
  } finally {
    savingProfile.value = false
  }
}
</script>

<template>
  <section v-if="task" class="card result-overview-card">
    <div class="overview-heading">
      <div><h2>体验者档案</h2><p>采集任务开始时保存的体验者资料</p></div>
      <div v-if="editingProfile" class="profile-actions">
        <button class="btn secondary" :disabled="savingProfile" @click="cancelProfileEdit">取消</button>
        <button class="btn primary" :disabled="!canSaveProfile || savingProfile" @click="saveProfile">{{ savingProfile ? '保存中…' : '保存修改' }}</button>
      </div>
      <button v-else class="btn secondary" @click="startProfileEdit">修改档案</button>
    </div>
    <div v-if="editingProfile && profileDraft" class="profile-edit-grid">
      <label>体验者编号<input v-model="profileDraft.participantId" disabled /></label>
      <label><span class="field-label">姓名 <i>必填</i></span><input v-model="profileDraft.name" /></label>
      <label>性别<select v-model="profileDraft.gender"><option value="">未填写</option><option>男</option><option>女</option><option>未说明</option></select></label>
      <label>年龄<input v-model.number="profileDraft.age" type="number" min="0" max="130" /></label>
      <label>出生日期<input v-model="profileDraft.birthDate" type="date" /></label>
      <label>文化程度<select v-model="profileDraft.educationLevel"><option value="">未填写</option><option>文盲</option><option>小学</option><option>初中</option><option>高中/中专</option><option>大专</option><option>本科</option><option>硕士</option><option>博士</option></select></label>
      <label>利手<select v-model="profileDraft.dominantHand"><option value="">未填写</option><option>右手</option><option>左手</option><option>双手</option><option>未知</option></select></label>
      <label>联系电话<input v-model="profileDraft.phone" /></label>
      <label>视力情况<input v-model="profileDraft.visionStatus" /></label>
      <label>听力情况<input v-model="profileDraft.hearingStatus" /></label>
      <label>上肢活动情况<input v-model="profileDraft.upperLimbStatus" /></label>
      <label>已知认知情况<input v-model="profileDraft.cognitiveStatus" /></label>
      <label class="edit-wide">档案备注<textarea v-model="profileDraft.profileNote" rows="2" /></label>
      <p class="edit-wide profile-save-note">保存后将同步更新本任务的档案快照、导出内容和体验者主档案。</p>
      <p v-if="profileError" class="edit-wide profile-error">保存失败：{{ profileError }}</p>
    </div>
    <div v-else class="overview-grid participant-grid">
      <div><span>体验者编号</span><b>{{ profileText(task.participantId) }}</b></div>
      <div><span>姓名</span><b>{{ profileText(participant?.name) }}</b></div>
      <div><span>性别</span><b>{{ profileText(participant?.gender) }}</b></div>
      <div><span>年龄</span><b>{{ profileText(participant?.age) }}</b></div>
      <div><span>出生日期</span><b>{{ birthDate(participant?.birthDate) }}</b></div>
      <div><span>文化程度</span><b>{{ profileText(participant?.educationLevel) }}</b></div>
      <div><span>利手</span><b>{{ profileText(participant?.dominantHand) }}</b></div>
      <div><span>联系电话</span><b>{{ profileText(participant?.phone) }}</b></div>
      <div><span>视力情况</span><b>{{ profileText(participant?.visionStatus) }}</b></div>
      <div><span>听力情况</span><b>{{ profileText(participant?.hearingStatus) }}</b></div>
      <div><span>上肢活动情况</span><b>{{ profileText(participant?.upperLimbStatus) }}</b></div>
      <div><span>已知认知情况</span><b>{{ profileText(participant?.cognitiveStatus) }}</b></div>
      <div class="overview-wide"><span>档案备注</span><b>{{ profileText(participant?.profileNote) }}</b></div>
    </div>
  </section>

  <section class="card result-overview-card">
    <div class="overview-heading">
      <div><h2>任务与移动信息</h2><p>本次采集任务及当前所选移动记录</p></div>
    </div>
    <div class="overview-grid task-grid">
      <div><span>详情类型</span><b>{{ resultKind === 'task' ? '任务结果' : '移动结果' }}</b></div>
      <div><span>任务编号</span><b>{{ task?.taskId ?? '未关联任务' }}</b></div>
      <div><span>任务开始时间</span><b>{{ dateTime(task?.startTime ?? movement?.startedAt) }}</b></div>
      <div><span>任务结束时间</span><b>{{ dateTime(task?.endTime ?? movement?.endedAt) }}</b></div>
      <div><span>任务持续时间</span><b>{{ durationText() }}</b></div>
      <div><span>记录状态</span><b>{{ taskStatus(task?.status) }}</b></div>
      <div><span>当前移动硬件设备ID</span><b>{{ selectedDeviceId ?? '—' }}</b></div>
      <div><span>当前移动积木编号</span><b>{{ selectedBlockId ?? '—' }}</b></div>
      <div><span>当前移动编号</span><b>{{ movement ? `M${movement.movementId}` : '未关联移动' }}</b></div>
      <div><span>分析模式</span><b>{{ analysisMode(movement?.analysisMode) }}</b></div>
      <div class="overview-wide"><span>本次采集备注</span><b>{{ task?.note || '未填写' }}</b></div>
    </div>
  </section>

  <section v-if="enrichmentNotice" class="card result-source-note">
    <StatusBadge tone="gray" text="数据提示" />
    <span>{{ enrichmentNotice }}</span>
  </section>

  <section class="card score result-score-card">
      <span>{{ resultKind === 'task' ? '本次任务评分' : '本次移动评分' }}</span>
      <strong>{{ displayedScore ?? '—' }}<small v-if="displayedScore !== null">分</small></strong>
      <StatusBadge :tone="displayedScore !== null ? 'green' : 'gray'" :text="displayedScore !== null ? '已评分' : '未评分'" />
  </section>
</template>

<style scoped>
.result-overview-card{padding:18px}.overview-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.overview-heading h2{font-size:16px;margin:0}.overview-heading p{color:var(--gray);font-size:11px;margin:4px 0 0}.profile-actions{display:flex;align-items:center;gap:8px}.overview-grid,.profile-edit-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.overview-grid>div{min-width:0;padding:12px;border:1px solid #edf1f6;border-radius:11px;background:#f8fafc}.overview-grid span{display:block;color:var(--gray);font-size:11px;margin-bottom:6px}.overview-grid b{display:block;font-size:13px;line-height:1.55;overflow-wrap:anywhere;white-space:pre-wrap}.overview-grid .overview-wide,.profile-edit-grid .edit-wide{grid-column:1/-1}.overview-grid .overview-wide{min-height:60px}.profile-edit-grid label{display:flex;min-width:0;flex-direction:column;gap:7px;color:#59667a;font-size:12px}.profile-edit-grid input:disabled{background:#eef2f7;color:#7b879a;cursor:not-allowed}.profile-edit-grid textarea{resize:vertical}.field-label{display:flex;align-items:center;gap:6px}.field-label i{color:#d14343;font-size:10px;font-style:normal;font-weight:700}.profile-save-note,.profile-error{margin:0;padding:10px 12px;border-radius:9px;font-size:11px}.profile-save-note{color:#68758b;background:#f6f8fb}.profile-error{color:#b53f3f;background:#fff1f1}.result-source-note{padding:14px 18px;display:flex;align-items:center;gap:10px;color:#68758b;font-size:12px}.result-score-card{min-height:150px}@media(max-width:1350px){.overview-grid,.profile-edit-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:900px){.overview-grid,.profile-edit-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.overview-heading{align-items:flex-start;flex-direction:column}.profile-actions{width:100%}.profile-actions .btn{flex:1}.overview-grid,.profile-edit-grid{grid-template-columns:1fr}.result-source-note{align-items:flex-start;flex-direction:column}}
</style>
