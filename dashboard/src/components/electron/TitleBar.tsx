import { Copy, Minus, Square, X } from 'lucide-react'
import { useMemo } from 'react'

import { useWindowControls } from '@/hooks/useWindowControls'
import { getPlatform, isElectron } from '@/lib/runtime'

const dragStyle = { WebkitAppRegion: 'drag' } as React.CSSProperties & { WebkitAppRegion: string }
const noDragStyle = { WebkitAppRegion: 'no-drag' } as React.CSSProperties & { WebkitAppRegion: string }

export function TitleBar() {
  const { close, isMaximized, minimize, toggleMaximize } = useWindowControls()
  const isMac = useMemo(() => getPlatform() === 'darwin', [])

  if (!isElectron()) return null

  return (
    <div
      className={`flex items-center justify-between border-b border-border bg-background select-none ${isMac ? 'h-7' : 'h-8'}`}
      style={dragStyle}
    >
      {/* macOS traffic light padding */}
      {isMac && <div className="h-full w-[78px]" style={noDragStyle} />}

      {/* Title / Drag area */}
      <div className="flex flex-1 items-center justify-center text-xs font-semibold text-foreground/80">
        MaiBot
      </div>

      {/* Windows / Linux Controls */}
      {!isMac && (
        <div className="flex h-full items-center" style={noDragStyle}>
          <button
            className="flex h-8 w-11 items-center justify-center hover:bg-accent hover:text-accent-foreground"
            onClick={minimize}
            tabIndex={-1}
            type="button"
            aria-label="最小化"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            className="flex h-8 w-11 items-center justify-center hover:bg-accent hover:text-accent-foreground"
            onClick={toggleMaximize}
            tabIndex={-1}
            type="button"
            aria-label={isMaximized ? "还原窗口" : "最大化"}
          >
            {isMaximized ? (
              <Copy className="h-3.5 w-3.5" />
            ) : (
              <Square className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            className="flex h-8 w-11 items-center justify-center hover:bg-destructive hover:text-destructive-foreground"
            onClick={close}
            tabIndex={-1}
            type="button"
            aria-label="关闭窗口"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}