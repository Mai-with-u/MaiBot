import type { Transition } from 'motion/react'

import type { DashboardStyle } from '@/lib/theme/tokens'

export type BezierTuple = readonly [number, number, number, number]

export interface SpringConfig {
  readonly type: 'spring'
  readonly stiffness: number
  readonly damping: number
  readonly mass: number
}

export interface MotionDurations {
  readonly instant: number
  readonly fast: number
  readonly normal: number
  readonly slow: number
  readonly exit: number
  readonly collapse: number
}

export interface MotionDistances {
  readonly pageY: number
  readonly tabX: number
  readonly tabY: number
  readonly overlayY: number
  readonly overlayScale: number
  readonly dialogY: number
  readonly dialogScale: number
  readonly listItemY: number
  readonly tapScale: number
  readonly tapY: number
  readonly hoverLiftY: number
  readonly stagger: number
  readonly maxStaggerItems: number
}

export interface MotionProfile {
  readonly style: DashboardStyle
  readonly label: string
  readonly spring: SpringConfig
  readonly snappySpring: SpringConfig
  readonly gentleSpring: SpringConfig
  readonly ease: BezierTuple
  readonly exitEase: BezierTuple
  readonly collapseEase: BezierTuple
  readonly easeCss: string
  readonly exitEaseCss: string
  readonly collapseEaseCss: string
  readonly durations: MotionDurations
  readonly distances: MotionDistances
}

export interface ResolvedMotionTransitions {
  readonly instant: Transition
  readonly fast: Transition
  readonly normal: Transition
  readonly slow: Transition
  readonly exit: Transition
  readonly collapse: Transition
  readonly spring: Transition
  readonly snappy: Transition
  readonly gentle: Transition
  readonly page: Transition
  readonly tab: Transition
  readonly overlay: Transition
  readonly dialog: Transition
}

function toCubicBezierCss(curve: BezierTuple): string {
  return `cubic-bezier(${curve[0]}, ${curve[1]}, ${curve[2]}, ${curve[3]})`
}

const MODERN_EASE: BezierTuple = [0.22, 1, 0.36, 1]
const MODERN_EXIT_EASE: BezierTuple = [0.4, 0, 1, 1]

const RETRO_EASE: BezierTuple = [0.2, 0, 0, 1]
const RETRO_EXIT_EASE: BezierTuple = [0.4, 0, 1, 1]

const MILLENNIUM_EASE: BezierTuple = [0.16, 1, 0.3, 1]
const MILLENNIUM_EXIT_EASE: BezierTuple = [0.55, 0, 1, 1]

/**
 * 三套界面风格对应的动效性格档案：
 * - modern：磨砂玻璃通透质感，轻盈自然减速与柔和弹簧
 * - future-retro：暖纸与精密印刷仪表，高阻尼干脆落位、零多余晃动
 * - millennium：塑料机壳与实体机械键帽，极快短促、物理按压下沉
 */
