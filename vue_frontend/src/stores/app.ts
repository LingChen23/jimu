import { ref } from 'vue'
import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', () => {
  const selectedDeviceId = ref(3)
  function selectDevice(deviceId: number): void {
    selectedDeviceId.value = deviceId
  }

  return {
    selectedDeviceId,
    selectDevice,
  }
})
