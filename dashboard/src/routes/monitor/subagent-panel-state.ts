/**
 * 麦麦观察 - 检查器右侧栏的开合状态
 *
 * 「统计」「当前上下文」「子代理」三个开关在时间线工具栏里，共用挂在页面最外层的同一个右侧栏，
 * 两者通过这个小状态库联动。
 */
import { useSyncExternalStore } from 'react'

const PANEL_OPEN_STORAGE_KEY = 'maisaka-monitor-subagent-panel-open'
const PANEL_VIEW_STORAGE_KEY = 'maisaka-monitor-inspector-view'

export type InspectorView = 'stats' | 'context' | 'subagents'

export interface SubagentPanelState {
  open: boolean
  /** 右侧栏当前显示的视图 */
  view: InspectorView
  /** 当前选中的任务键；null 表示跟随最新任务 */
  selectedTaskKey: string | null
  /** 从时间线卡片跳转过来时要定位的 Planner 工具调用 id */
  focusCallId: string | null
}

function loadStoredView(): InspectorView {
  const stored = localStorage.getItem(PANEL_VIEW_STORAGE_KEY)
  return stored === 'stats' || stored === 'context' ? stored : 'subagents'
}

let panelState: SubagentPanelState = {
  open: localStorage.getItem(PANEL_OPEN_STORAGE_KEY) === 'true',
  view: loadStoredView(),
  selectedTaskKey: null,
  focusCallId: null,
}
const panelListeners = new Set<() => void>()

export function setSubagentPanelState(patch: Partial<SubagentPanelState>) {
  panelState = { ...panelState, ...patch }
  localStorage.setItem(PANEL_OPEN_STORAGE_KEY, String(panelState.open))
  localStorage.setItem(PANEL_VIEW_STORAGE_KEY, panelState.view)
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

/** 点开某个视图；已经在看这个视图时再点一次就收起右侧栏。 */
export function toggleInspectorView(view: InspectorView) {
  if (panelState.open && panelState.view === view) {
    setSubagentPanelState({ open: false })
    return
  }
  setSubagentPanelState({ open: true, view, focusCallId: null })
}

/** 展开右侧栏的子代理视图；传入 callId 时定位到由这次工具调用派发的任务。 */
export function openSubagentPanel(callId?: string) {
  setSubagentPanelState({ open: true, view: 'subagents', focusCallId: callId ?? null })
}
