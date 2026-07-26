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
    // 覆盖率：v8 provider，只统计 src 业务代码
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/__tests__/**',
        'src/test/**',
        'src/types/**',
        'src/**/*.d.ts',
        'src/main.tsx',
        'src/assets/**',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
