/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The app is served by poketokenweb's static handler out of web_root
// (/app/web by default), which marks only content-hashed files under
// assets/ immutable — Vite's default output layout already matches that.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', assetsDir: 'assets', sourcemap: false },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/sprites': 'http://127.0.0.1:8080',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
