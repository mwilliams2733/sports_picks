import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: false, // use public/manifest.json
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png}'],
        runtimeCaching: [
          {
            urlPattern: /^https?:\/\/.*\/(picks|stats|games|props|users)\/.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: { maxAgeSeconds: 300 },
            },
          },
        ],
      },
    }),
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
  }
})
