/**
 * Bot 配置模块
 * 
 * 这个模块包含麦麦主程序配置页面的所有组件和类型
 * 
 * 目录结构:
 * - types.ts: 类型定义
 * - hooks/: 自定义 hooks
 *   - useAutoSave.ts: 自动保存 hook
 * - sections/: 各个配置区块组件
 *   - BotInfoSection.tsx
 *   - PersonalitySection.tsx
 *   - ChatSection.tsx
 *   - ...等
 */

// 类型导出
export * from './types'

// Hooks 导出
export * from './hooks'

// Section 组件导出
export * from './sections'
