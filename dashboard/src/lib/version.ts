/**
 * 版本信息模块
 */

// 从 package.json 或环境变量获取版本信息
export const APP_VERSION = import.meta.env.VITE_APP_VERSION || '1.0.0'
export const APP_NAME = import.meta.env.VITE_APP_NAME || 'MaiBot'
export const APP_FULL_NAME = import.meta.env.VITE_APP_FULL_NAME || 'MaiBot WebUI'

/**
 * 格式化版本号显示
 */
export function formatVersion(version: string = APP_VERSION): string {
  return `v${version}`
}
