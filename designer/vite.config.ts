import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端源可经环境变量覆盖(默认 8901;多副本/隔离冒烟用 FLOWORK_API_ORIGIN 指向其他端口)
const apiOrigin = process.env.FLOWORK_API_ORIGIN ?? 'http://127.0.0.1:8901'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiOrigin, changeOrigin: true },
    },
  },
})

