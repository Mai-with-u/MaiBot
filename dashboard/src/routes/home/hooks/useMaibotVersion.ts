/**
 * useMaibotVersion —— 版本信息与一言领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 两段走原生 fetch 的逻辑：
 * - GitHub 最新稳定版（maibotStableRelease，挂载时一次性拉取）
 * - 一言（hitokoto / hitokotoLoading + fetchHitokoto）
 *
 * 设计判断：
 * - 一言与 GitHub 版本都直接走 fetch（非后端 API），故合并为一个 hook，避免碎片化。
 * - fetchHitokoto 依赖 [t]，t 来自 useTranslation 为稳定引用，主 effect 不会反复重建。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { ReleaseStatus } from '../types'

export function useMaibotVersion() {
  const { t } = useTranslation()
  const [hitokoto, setHitokoto] = useState<{ hitokoto: string; from: string } | null>(null)
  const [hitokotoLoading, setHitokotoLoading] = useState(true)
  const [maibotStableRelease, setMaibotStableRelease] = useState<ReleaseStatus | null>(null)

  // 使用 ref 跟踪组件是否已卸载，防止内存泄漏
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  // 挂载时拉取 GitHub 最新稳定版
  useEffect(() => {
    let mounted = true

    const loadLatestVersions = async () => {
      try {
        const response = await fetch('https://api.github.com/repos/Mai-with-u/MaiBot/releases?per_page=20', {
          headers: { Accept: 'application/vnd.github+json' },
        })
        if (!response.ok) {
          throw new Error(`GitHub release status ${response.status}`)
        }
        const releases = await response.json() as Array<{
          draft?: boolean
          prerelease?: boolean
          tag_name?: string
          html_url?: string
        }>
        const visibleReleases = releases.filter((release) => !release.draft)
        const stableRelease = visibleReleases.find((release) => !release.prerelease)
        if (mounted) {
          if (stableRelease?.tag_name) {
            setMaibotStableRelease({
              version: String(stableRelease.tag_name).replace(/^v/i, '').trim(),
              url: stableRelease.html_url || 'https://github.com/Mai-with-u/MaiBot/releases',
            })
          }
        }
      } catch (error) {
        console.debug('检查 MaiBot 最新版本失败:', error)
      }

    }

    void loadLatestVersions()

    return () => {
      mounted = false
    }
  }, [])

  // 获取一言
  const fetchHitokoto = useCallback(async () => {
    try {
      setHitokotoLoading(true)
      const response = await fetch('https://v1.hitokoto.cn/?c=a&c=b&c=c&c=d&c=h&c=i&c=k')
      if (!response.ok) {
        throw new Error(`一言接口返回 HTTP ${response.status}`)
      }
      const data = await response.json()
      if (isMountedRef.current) {
        setHitokoto({
          hitokoto: data.hitokoto,
          from: data.from || data.from_who || t('home.unknownSource')
        })
      }
    } catch (error) {
      console.error('获取一言失败:', error)
      if (isMountedRef.current) {
        setHitokoto({
          hitokoto: t('home.hitokotoFallback'),
          from: t('home.hitokotoFallbackFrom')
        })
      }
    } finally {
      if (isMountedRef.current) {
        setHitokotoLoading(false)
      }
    }
  }, [t])

  return {
    hitokoto,
    hitokotoLoading,
    maibotStableRelease,
    fetchHitokoto,
  }
}
