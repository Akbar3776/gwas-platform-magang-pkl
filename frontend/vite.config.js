import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy /api dan /ws ke backend FastAPI agar tidak kena isu CORS/WebSocket saat dev.
// Sesuaikan target jika port backend berbeda (lihat infra/docker-compose.yml).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
