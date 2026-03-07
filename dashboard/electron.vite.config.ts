import react from '@vitejs/plugin-react'
import { defineConfig } from 'electron-vite'
import path from 'path'

export default defineConfig({
  main: {
    entry: 'electron/main/index.ts',
    build: {
      target: 'node18',
      lib: {
        entry: 'electron/main/index.ts',
      },
      rollupOptions: {
        external: ['electron', 'electron-store'],
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  },
  preload: {
    entry: 'electron/preload/index.ts',
    build: {
      target: 'node18',
      rollupOptions: {
        input: path.resolve(__dirname, 'electron/preload/index.ts'),
        output: {
          entryFileNames: '[name].js',
          format: 'cjs',
        },
      },
    },
  },
  renderer: {
    root: '.',
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    plugins: [react()],
    server: {
      port: 7999,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8001',
          changeOrigin: true,
          ws: true,
          cookieDomainRewrite: '',
          cookiePathRewrite: '/',
        },
      },
    },
    build: {
      rollupOptions: {
        input: path.resolve(__dirname, 'index.html'),
        output: {
          manualChunks: {
            'react-vendor': ['react', 'react-dom', 'react/jsx-runtime'],

            router: ['@tanstack/react-router', '@tanstack/react-virtual'],

            'radix-core': [
              '@radix-ui/react-dialog',
              '@radix-ui/react-select',
              '@radix-ui/react-checkbox',
              '@radix-ui/react-label',
              '@radix-ui/react-slot',
              '@radix-ui/react-toast',
              '@radix-ui/react-tooltip',
            ],
            'radix-extra': [
              '@radix-ui/react-alert-dialog',
              '@radix-ui/react-avatar',
              '@radix-ui/react-collapsible',
              '@radix-ui/react-context-menu',
              '@radix-ui/react-popover',
              '@radix-ui/react-progress',
              '@radix-ui/react-scroll-area',
              '@radix-ui/react-separator',
              '@radix-ui/react-slider',
              '@radix-ui/react-switch',
              '@radix-ui/react-tabs',
            ],

            icons: ['lucide-react'],

            charts: ['recharts'],

            codemirror: [
              '@uiw/react-codemirror',
              '@codemirror/lang-javascript',
              '@codemirror/lang-json',
              '@codemirror/lang-python',
              '@codemirror/lint',
              '@codemirror/theme-one-dark',
            ],

            reactflow: ['reactflow', 'dagre'],

            markdown: [
              'react-markdown',
              'remark-gfm',
              'remark-math',
              'rehype-katex',
              'katex',
            ],

            uppy: [
              '@uppy/core',
              '@uppy/dashboard',
              '@uppy/react',
              '@uppy/xhr-upload',
            ],

            dnd: ['@dnd-kit/core', '@dnd-kit/sortable', '@dnd-kit/utilities'],

            utils: [
              'date-fns',
              'clsx',
              'tailwind-merge',
              'class-variance-authority',
              'axios',
            ],

            misc: ['react-joyride', 'react-day-picker', 'cmdk'],
          },
        },
      },
      chunkSizeWarningLimit: 500,
    },
  },
})
