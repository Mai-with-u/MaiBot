import * as React from "react"

import { cn } from "@/lib/utils"

function getScrollContainers(element: HTMLElement) {
  const containers: Array<{ element: HTMLElement; scrollLeft: number; scrollTop: number }> = []
  let current = element.parentElement

  while (current) {
    if (current.scrollHeight > current.clientHeight || current.scrollWidth > current.clientWidth) {
      containers.push({
        element: current,
        scrollLeft: current.scrollLeft,
        scrollTop: current.scrollTop,
      })
    }
    current = current.parentElement
  }

  return containers
}

function restoreScrollContainers(containers: Array<{ element: HTMLElement; scrollLeft: number; scrollTop: number }>) {
  for (const container of containers) {
    container.element.scrollTop = container.scrollTop
    container.element.scrollLeft = container.scrollLeft
  }
}

export interface TextareaProps extends React.ComponentProps<"textarea"> {
  /** 编辑区已由输入面板提供边框时使用 composer。 */
  variant?: "default" | "composer"
  /**
   * 是否启用自动高度调整
   * @default true
   */
  autoResize?: boolean
  /**
   * 最小高度（像素），仅在 autoResize=true 时生效
   * @default 60
   */
  minHeight?: number
  /**
   * 最大高度（像素），仅在 autoResize=true 时生效
   * 设置为 undefined 或 0 表示不限制最大高度
   */
  maxHeight?: number
  /** 仅滚动期间显示滚动条，保留轨道宽度以避免文字跳动。 */
  hideScrollbarWhenIdle?: boolean
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, variant = "default", autoResize = true, minHeight = 60, maxHeight, value, onChange, hideScrollbarWhenIdle = false, onScroll, ...props }, ref) => {
    const innerRef = React.useRef<HTMLTextAreaElement>(null)
    const scrollTimer = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
    const [isScrolling, setIsScrolling] = React.useState(false)
    React.useEffect(() => () => clearTimeout(scrollTimer.current), [])
    const [hasFixedHeight, setHasFixedHeight] = React.useState(false)

    // 合并 ref
    React.useImperativeHandle(ref, () => innerRef.current!)

    // 检测是否设置了固定高度
    React.useEffect(() => {
      if (className) {
        // 检查是否包含固定高度的类（如 h-20, h-[200px], min-h-[xxx] 等）
        const hasFixedHeightClass = /\b(h-\d+|h-\[[\d.]+(?:px|rem|em)\]|min-h-\[[\d.]+(?:px|rem|em)\])\b/.test(className)
        setHasFixedHeight(hasFixedHeightClass)
      }
    }, [className])

    // 自动调整高度函数
    const adjustHeight = React.useCallback(() => {
      const textarea = innerRef.current
      if (!textarea || !autoResize || hasFixedHeight) return

      const scrollContainers = getScrollContainers(textarea)

      // 先解除旧高度才能测出内容缩短后的高度；同步恢复父容器滚动，避免输入时页面跳动。
      textarea.style.height = 'auto'
      const computed = window.getComputedStyle(textarea)
      const borderHeight = parseFloat(computed.borderTopWidth) + parseFloat(computed.borderBottomWidth)
      const paddingHeight = parseFloat(computed.paddingTop) + parseFloat(computed.paddingBottom)
      // scrollHeight 含内边距、不含边框，不能直接当作 border-box 的高度。
      const contentHeight = computed.boxSizing === 'border-box'
        ? textarea.scrollHeight + borderHeight
        : textarea.scrollHeight - paddingHeight

      // 计算新高度
      let newHeight = Math.max(contentHeight, minHeight)
      
      // 应用最大高度限制
      if (maxHeight && maxHeight > 0) {
        newHeight = Math.min(newHeight, maxHeight)
      }
      
      textarea.style.height = `${newHeight}px`
      
      // 如果内容超过最大高度，启用滚动
      if (maxHeight && maxHeight > 0 && contentHeight > maxHeight) {
        textarea.style.overflowY = 'auto'
      } else {
        textarea.style.overflowY = 'hidden'
      }

      restoreScrollContainers(scrollContainers)
    }, [autoResize, hasFixedHeight, minHeight, maxHeight])

    // 监听 value 变化并调整高度
    React.useLayoutEffect(() => {
      adjustHeight()
    }, [value, adjustHeight])

    // 字体和容器布局稳定后再次测量，避免初始 scrollHeight 被算得过高。
    React.useEffect(() => {
      adjustHeight()

      const textarea = innerRef.current
      if (!textarea || !autoResize || hasFixedHeight) return

      const animationFrameId = window.requestAnimationFrame(adjustHeight)
      // 高度由本组件调整，只在可用宽度变化时重新测量，避免观察自身高度形成循环。
      let previousWidth = textarea.getBoundingClientRect().width
      const resizeObserver = new ResizeObserver(() => {
        const width = textarea.getBoundingClientRect().width
        if (width !== previousWidth) {
          previousWidth = width
          adjustHeight()
        }
      })
      resizeObserver.observe(textarea)
      if (textarea.parentElement) {
        resizeObserver.observe(textarea.parentElement)
      }

      const fonts = document.fonts
      fonts?.ready.then(adjustHeight).catch(() => undefined)

      return () => {
        window.cancelAnimationFrame(animationFrameId)
        resizeObserver.disconnect()
      }
    }, [adjustHeight, autoResize, hasFixedHeight])

    // 处理 onChange 事件
    const handleChange = React.useCallback(
      (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        onChange?.(e)
        // 延迟调整高度，确保值已更新
        requestAnimationFrame(() => {
          adjustHeight()
        })
      },
      [onChange, adjustHeight]
    )

    return (
      <textarea
        data-dashboard-textarea="true"
        data-textarea-variant={variant}
        data-scrollbar-idle={hideScrollbarWhenIdle && !isScrolling ? 'true' : undefined}
        onScroll={(event) => {
          onScroll?.(event)
          if (!hideScrollbarWhenIdle) return
          setIsScrolling(true)
          clearTimeout(scrollTimer.current)
          scrollTimer.current = setTimeout(() => setIsScrolling(false), 800)
        }}
        className={cn(
          "flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
          "custom-scrollbar",
          autoResize && !hasFixedHeight && "resize-none overflow-hidden",
          className
        )}
        ref={innerRef}
        value={value}
        onChange={handleChange}
        style={{
          minHeight: autoResize && !hasFixedHeight ? `${minHeight}px` : undefined,
        }}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea }
