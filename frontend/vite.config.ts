/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [
    react(),
  ],
  server: {
    proxy: {
      '/picks': 'http://localhost:8000',
      '/stats': 'http://localhost:8000',
      '/backtest': 'http://localhost:8000',
      '/games': 'http://localhost:8000',
      '/props': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/pipeline': 'http://localhost:8000',
      '/users': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
})
