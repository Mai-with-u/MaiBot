import { createElement } from 'react'

import { StreamlineIcon } from './streamline-icon'

import type { MenuIcon } from '@/components/layout/types'

export function createStreamlineIcon(name: string, fallback?: MenuIcon): MenuIcon {
  return function StreamlineGeneratedIcon({ className, color, size = 20 }) {
    // 与普通图标共用主题选择；千禧风格未登记的名称使用备用图标。
    return createElement(StreamlineIcon, { name, fallback, className, color, size })
  }
}
