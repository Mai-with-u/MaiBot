// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type { PluginLoadProgress } from './types'
import { clearPluginProgress, mergePluginProgress } from './types'

function updateProgress(pluginId: string, progress: number): PluginLoadProgress {
  return {
    operation: 'update',
    stage: progress === 100 ? 'success' : 'loading',
    progress,
    message: `更新进度 ${progress}%`,
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: progress === 100 ? 1 : 0,
  }
}

describe('插件操作进度', () => {
  it('按插件 ID 保留多个插件各自的进度', () => {
    const firstProgress = updateProgress('plugin-a', 30)
    const secondProgress = updateProgress('plugin-b', 10)

    const progressById = mergePluginProgress(
      mergePluginProgress({}, firstProgress),
      secondProgress
    )

    expect(progressById['plugin-a']).toBe(firstProgress)
    expect(progressById['plugin-b']).toBe(secondProgress)
  })

  it('旧完成状态的延迟清理不会删除同插件的新进度', () => {
    const completedProgress = updateProgress('plugin-a', 100)
    const restartedProgress = updateProgress('plugin-a', 5)
    const progressById = mergePluginProgress(
      mergePluginProgress({}, completedProgress),
      restartedProgress
    )

    expect(clearPluginProgress(progressById, 'plugin-a', completedProgress)).toBe(progressById)
    expect(progressById['plugin-a']).toBe(restartedProgress)
  })
})
