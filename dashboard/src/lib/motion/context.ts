import { createContext } from 'react'

import type { DashboardStyle } from '@/lib/theme/tokens'
import type { MotionProfile, ResolvedMotionTransitions } from './profiles'

export interface MotionRuntime {
  readonly style: DashboardStyle
  readonly enabled: boolean
  readonly reduced: boolean
  readonly speedScale: number
  readonly profile: MotionProfile
  readonly transitions: ResolvedMotionTransitions
  /** 按序号计算交错延迟（秒），超过风格上限后封顶，防止长列表拖沓 */
  readonly staggerDelay: (index: number) => number
  /** 按当前速度倍率与开关缩放时长（秒） */
  readonly duration: (seconds: number) => number
}

export const MotionContext = createContext<MotionRuntime | undefined>(undefined)
