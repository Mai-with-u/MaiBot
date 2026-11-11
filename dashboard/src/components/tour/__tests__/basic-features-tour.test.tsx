import type { ReactNode } from 'react'
import type { TourContextType } from '../types'

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BasicFeaturesTour } from '../BasicFeaturesTour'
import { TourContext } from '../tour-context'
import {
  BASIC_FEATURES_STEP_HREFS,
  BASIC_FEATURES_TOUR_ID,
  buildBasicFeaturesTourSteps,
} from '../tours/basic-features-tour'

const navigate = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh' } }),
}))

function makeContext(activeTourId: string | null, stepIndex: number): TourContextType {
  return {
    state: { activeTourId, stepIndex, isRunning: activeTourId !== null },
    tours: new Map(),
    registerTour: vi.fn(),
    unregisterTour: vi.fn(),
    startTour: vi.fn(),
    stopTour: vi.fn(),
    goToStep: vi.fn(),
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    getCurrentSteps: vi.fn(() => []),
    handleJoyrideCallback: vi.fn(),
    isTourCompleted: vi.fn(() => false),
    markTourCompleted: vi.fn(),
    resetTourCompleted: vi.fn(),
  }
}

function renderWith(context: TourContextType) {
  return render(<BasicFeaturesTour />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <TourContext.Provider value={context}>{children}</TourContext.Provider>
    ),
  })
}

afterEach(() => {
  cleanup()
  navigate.mockReset()
  window.history.replaceState(null, '', '/')
})

describe('基础功能引导', () => {
  it('依次讲解人格、说话风格、群聊频率、插件和知识库，并定位到对应配置字段', () => {
    const steps = buildBasicFeaturesTourSteps(((key: string) => key) as never)

    expect(steps.map((step) => step.title)).toEqual([
      'tour.basicFeatures.personality.title',
      'tour.basicFeatures.replyStyle.title',
      'tour.basicFeatures.talkValue.title',
      'tour.basicFeatures.plugins.title',
      'tour.basicFeatures.knowledgeBase.title',
      'tour.basicFeatures.done.title',
    ])
    expect(steps[0].target).toContain('[data-config-field-path="personality.personality"]')
    expect(steps[2].target).toContain('[data-config-field-path="chat.reply_timing.talk_value"]')
    expect(BASIC_FEATURES_STEP_HREFS[0]).toBe('/config/bot?field=personality.personality')
    expect(BASIC_FEATURES_STEP_HREFS[2]).toBe('/config/bot?field=chat.reply_timing.talk_value')
    // 讲解配置项时允许直接操作高亮区域，结束页不需要。
    expect(steps[0].spotlightClicks).toBe(true)
    expect(steps.at(-1)?.spotlightClicks).toBe(false)
  })

  it('挂载时注册引导步骤', () => {
    const context = makeContext(null, 0)
    renderWith(context)

    expect(context.registerTour).toHaveBeenCalledWith(BASIC_FEATURES_TOUR_ID, expect.any(Array))
  })

  it('引导运行时跳到当前步骤所在页面', () => {
    renderWith(makeContext(BASIC_FEATURES_TOUR_ID, 3))

    expect(navigate).toHaveBeenCalledWith({ href: '/plugins' })
  })

  it('已在当前步骤页面时不重复跳转', () => {
    window.history.replaceState(null, '', '/plugins')
    renderWith(makeContext(BASIC_FEATURES_TOUR_ID, 3))

    expect(navigate).not.toHaveBeenCalled()
  })

  it('其他引导运行时不跳转', () => {
    renderWith(makeContext('model-assignment-tour', 3))

    expect(navigate).not.toHaveBeenCalled()
  })
})
