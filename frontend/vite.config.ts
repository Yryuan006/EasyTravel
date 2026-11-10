import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'EASYTRAVEL_')
  return {
    plugins: [vue()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: env.EASYTRAVEL_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
          changeOrigin: true,
          timeout: 5000,
          proxyTimeout: 5000,
        },
      },
    },
    test: { environment: 'jsdom', include: ['tests/**/*.test.ts'], clearMocks: true },
  }
})
