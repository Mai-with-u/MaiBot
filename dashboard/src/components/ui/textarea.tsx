import * as React from "react"

import { cn } from "@/lib/utils"

export interface TextareaProps extends React.ComponentProps<"textarea"> {
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
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, autoResize = true, minHeight = 60, maxHeight, value, onChange, ...props }, ref) => {
    const innerRef = React.useRef<HTMLTextAreaElement>(null)
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

      // 重置高度以获取真实的 scrollHeight
      textarea.style.height = 'auto'
      
      // 计算新高度
      const scrollHeight = textarea.scrollHeight
      let newHeight = Math.max(scrollHeight, minHeight)
      
      // 应用最大高度限制
      if (maxHeight && maxHeight > 0) {
        newHeight = Math.min(newHeight, maxHeight)
      }
      
      textarea.style.height = `${newHeight}px`
      
      // 如果内容超过最大高度，启用滚动
      if (maxHeight && maxHeight > 0 && scrollHeight > maxHeight) {
        textarea.style.overflowY = 'auto'
      } else {
        textarea.style.overflowY = 'hidden'
      }
    }, [autoResize, hasFixedHeight, minHeight, maxHeight])

    // 监听 value 变化并调整高度
    React.useEffect(() => {
      adjustHeight()
    }, [value, adjustHeight])

    // 组件挂载时调整高度
    React.useEffect(() => {
      adjustHeight()
    }, [adjustHeight])

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
