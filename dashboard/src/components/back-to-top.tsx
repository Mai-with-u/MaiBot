import { useEffect, useState, useRef } from 'react'
import { ArrowUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

export function BackToTop() {
  const [progress, setProgress] = useState(0)
  const [visible, setVisible] = useState(false)
  const scrollerRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const handleScroll = (e: Event) => {
      const target = e.target as HTMLElement
      
      // 简单的启发式：如果是主要滚动容器（通常高度较大）
      // 我们假设页面中主要的滚动区域是高度最大的那个，或者就是当前触发滚动的这个
      // 只要它有足够的滚动空间
      if (target.scrollHeight > target.clientHeight + 100) {
         scrollerRef.current = target
         
         const scrollTop = target.scrollTop
         const height = target.scrollHeight - target.clientHeight
         const scrolled = height > 0 ? (scrollTop / height) * 100 : 0
         
         setProgress(scrolled)
         setVisible(scrollTop > 300)
      }
    }

    // 使用捕获阶段监听所有滚动事件，因为 scroll 事件不冒泡
    window.addEventListener('scroll', handleScroll, { capture: true, passive: true })
    return () => window.removeEventListener('scroll', handleScroll, { capture: true })
  }, [])

  const scrollToTop = () => {
    scrollerRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // SVG 环形进度条参数
  const radius = 18
  const circumference = 2 * Math.PI * radius
  const strokeDashoffset = circumference - (progress / 100) * circumference

  return (
    <div 
      className={cn(
        "fixed bottom-24 right-8 z-50 transition-all duration-500 ease-in-out transform",
        visible ? "translate-x-0 opacity-100" : "translate-x-32 opacity-0 pointer-events-none"
      )}
    >
      <Button
        variant="outline"
        size="icon"
        className={cn(
          "relative h-12 w-12 rounded-full shadow-xl",
          "bg-background/80 backdrop-blur-md border-border/50",
          "hover:bg-accent hover:scale-110 hover:shadow-2xl hover:border-primary/50",
          "transition-all duration-300",
          "group"
        )}
        onClick={scrollToTop}
        aria-label="回到顶部"
      >
        {/* 进度环背景 */}
        <svg className="absolute inset-0 h-full w-full -rotate-90 transform p-1" viewBox="0 0 44 44">
          <circle
            className="text-muted-foreground/10"
            strokeWidth="3"
            stroke="currentColor"
            fill="transparent"
            r={radius}
            cx="22"
            cy="22"
          />
          {/* 进度环 */}
          <circle
            className="text-primary transition-all duration-100 ease-out"
            strokeWidth="3"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            stroke="currentColor"
            fill="transparent"
            r={radius}
            cx="22"
            cy="22"
          />
        </svg>
        
        {/* 图标 */}
        <ArrowUp 
          className="h-5 w-5 text-primary transition-transform duration-300 group-hover:-translate-y-1 group-hover:scale-110" 
          strokeWidth={2.5}
        />
        
        {/* 内部发光效果 (仅在 dark 模式下明显) */}
        <div className="absolute inset-0 rounded-full bg-primary/5 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
      </Button>
    </div>
  )
}