export const MOTION_PROFILES: Record<DashboardStyle, MotionProfile> = {
  modern: {
    style: 'modern',
    label: '流畅通透',
    spring: { type: 'spring', stiffness: 400, damping: 32, mass: 0.85 },
    snappySpring: { type: 'spring', stiffness: 480, damping: 34, mass: 0.7 },
    gentleSpring: { type: 'spring', stiffness: 310, damping: 30, mass: 0.95 },
    ease: MODERN_EASE,
    exitEase: MODERN_EXIT_EASE,
    collapseEase: MODERN_EASE,
    easeCss: toCubicBezierCss(MODERN_EASE),
    exitEaseCss: toCubicBezierCss(MODERN_EXIT_EASE),
    collapseEaseCss: toCubicBezierCss(MODERN_EASE),
    durations: {
      instant: 0.11,
      fast: 0.16,
      normal: 0.24,
      slow: 0.34,
      exit: 0.15,
      collapse: 0.25,
    },
    distances: {
      pageY: 8,
      tabX: 0,
      tabY: 6,
      overlayY: 6,
      overlayScale: 0.96,
      dialogY: 10,
      dialogScale: 0.96,
      listItemY: 8,
      tapScale: 0.975,
      tapY: 0,
      hoverLiftY: -1.5,
      stagger: 0.028,
      maxStaggerItems: 6,
    },
  },
  'future-retro': {
    style: 'future-retro',
    label: '精密克制',
    spring: { type: 'spring', stiffness: 520, damping: 44, mass: 0.9 },
    snappySpring: { type: 'spring', stiffness: 600, damping: 48, mass: 0.75 },
    gentleSpring: { type: 'spring', stiffness: 420, damping: 40, mass: 0.95 },
    ease: RETRO_EASE,
    exitEase: RETRO_EXIT_EASE,
    collapseEase: RETRO_EASE,
    easeCss: toCubicBezierCss(RETRO_EASE),
    exitEaseCss: toCubicBezierCss(RETRO_EXIT_EASE),
    collapseEaseCss: toCubicBezierCss(RETRO_EASE),
    durations: {
      instant: 0.09,
      fast: 0.13,
      normal: 0.2,
      slow: 0.28,
      exit: 0.12,
      collapse: 0.2,
    },
    distances: {
      pageY: 5,
      tabX: 6,
      tabY: 0,
      overlayY: 4,
      overlayScale: 0.985,
      dialogY: 6,
      dialogScale: 0.985,
      listItemY: 5,
      tapScale: 0.988,
      tapY: 1,
      hoverLiftY: -1,
      stagger: 0.02,
      maxStaggerItems: 6,
    },
  },
  millennium: {
    style: 'millennium',
    label: '机械清脆',
    spring: { type: 'spring', stiffness: 680, damping: 38, mass: 0.55 },
    snappySpring: { type: 'spring', stiffness: 780, damping: 42, mass: 0.5 },
    gentleSpring: { type: 'spring', stiffness: 560, damping: 36, mass: 0.6 },
    ease: MILLENNIUM_EASE,
    exitEase: MILLENNIUM_EXIT_EASE,
    collapseEase: MILLENNIUM_EASE,
    easeCss: toCubicBezierCss(MILLENNIUM_EASE),
    exitEaseCss: toCubicBezierCss(MILLENNIUM_EXIT_EASE),
    collapseEaseCss: toCubicBezierCss(MILLENNIUM_EASE),
    durations: {
      instant: 0.06,
      fast: 0.09,
      normal: 0.14,
      slow: 0.2,
      exit: 0.09,
      collapse: 0.15,
    },
    distances: {
      pageY: 4,
      tabX: 0,
      tabY: 3,
      overlayY: 3,
      overlayScale: 0.96,
      dialogY: 4,
      dialogScale: 0.96,
      listItemY: 4,
      tapScale: 1,
      tapY: 2,
      hoverLiftY: 0,
      stagger: 0.015,
      maxStaggerItems: 5,
    },
  },
}

const ZERO_TRANSITION: Transition = { duration: 0 }

/**
 * 将外观设置中的 `anim-duration-normal`（如 `100ms` / `300ms` / `500ms` / `0ms`）
 * 映射为动效时长倍率；默认 `300ms` 对应 `1`。
 */
export function resolveMotionSpeedScale(durationToken?: string): number {
  if (!durationToken) return 1
  const trimmed = durationToken.trim().toLowerCase()
  if (trimmed === '0' || trimmed === '0ms' || trimmed === '0s') return 0
  if (trimmed === '100ms') return 0.65
  if (trimmed === '300ms') return 1
  if (trimmed === '500ms') return 1.45

  const msMatch = /^(\d+(?:\.\d+)?)ms$/.exec(trimmed)
  if (msMatch) {
    const ms = Number(msMatch[1])
    if (!Number.isFinite(ms) || ms <= 0) return 0
    return Math.min(2, Math.max(0.25, ms / 300))
  }

  const sMatch = /^(\d+(?:\.\d+)?)s$/.exec(trimmed)
  if (sMatch) {
    const ms = Number(sMatch[1]) * 1000
    if (!Number.isFinite(ms) || ms <= 0) return 0
    return Math.min(2, Math.max(0.25, ms / 300))
  }

  return 1
}

export function scaleDuration(seconds: number, speedScale: number, enabled: boolean): number {
  if (!enabled || speedScale <= 0 || seconds <= 0) return 0
  return Number((seconds * speedScale).toFixed(3))
}

/**
 * 按谐振子频率缩放弹簧参数，保持阻尼比 ζ 不变的同时调整响应快慢。
 */
export function scaleSpring(spring: SpringConfig, speedScale: number, enabled: boolean): Transition {
  if (!enabled || speedScale <= 0) return ZERO_TRANSITION
  if (speedScale === 1) return spring
  const factor = 1 / speedScale
  return {
    type: 'spring',
    stiffness: Math.round(spring.stiffness * factor * factor),
    damping: Math.round(spring.damping * factor),
    mass: spring.mass,
  }
}

