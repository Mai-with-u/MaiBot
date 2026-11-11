import { useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import {
  BASIC_FEATURES_STEP_HREFS,
  BASIC_FEATURES_TOUR_ID,
  buildBasicFeaturesTourSteps,
} from './tours/basic-features-tour'
import { useTour } from './use-tour'

/**
 * 基础功能引导的跨页面驱动：注册步骤，并在切换步骤时跳到对应页面。
 * 挂在受保护布局里，跨路由时保持存活。
 */
export function BasicFeaturesTour() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { registerTour, state } = useTour()

  useEffect(() => {
    registerTour(BASIC_FEATURES_TOUR_ID, buildBasicFeaturesTourSteps(t))
  }, [registerTour, t, i18n.language])

  const active = state.activeTourId === BASIC_FEATURES_TOUR_ID && state.isRunning
  useEffect(() => {
    if (!active) return
    const href = BASIC_FEATURES_STEP_HREFS[state.stepIndex]
    if (href && `${window.location.pathname}${window.location.search}` !== href) {
      void navigate({ href })
    }
  }, [active, state.stepIndex, navigate])

  return null
}
