import { cn } from '@/lib/utils'
import { formatVersion } from '@/lib/version'

interface LogoAreaProps {
  sidebarOpen: boolean
}

export function LogoArea({ sidebarOpen }: LogoAreaProps) {
  return (
    <div className="flex h-16 items-center border-b px-4">
      <div
        className={cn(
          'relative flex items-center justify-center flex-1 transition-all overflow-hidden',
          // 移动端始终完整显示,桌面端根据 sidebarOpen 切换
          'lg:flex-1',
          !sidebarOpen && 'lg:flex-none lg:w-8'
        )}
      >
        {/* 移动端始终显示完整 Logo，桌面端根据 sidebarOpen 切换 */}
        <div className={cn(
          "flex items-baseline gap-2",
          !sidebarOpen && "lg:hidden"
        )}>
          <span className="font-bold text-xl text-primary-gradient whitespace-nowrap">MaiBot WebUI</span>
          <span className="text-xs text-primary/60 whitespace-nowrap">
            {formatVersion()}
          </span>
        </div>
        {/* 折叠时的 Logo - 仅桌面端显示 */}
        {!sidebarOpen && (
          <span className="hidden lg:block font-bold text-primary-gradient text-2xl">M</span>
        )}
      </div>
    </div>
  )
}
