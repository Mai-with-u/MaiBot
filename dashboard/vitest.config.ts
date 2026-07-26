/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

import { dashboardVersionDefine } from './app-version'

export default defineConfig({
  plugins: [react()],
  define: dashboardVersionDefine,
  test: {
    globals: true,
    environment: 'jsdom',
    // Node 22+ 自带实验性 localStorage 全局（未配 --localstorage-file 时为 undefined），
    // 会抢占 globalThis.localStorage 导致 jsdom 的实现不生效，这里显式关闭
    execArgv: ['--no-experimental-webstorage'],
    setupFiles: './src/test/setup.ts',
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
