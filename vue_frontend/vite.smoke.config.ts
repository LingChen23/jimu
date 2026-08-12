import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    ssr: fileURLToPath(new URL('./scripts/mock-motion-data-service.smoke.ts', import.meta.url)),
    outDir: '.smoke-dist',
    emptyOutDir: true,
    rollupOptions: {
      output: { entryFileNames: 'mock-motion-data-service.smoke.mjs' },
    },
  },
})
