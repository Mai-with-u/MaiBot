/**
 * 麦麦观察 - 子代理右侧栏的开合状态
 *
 * 开关按钮在时间线工具栏里，右侧栏挂在页面最外层，两者通过这个小状态库联动。
 */
import { useSyncExternalStore } from 'react'

const PANEL_OPEN_STORAGE_KEY = 'maisaka-monitor-subagent-panel-open'

export interface SubagentPanelState {
  open: boolean
  /** 当前选中的任务键；null 表示跟随最新任务 */
  selectedTaskKey: string | null
  /** 从时间线卡片跳转过来时要定位的 Planner 工具调用 id */
  focusCallId: string | null
}

let panelState: SubagentPanelState = {
  open: localStorage.getItem(PANEL_OPEN_STORAGE_KEY) === 'true',
  selectedTaskKey: null,
  focusCallId: null,
}
const panelListeners = new Set<() => void>()

export function setSubagentPanelState(patch: Partial<SubagentPanelState>) {
  panelState = { ...panelState, ...patch }
  localStorage.setItem(PANEL_OPEN_STORAGE_KEY, String(panelState.open))
  panelListeners.forEach((listener) => listener())
}

function subscribePanelState(listener: () => void) {
  panelListeners.add(listener)
  return () => {
    panelListeners.delete(listener)
  }
}

export function useSubagentPanelState() {
  return useSyncExternalStore(subscribePanelState, () => panelState)
}

/** 展开右侧栏；传入 callId 时定位到由这次工具调用派发的任务。 */
export function openSubagentPanel(callId?: string) {
  setSubagentPanelState({ open: true, focusCallId: callId ?? null })
}
