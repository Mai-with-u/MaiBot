import { defineConfig } from 'electron-vite'
import { mergeConfig } from 'vite'
import path from 'path'

import rendererConfig from './vite.config'

export default defineConfig({
  main: {
    build: {
      target: 'node18',
      rollupOptions: {
        input: {
          index: path.resolve(__dirname, 'electron/main/index.ts'),
        },
        output: {
          format: 'cjs',
        },
        // electron-store 是 ESM-only 且位于 devDependencies，必须打进主进程产物。
        // 只保留 Electron 运行时本身为 external，避免 CJS require ESM 及打包后缺包。
        external: ['electron'],
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  },
  preload: {
    build: {
      target: 'node18',
      rollupOptions: {
        input: {
          index: path.resolve(__dirname, 'electron/preload/index.ts'),
        },
        output: {
          entryFileNames: '[name].js',
          format: 'cjs',
        },
      },
    },
  },
  // Web 与 Electron 共用渲染配置，保持依赖去重、自动分包及开发代理一致。
  renderer: mergeConfig(rendererConfig, {
    root: '.',
    build: {
      rollupOptions: {
        input: path.resolve(__dirname, 'index.html'),
      },
    },
  }),
})
