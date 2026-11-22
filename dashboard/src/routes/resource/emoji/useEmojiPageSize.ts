import { useState } from 'react'

export const emojiPageSizes = {
  desktop: [8, 18, 24],
  mobile: [24, 48, 72, 96],
}

export function useEmojiPageSize(layout: keyof typeof emojiPageSizes) {
  const key = `maibot.emoji.pageSize.${layout}`
  const [size, setSize] = useState(() => {
    const defaultSize = layout === 'desktop' ? 18 : 24
    try {
      const stored = Number(localStorage.getItem(key))
      return emojiPageSizes[layout].includes(stored) ? stored : defaultSize
    } catch {
      return defaultSize
    }
  })

  function selectSize(next: number) {
    if (!emojiPageSizes[layout].includes(next)) return
    setSize(next)
    try {
      localStorage.setItem(key, String(next))
    } catch {
      // 浏览器禁用本地存储时，选择仍在本次浏览中生效。
    }
  }

  return [size, selectSize] as const
}
