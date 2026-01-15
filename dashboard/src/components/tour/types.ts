import type { Step, CallBackProps } from 'react-joyride'

// Tour ID 类型，用于区分不同的引导流程
export type TourId = string

export interface TourState {
  // 当前激活的 Tour ID
  activeTourId: TourId | null
  // 当前步骤索引
  stepIndex: number
  // Tour 是否正在运行
  isRunning: boolean
}

export interface TourContextType {
  // 状态
  state: TourState
  // 注册的所有 Tour 步骤
  tours: Map<TourId, Step[]>
  
  // 注册一个 Tour
  registerTour: (tourId: TourId, steps: Step[]) => void
  // 注销一个 Tour
  unregisterTour: (tourId: TourId) => void
  
  // 开始一个 Tour
  startTour: (tourId: TourId, startIndex?: number) => void
  // 停止当前 Tour
  stopTour: () => void
  // 跳转到指定步骤
  goToStep: (index: number) => void
  // 下一步
  nextStep: () => void
  // 上一步
  prevStep: () => void
  
  // 获取当前 Tour 的步骤
  getCurrentSteps: () => Step[]
  
  // Joyride 回调处理
  handleJoyrideCallback: (data: CallBackProps) => void
  
  // 检查用户是否已完成某个 Tour
  isTourCompleted: (tourId: TourId) => boolean
  // 标记 Tour 已完成
  markTourCompleted: (tourId: TourId) => void
  // 重置 Tour 完成状态
  resetTourCompleted: (tourId: TourId) => void
}