export function buildMotionTransitions(
  profile: MotionProfile,
  speedScale: number,
  enabled: boolean
): ResolvedMotionTransitions {
  if (!enabled || speedScale <= 0) {
    return {
      instant: ZERO_TRANSITION,
      fast: ZERO_TRANSITION,
      normal: ZERO_TRANSITION,
      slow: ZERO_TRANSITION,
      exit: ZERO_TRANSITION,
      collapse: ZERO_TRANSITION,
      spring: ZERO_TRANSITION,
      snappy: ZERO_TRANSITION,
      gentle: ZERO_TRANSITION,
      page: ZERO_TRANSITION,
      tab: ZERO_TRANSITION,
      overlay: ZERO_TRANSITION,
      dialog: ZERO_TRANSITION,
    }
  }

  const spring = scaleSpring(profile.spring, speedScale, enabled)
  const snappy = scaleSpring(profile.snappySpring, speedScale, enabled)
  const gentle = scaleSpring(profile.gentleSpring, speedScale, enabled)
  const instant: Transition = {
    duration: scaleDuration(profile.durations.instant, speedScale, enabled),
    ease: profile.ease,
  }
  const fast: Transition = {
    duration: scaleDuration(profile.durations.fast, speedScale, enabled),
    ease: profile.ease,
  }
  const normal: Transition = {
    duration: scaleDuration(profile.durations.normal, speedScale, enabled),
    ease: profile.ease,
  }
  const slow: Transition = {
    duration: scaleDuration(profile.durations.slow, speedScale, enabled),
    ease: profile.ease,
  }
  const exit: Transition = {
    duration: scaleDuration(profile.durations.exit, speedScale, enabled),
    ease: profile.exitEase,
  }
  const collapse: Transition = {
    duration: scaleDuration(profile.durations.collapse, speedScale, enabled),
    ease: profile.collapseEase,
  }

  // 未来复古强调高阻尼线性/贝塞尔精准落位；原版与千禧浮层由对应弹簧驱动物理手感。
  const overlay: Transition = profile.style === 'future-retro' ? fast : snappy
  const dialog: Transition = profile.style === 'future-retro' ? normal : spring

  return {
    instant,
    fast,
    normal,
    slow,
    exit,
    collapse,
    spring,
    snappy,
    gentle,
    page: {
      duration: scaleDuration(profile.durations.normal, speedScale, enabled),
      ease: profile.ease,
    },
    tab: fast,
    overlay,
    dialog,
  }
}

function formatMs(seconds: number): string {
  return `${Math.round(seconds * 1000)}ms`
}

/**
 * 同步到 `:root` 的 CSS 动效令牌，让 Radix 退场、折叠高度与控件微交互
 * 与 `motion/react` 共享同一套风格性格和速度倍率。
 */
export function buildMotionCssVariables(
  profile: MotionProfile,
  speedScale: number,
  enabled: boolean
): Record<string, string> {
  const instant = scaleDuration(profile.durations.instant, speedScale, enabled)
  const fast = scaleDuration(profile.durations.fast, speedScale, enabled)
  const normal = scaleDuration(profile.durations.normal, speedScale, enabled)
  const slow = scaleDuration(profile.durations.slow, speedScale, enabled)
  const exit = scaleDuration(profile.durations.exit, speedScale, enabled)
  const collapse = scaleDuration(profile.durations.collapse, speedScale, enabled)

  return {
    '--motion-duration-instant': formatMs(instant),
    '--motion-duration-fast': formatMs(fast),
    '--motion-duration-normal': formatMs(normal),
    '--motion-duration-slow': formatMs(slow),
    '--motion-duration-exit': formatMs(exit),
    '--motion-collapse-duration': formatMs(collapse),
    '--motion-ease': profile.easeCss,
    '--motion-ease-exit': profile.exitEaseCss,
    '--motion-collapse-ease': profile.collapseEaseCss,
    '--motion-overlay-scale': String(profile.distances.overlayScale),
    '--motion-overlay-y': `${profile.distances.overlayY}px`,
    '--motion-dialog-scale': String(profile.distances.dialogScale),
    '--motion-dialog-y': `${profile.distances.dialogY}px`,
    '--motion-tap-scale': String(profile.distances.tapScale),
    '--motion-tap-y': `${profile.distances.tapY}px`,
    '--motion-hover-y': `${profile.distances.hoverLiftY}px`,
  }
}

export function getMotionProfile(style: DashboardStyle): MotionProfile {
  return MOTION_PROFILES[style]
}
