import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import type { ComponentPropsWithoutRef } from 'react'

interface MarkdownRendererProps {
  content: string
  className?: string
}

export function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  return (
    <div className={`prose prose-sm dark:prose-invert max-w-none ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // 自定义代码块样式
          code({ inline, className, children, ...props }: ComponentPropsWithoutRef<'code'> & { inline?: boolean }) {
            return inline ? (
              <code className="bg-muted px-1.5 py-0.5 rounded text-sm font-mono" {...props}>
                {children}
              </code>
            ) : (
              <code className={`${className} block bg-muted p-4 rounded-lg overflow-x-auto`} {...props}>
                {children}
              </code>
            )
          },
          // 自定义表格样式
          table({ children, ...props }) {
            return (
              <div className="overflow-x-auto">
                <table className="border-collapse border border-border" {...props}>
                  {children}
                </table>
              </div>
            )
          },
          th({ children, ...props }) {
            return (
              <th className="border border-border bg-muted px-4 py-2 text-left font-semibold" {...props}>
                {children}
              </th>
            )
          },
          td({ children, ...props }) {
            return (
              <td className="border border-border px-4 py-2" {...props}>
                {children}
              </td>
            )
          },
          // 自定义链接样式
          a({ children, ...props }) {
            return (
              <a className="text-primary hover:underline" target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            )
          },
          // 自定义引用块样式
          blockquote({ children, ...props }) {
            return (
              <blockquote className="border-l-4 border-primary pl-4 italic text-muted-foreground" {...props}>
                {children}
              </blockquote>
            )
          },
          // 自定义标题样式
          h1({ children, ...props }) {
            return (
              <h1 className="text-3xl font-bold mt-6 mb-4" {...props}>
                {children}
              </h1>
            )
          },
          h2({ children, ...props }) {
            return (
              <h2 className="text-2xl font-bold mt-5 mb-3" {...props}>
                {children}
              </h2>
            )
          },
          h3({ children, ...props }) {
            return (
              <h3 className="text-xl font-bold mt-4 mb-2" {...props}>
                {children}
              </h3>
            )
          },
          h4({ children, ...props }) {
            return (
              <h4 className="text-lg font-semibold mt-3 mb-2" {...props}>
                {children}
              </h4>
            )
          },
          // 自定义列表样式
          ul({ children, ...props }) {
            return (
              <ul className="list-disc list-inside space-y-1 my-2" {...props}>
                {children}
              </ul>
            )
          },
          ol({ children, ...props }) {
            return (
              <ol className="list-decimal list-inside space-y-1 my-2" {...props}>
                {children}
              </ol>
            )
          },
          // 自定义段落样式
          p({ children, ...props }) {
            return (
              <p className="my-2 leading-relaxed" {...props}>
                {children}
              </p>
            )
          },
          // 自定义分隔线样式
          hr({ ...props }) {
            return <hr className="my-4 border-border" {...props} />
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
