/**
 * 一键包桌面外壳的嵌入模式。
 *
 * 一键包窗口的顶栏已经显示品牌、版本号和更新入口，WebUI 在里面打开时把自己重复的那几处藏起来，
 * 并把侧栏宽度告诉外壳，让顶栏的品牌列和侧栏对齐。旧版一键包没有这些字段，WebUI 照常显示。
 */
export function isDesktopEmbedded(): boolean {
  return typeof window !== 'undefined' && window.maibotDesktopHost?.embedded === true
}

/** 启动时调用一次：嵌入时给根元素打标记，样式表据此隐藏重复的品牌与版本信息。 */
export function applyDesktopEmbedding(): void {
  if (!isDesktopEmbedded()) return
  document.documentElement.dataset.desktopEmbedded = 'true'
  // 外壳顶栏能代管折叠键时，侧栏顶部那一行就整行收起
  if (typeof window.maibotDesktopHost?.onSidebarCommand === 'function') {
    document.documentElement.dataset.desktopSidebarControl = 'true'
  }
}

/** 订阅外壳顶栏的折叠键；旧版外壳不支持时返回空的取消函数。 */
export function subscribeDesktopSidebar(listener: (collapsed: boolean) => void): () => void {
  const subscribe = window.maibotDesktopHost?.onSidebarCommand
  if (!isDesktopEmbedded() || typeof subscribe !== 'function') return () => {}
  try {
    const off = subscribe((command) => {
      if (typeof command?.collapsed === 'boolean') listener(command.collapsed)
    })
    return typeof off === 'function' ? off : () => {}
  } catch {
    return () => {}
  }
}

export interface DesktopLayout {
  sidebarWidth: number
  collapsed: boolean
  /** 教程、首次试聊或没有侧栏时为 true：外壳不允许点标志回首页 */
  homeLocked: boolean
}

export function reportDesktopLayout(layout: DesktopLayout): void {
  const report = window.maibotDesktopHost?.reportLayout
  if (typeof report !== 'function') return
  try {
    report(layout)
  } catch {
    // 外壳通道断开时不影响 WebUI 本身
  }
}
