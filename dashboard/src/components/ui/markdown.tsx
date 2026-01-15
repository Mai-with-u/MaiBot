import { MarkdownRenderer } from '@/components/markdown-renderer'

interface MarkdownProps {
  children: string
  className?: string
}

/**
 * Markdown 组件 - 用于渲染 Markdown 内容（支持 GFM 和 LaTeX）
 * 
 * @example
 * ```tsx
 * <Markdown>
 *   # 标题
 *   这是一段 **加粗** 的文字
 *   
 *   数学公式：$E = mc^2$
 *   
 *   块级公式：
 *   $$
 *   \int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi}
 *   $$
 * </Markdown>
 * ```
 */
export function Markdown({ children, className }: MarkdownProps) {
  return <MarkdownRenderer content={children} className={className} />
}
