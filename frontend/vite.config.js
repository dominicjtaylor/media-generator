import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // In dev, proxy API calls to the FastAPI backend
    proxy: {
      '/generate': 'http://localhost:8000',
      '/healthz':  'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
