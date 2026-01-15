import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 7999,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',  // WebUI 后端服务器
        changeOrigin: true,
        ws: true,
        // 确保 Cookie 正确转发
        cookieDomainRewrite: '',  // 移除域名限制
        cookiePathRewrite: '/',   // 确保路径一致
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // React 核心库
          'react-vendor': ['react', 'react-dom', 'react/jsx-runtime'],
          
          // TanStack Router
          'router': ['@tanstack/react-router', '@tanstack/react-virtual'],
          
          // Radix UI 组件库（按使用频率分组）
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
          
          // 图标库
          'icons': ['lucide-react'],
          
          // 图表库
          'charts': ['recharts'],
          
          // CodeMirror 编辑器（较大，单独分包）
          'codemirror': [
            '@uiw/react-codemirror',
            '@codemirror/lang-javascript',
            '@codemirror/lang-json',
            '@codemirror/lang-python',
            '@codemirror/lint',
            '@codemirror/theme-one-dark',
          ],
          
          // ReactFlow 流程图（较大，单独分包）
          'reactflow': ['reactflow', 'dagre'],
          
          // Markdown 渲染（较大，单独分包）
          'markdown': [
            'react-markdown',
            'remark-gfm',
            'remark-math',
            'rehype-katex',
            'katex',
          ],
          
          // 文件上传（Uppy）
          'uppy': [
            '@uppy/core',
            '@uppy/dashboard',
            '@uppy/react',
            '@uppy/xhr-upload',
          ],
          
          // 拖拽功能
          'dnd': [
            '@dnd-kit/core',
            '@dnd-kit/sortable',
            '@dnd-kit/utilities',
          ],
          
          // 工具库
          'utils': [
            'date-fns',
            'clsx',
            'tailwind-merge',
            'class-variance-authority',
            'axios',
            'jotai',
          ],
          
          // 其他
          'misc': [
            'react-joyride',
            'react-day-picker',
            'cmdk',
          ],
        },
      },
    },
    chunkSizeWarningLimit: 500, // 降低警告阈值，便于发现大块
  },
})
