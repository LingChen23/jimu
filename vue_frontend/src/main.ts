import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/global.css'

function reportPageError(): void {
  if (!['127.0.0.1', 'localhost'].includes(location.hostname)) return
  const section = location.pathname.split('/')[1]
  const operation = ['realtime', 'history', 'results', 'devices'].includes(section)
    ? section : 'browser_page'
  void fetch('/api/diagnostics/event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: 'page_render_failed', operation }),
    keepalive: true,
  }).catch(() => {})
}

window.addEventListener('error', reportPageError)
window.addEventListener('unhandledrejection', reportPageError)

const app = createApp(App)
app.config.errorHandler = reportPageError
app.use(createPinia()).use(router).mount('#app')
