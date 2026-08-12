import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { useMotionStore } from './motion.store'

export const useAppStore = defineStore('app', () => {
  const selectedDeviceId = ref(3)
  const globalNotice = ref<string | null>(null)
  const motionStore = useMotionStore()

  // Compatibility computed values: motion.store.ts remains the only device source.
  const devices = computed(() => motionStore.devices)
  const selectedDevice = computed(() => motionStore.deviceById(selectedDeviceId.value))
  const onlineCount = computed(() => motionStore.onlineCount)
  const offlineCount = computed(() => motionStore.offlineCount)
  const warningCount = computed(() => motionStore.warningCount)

  async function loadDevices(): Promise<void> {
    await motionStore.loadDevices()
  }

  function selectDevice(deviceId: number): void {
    selectedDeviceId.value = deviceId
  }

  return {
    selectedDeviceId,
    globalNotice,
    devices,
    selectedDevice,
    onlineCount,
    offlineCount,
    warningCount,
    loadDevices,
    selectDevice,
  }
})
