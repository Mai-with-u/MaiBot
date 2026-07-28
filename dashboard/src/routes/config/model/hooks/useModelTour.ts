/**
 * 模型配置页面 Tour 引导 Hook
 */
import { useCallback, useEffect, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { useTour } from '@/components/tour'
import {
  MODEL_ASSIGNMENT_TOUR_ID,
  STEP_ROUTE_MAP,
  modelAssignmentTourSteps,
} from '@/components/tour/tours/model-assignment-tour'

interface UseModelTourOptions {
  /** 打开模型编辑对话框回调 */
  onOpenEditDialog?: () => void
  /** 关闭模型编辑对话框回调 */
  onCloseEditDialog?: () => void
  /** 打开提供商编辑对话框回调 */
  onOpenProviderDialog?: () => void
  /** 关闭提供商编辑对话框回调 */
  onCloseProviderDialog?: () => void
  /** 切换到模型厂商设置标签页 */
  onOpenProvidersTab?: () => void
  /** 切换到添加模型标签页 */
  onOpenModelsTab?: () => void
  /** 切换到模型分配标签页 */
  onOpenTasksTab?: () => void
}

interface UseModelTourReturn {
  /** 开始引导 */
  startTour: () => void
  /** Tour 是否正在运行 */
  isRunning: boolean
  /** 当前步骤索引 */
  stepIndex: number
}

export function useModelTour(options: UseModelTourOptions = {}): UseModelTourReturn {
  const {
    onOpenEditDialog,
    onCloseEditDialog,
    onOpenProviderDialog,
    onCloseProviderDialog,
    onOpenProvidersTab,
    onOpenModelsTab,
    onOpenTasksTab,
  } = options
  const navigate = useNavigate()
  const { registerTour, startTour: startTourFn, state: tourState, goToStep } = useTour()
  const prevTourStepRef = useRef(tourState.stepIndex)

  const didClickTourTarget = useCallback((event: MouseEvent, selector: string) => {
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest(selector)) {
      return true
    }

    const element = document.querySelector(selector)
    if (!element) {
      return false
    }

    const rect = element.getBoundingClientRect()
    return (
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom
    )
  }, [])

  useEffect(() => {
    registerTour(MODEL_ASSIGNMENT_TOUR_ID, modelAssignmentTourSteps)
  }, [registerTour])

  useEffect(() => {
    if (tourState.activeTourId !== MODEL_ASSIGNMENT_TOUR_ID || !tourState.isRunning) {
      return
    }

    const targetRoute = STEP_ROUTE_MAP[tourState.stepIndex]
    if (targetRoute && window.location.pathname !== targetRoute) {
      navigate({ to: targetRoute })
    }
  }, [tourState.stepIndex, tourState.activeTourId, tourState.isRunning, navigate])

  useEffect(() => {
    if (tourState.activeTourId !== MODEL_ASSIGNMENT_TOUR_ID || !tourState.isRunning) {
      return
    }

    const prevStep = prevTourStepRef.current
    const currentStep = tourState.stepIndex

    if (currentStep <= 2) {
      onOpenProvidersTab?.()
    }

    if (prevStep >= 3 && prevStep <= 9 && currentStep < 3) {
      onCloseProviderDialog?.()
    }

    if (prevStep <= 2 && currentStep >= 3 && currentStep <= 9) {
      onOpenProviderDialog?.()
    }

    if (currentStep === 10 || currentStep === 11) {
      onCloseProviderDialog?.()
      onOpenModelsTab?.()
    }

    if (prevStep >= 12 && prevStep <= 17 && currentStep < 12) {
      onCloseEditDialog?.()
    }

    if (prevStep <= 11 && currentStep >= 12 && currentStep <= 17) {
      onOpenEditDialog?.()
    }

    if (currentStep === 19) {
      onOpenTasksTab?.()
    }

    prevTourStepRef.current = currentStep
  }, [
    tourState.stepIndex,
    tourState.activeTourId,
    tourState.isRunning,
    onOpenEditDialog,
    onCloseEditDialog,
    onOpenProviderDialog,
    onCloseProviderDialog,
    onOpenProvidersTab,
    onOpenModelsTab,
    onOpenTasksTab,
  ])

  useEffect(() => {
    if (tourState.activeTourId !== MODEL_ASSIGNMENT_TOUR_ID || !tourState.isRunning) return

    const handleTourClick = (event: MouseEvent) => {
      const currentStep = tourState.stepIndex

      if (currentStep === 1 && didClickTourTarget(event, '[data-tour="providers-tab-trigger"]')) {
        onOpenProvidersTab?.()
        setTimeout(() => goToStep(2), 300)
      } else if (
        currentStep === 2 &&
        didClickTourTarget(event, '[data-tour="add-provider-button"]')
      ) {
        onOpenProviderDialog?.()
        setTimeout(() => goToStep(3), 300)
      } else if (
        currentStep === 9 &&
        didClickTourTarget(event, '[data-tour="provider-cancel-button"]')
      ) {
        onCloseProviderDialog?.()
        setTimeout(() => goToStep(10), 300)
      } else if (
        currentStep === 10 &&
        didClickTourTarget(event, '[data-tour="models-tab-trigger"]')
      ) {
        onOpenModelsTab?.()
        setTimeout(() => goToStep(11), 300)
      } else if (
        currentStep === 11 &&
        didClickTourTarget(event, '[data-tour="add-model-button"]')
      ) {
        onOpenEditDialog?.()
        setTimeout(() => goToStep(12), 300)
      } else if (
        currentStep === 17 &&
        didClickTourTarget(event, '[data-tour="model-cancel-button"]')
      ) {
        onCloseEditDialog?.()
        setTimeout(() => goToStep(18), 300)
      } else if (
        currentStep === 18 &&
        didClickTourTarget(event, '[data-tour="tasks-tab-trigger"]')
      ) {
        onOpenTasksTab?.()
        setTimeout(() => goToStep(19), 300)
      }
    }

    document.addEventListener('click', handleTourClick, true)
    return () => document.removeEventListener('click', handleTourClick, true)
  }, [
    tourState,
    goToStep,
    onOpenEditDialog,
    onCloseEditDialog,
    onOpenProviderDialog,
    onCloseProviderDialog,
    onOpenProvidersTab,
    onOpenModelsTab,
    onOpenTasksTab,
    didClickTourTarget,
  ])

  const handleStartTour = useCallback(() => {
    onOpenProvidersTab?.()
    startTourFn(MODEL_ASSIGNMENT_TOUR_ID)
  }, [startTourFn, onOpenProvidersTab])

  return {
    startTour: handleStartTour,
    isRunning: tourState.isRunning && tourState.activeTourId === MODEL_ASSIGNMENT_TOUR_ID,
    stepIndex: tourState.stepIndex,
  }
}
