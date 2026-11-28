import { describe, expect, it } from 'vitest'

import {
  MOTION_PROFILES,
  buildMotionCssVariables,
  buildMotionTransitions,
  resolveMotionSpeedScale,
  scaleDuration,
  scaleSpring,
} from '../profiles'

describe('motion profiles', () => {
  it('defines distinct personalities for modern, future-retro, and millennium', () => {
    const modern = MOTION_PROFILES.modern
    const retro = MOTION_PROFILES['future-retro']
    const millennium = MOTION_PROFILES.millennium

    // 千禧机械清脆速度最快，未来复古居中，原版偏顺滑舒展
    expect(millennium.durations.normal).toBeLessThan(retro.durations.normal)
    expect(retro.durations.normal).toBeLessThan(modern.durations.normal)

    // 千禧键帽纯纵向按压不做缩放；原版与未来复古具备轻微缩放按压
    expect(millennium.distances.tapScale).toBe(1)
    expect(millennium.distances.tapY).toBeGreaterThan(0)
    expect(modern.distances.tapScale).toBeLessThan(1)

    // 弹簧刚度区分明显：千禧最高、未来复古高阻尼、原版柔和
    expect(millennium.spring.stiffness).toBeGreaterThan(retro.spring.stiffness)
    expect(retro.spring.damping).toBeGreaterThan(modern.spring.damping)
  })

  it('maps appearance animation duration tokens to speed scale', () => {
    expect(resolveMotionSpeedScale(undefined)).toBe(1)
    expect(resolveMotionSpeedScale('300ms')).toBe(1)
    expect(resolveMotionSpeedScale('100ms')).toBe(0.65)
    expect(resolveMotionSpeedScale('500ms')).toBe(1.45)
    expect(resolveMotionSpeedScale('0ms')).toBe(0)
    expect(resolveMotionSpeedScale('150ms')).toBe(0.5)
  })

  it('zeros transitions and CSS durations when disabled or speed is 0', () => {
    const transitions = buildMotionTransitions(MOTION_PROFILES.modern, 1, false)
    expect(transitions.spring).toEqual({ duration: 0 })
    expect(transitions.page).toEqual({ duration: 0 })
    expect(transitions.dialog).toEqual({ duration: 0 })

    const vars = buildMotionCssVariables(MOTION_PROFILES.millennium, 1, false)
    expect(vars['--motion-duration-fast']).toBe('0ms')
    expect(vars['--motion-collapse-duration']).toBe('0ms')
    expect(scaleDuration(0.2, 0, true)).toBe(0)
  })

  it('scales spring physics proportionally while preserving damping ratio', () => {
    const base = MOTION_PROFILES.modern.spring
    const faster = scaleSpring(base, 0.5, true)
    expect(faster).toMatchObject({ type: 'spring', mass: base.mass })
    if ('stiffness' in faster && 'damping' in faster) {
      expect(faster.stiffness).toBe(base.stiffness * 4)
      expect(faster.damping).toBe(base.damping * 2)
    }
  })
})
