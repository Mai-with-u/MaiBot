import { ThinkingIllustration } from '@/components/ui/thinking-illustration'

export function RoutePendingFallback() {
  return (
    // 手机上可滚动页面按内容撑高，h-full 不再有高度；用视口高度扣掉页头与标签栏，让加载动画落在屏幕中间
    <div className="flex h-full items-center justify-center max-lg:min-h-[calc(100dvh-var(--mobile-dock-height,0px)-env(safe-area-inset-top)-8rem)]">
      <ThinkingIllustration size="lg" />
    </div>
  )
}
