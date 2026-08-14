<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { isPresentationRoute, isWorkspaceRoute } from './app-layout-route'

const route = useRoute()
const sidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')

watch(sidebarCollapsed, (collapsed) => {
  localStorage.setItem('sidebar-collapsed', String(collapsed))
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'app-shell--presentation': isPresentationRoute(route.path),
      'app-shell--workspace': isWorkspaceRoute(route.path),
      'app-shell--sidebar-collapsed': sidebarCollapsed,
    }"
  >
    <aside class="sidebar">
      <button
        class="sidebar-toggle"
        type="button"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        @click="sidebarCollapsed = !sidebarCollapsed"
      >
        <svg class="toggle-icon" :class="{ 'toggle-icon--reversed': sidebarCollapsed }" viewBox="0 0 20 20" aria-hidden="true">
          <path d="m12.5 5-5 5 5 5" />
        </svg>
      </button>
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m12 3 7 4-7 4-7-4 7-4Z" />
            <path d="m5 12 7 4 7-4M5 17l7 4 7-4" />
          </svg>
        </div>
        <div class="brand-copy"><div class="brand-title">积木运动数据采集</div><div class="brand-subtitle">与评分系统</div></div>
      </div>
      <nav class="side-nav">
        <RouterLink to="/realtime" class="nav-item" title="实时采集">
          <span class="nav-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V5M4 19h16M7 15l3-4 3 2 5-6" /><path d="m15 7h3v3" /></svg></span>
          <span class="nav-label">实时采集</span>
        </RouterLink>
        <RouterLink to="/history" class="nav-item" title="历史记录">
          <span class="nav-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2M5.8 5.8 4 4" /></svg></span>
          <span class="nav-label">历史记录</span>
        </RouterLink>
        <RouterLink to="/devices" class="nav-item" title="设备状态">
          <span class="nav-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4" /><circle cx="12" cy="12" r="2.5" /></svg></span>
          <span class="nav-label">设备状态</span>
        </RouterLink>
      </nav>
      <div class="sidebar-footer"><div><span class="footer-dot">●</span><span class="footer-label">系统运行中</span></div></div>
    </aside>
    <section class="main-area">
      <main class="content"><RouterView /></main>
    </section>
  </div>
</template>
