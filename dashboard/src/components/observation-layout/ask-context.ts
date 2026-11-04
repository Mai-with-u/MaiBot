import { createContext, useContext } from 'react'

/** 麦麦答疑：把问题交给外壳的全局搜索，由 AI 查文档与插件市场作答。 */
export const ObservationAskContext = createContext<(question: string) => void>(() => {})

export function useAskMai(): (question: string) => void {
  return useContext(ObservationAskContext)
}
