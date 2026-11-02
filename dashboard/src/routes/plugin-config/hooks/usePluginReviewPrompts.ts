import { useEffect, useRef, useState } from 'react'

import { getApiBaseUrl } from '@/lib/api-base'
import { getPluginUsageSeconds, type InstalledPlugin } from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'

const REVIEW_PROMPT_MIN_USAGE_SECONDS = 24 * 60 * 60
const REVIEW_PROMPT_USAGE_SPREAD_SECONDS = 96 * 60 * 60
const REVIEW_PROMPT_LABELS = [
  '给插件评分？',
  '喵喵喵打个分？',
  '给插件评论！',
  '喜欢这个插件吗？',
  '用的如何？',
]

interface PluginReviewPrompt {
  label: string
  marketPluginId: string
  storageKey: string
}

function getReviewPromptUsageSeconds(storageKey: string): number {
  // 按实例和插件身份生成固定的伪随机门槛（24～120 小时），避免刷新或重启后重新抽取。
  let hash = 2166136261
  for (let index = 0; index < storageKey.length; index += 1) {
    hash = Math.imul(hash ^ storageKey.charCodeAt(index), 16777619)
  }
  return REVIEW_PROMPT_MIN_USAGE_SECONDS
    + ((hash >>> 0) / 0x100000000) * REVIEW_PROMPT_USAGE_SPREAD_SECONDS
}

export function usePluginReviewPrompts(
  plugins: InstalledPlugin[],
  marketPluginsById: Record<string, PluginInfo>
) {
  const [prompts, setPrompts] = useState<Record<string, PluginReviewPrompt>>({})
  const labels = useRef(new Map<string, string>())

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    void getApiBaseUrl().then((backendUrl) => {
      if (cancelled) return

      const refresh = (usageSeconds: Record<string, number>) => {
        const next: Record<string, PluginReviewPrompt> = {}
        for (const plugin of plugins) {
          if (plugin.load_status !== 'success' || plugin.enabled === false || plugin.disabled) continue

          // 本地只记录已评价状态；旧版保存的首次看到时间不再参与使用时长判断。
          const storageKey = `maibot-plugin-review-prompt:${JSON.stringify([
            backendUrl, plugin.manifest.id || plugin.id, plugin.path,
          ])}`
          if (localStorage.getItem(storageKey) === 'reviewed') continue

          const marketPlugin = marketPluginsById[plugin.manifest.id || plugin.id] || marketPluginsById[plugin.id]
          const usage = usageSeconds[plugin.manifest.id || plugin.id]
          if (!marketPlugin || usage === undefined || usage < getReviewPromptUsageSeconds(storageKey)) continue

          // 每个插件在本次页面访问中固定文案，运行状态刷新时不重新随机。
          let label = labels.current.get(storageKey)
          if (!label) {
            label = REVIEW_PROMPT_LABELS[Math.floor(Math.random() * REVIEW_PROMPT_LABELS.length)]
            labels.current.set(storageKey, label)
          }
          next[plugin.id] = {
            label,
            marketPluginId: marketPlugin.manifest.id || marketPlugin.id,
            storageKey,
          }
        }
        setPrompts(next)
      }

      refresh(Object.fromEntries(plugins
        .filter((plugin) => plugin.usage_seconds !== undefined)
        .map((plugin) => [plugin.manifest.id || plugin.id, plugin.usage_seconds!])
      ))

      // 页面停留期间只轮询轻量时长快照，不在浏览器自行补算运行时间。
      const poll = async () => {
        try {
          const usage = await getPluginUsageSeconds()
          if (!cancelled) refresh(usage)
        } catch (error) {
          console.error('刷新插件运行时长失败:', error)
        } finally {
          if (!cancelled) timer = setTimeout(() => void poll(), 60_000)
        }
      }
      if (plugins.some((plugin) => plugin.load_status === 'success')) {
        timer = setTimeout(() => void poll(), 60_000)
      }
    }).catch((error) => console.error('加载插件评价提醒失败:', error))

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [plugins, marketPluginsById])

  const markReviewed = (storageKey: string) => {
    localStorage.setItem(storageKey, 'reviewed')
    setPrompts((current) => Object.fromEntries(
      Object.entries(current).filter(([, prompt]) => prompt.storageKey !== storageKey)
    ))
  }

  return { reviewPrompts: prompts, markReviewed }
}
