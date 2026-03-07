/**
 * MaiBot Dashboard 版本管理
 * 
 * 这是唯一需要修改版本号的地方
 * 修改此处的版本号后，所有展示版本的地方都会自动更新
 */

export const APP_VERSION = '1.0.0'
export const APP_NAME = 'MaiBot Dashboard'
export const APP_FULL_NAME = `${APP_NAME} v${APP_VERSION}`

/**
 * 获取版本信息
 */
export const getVersionInfo = () => ({
  version: APP_VERSION,
  name: APP_NAME,
  fullName: APP_FULL_NAME,
  buildDate: import.meta.env.VITE_BUILD_DATE || new Date().toISOString().split('T')[0],
  buildEnv: import.meta.env.MODE,
})

/**
 * 格式化版本显示
 */
export const formatVersion = (prefix = 'v') => `${prefix}${APP_VERSION}`
