import { createRouter, createWebHistory } from 'vue-router'

const RealtimePage = () => import('@/pages/realtime/RealtimePage.vue')
const HistoryPage = () => import('@/pages/history/HistoryPage.vue')
const DeviceStatusPage = () => import('@/pages/devices/DeviceStatusPage.vue')
const ResultPage = () => import('@/pages/results/ResultPage.vue')
const CloudHistoryPage = () => import('@/pages/cloud/CloudHistoryPage.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/realtime' },
    { path: '/realtime', component: RealtimePage, meta: { title: '实时采集' } },
    { path: '/history', component: HistoryPage, meta: { title: '历史记录' } },
    { path: '/devices', component: DeviceStatusPage, meta: { title: '设备状态' } },
    {
      path: '/results/movements/:deviceId/:analysisMode/:movementId',
      name: 'movement-result',
      component: ResultPage,
      meta: { title: '移动结果详情' },
    },
    {
      path: '/results/:taskId',
      name: 'task-result',
      component: ResultPage,
      meta: { title: '任务结果详情' },
    },
    {
      path: '/cloud-history',
      name: 'cloud-history',
      component: CloudHistoryPage,
      meta: { title: '云端历史记录', standalone: true },
    },
    {
      path: '/cloud-history/tasks/:taskId',
      name: 'cloud-task-result',
      component: ResultPage,
      meta: { title: '云端任务结果详情', standalone: true, cloud: true },
    },
  ]
})

router.afterEach((to) => {
  if (to.path === '/history' || to.path === '/cloud-history') {
    void ResultPage().catch(() => { /* Navigation can retry if background loading fails. */ })
  }
})

export default router
