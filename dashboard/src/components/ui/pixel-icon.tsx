import menuSprite from '@/assets/millennium-menu-icons.svg'
import millenniumSprite from '@/assets/millennium-pixel-icons.svg'
import { menuIconNames, millenniumNames } from './millennium-icon-set'
import toolbarSprite from '@/assets/observation-toolbar.svg'

const toolbarNames = [
  'record', 'stats', 'context', 'subagents', 'schedules', 'edit', 'top', 'previous', 'bottom', 'clear',
] as const

type PixelIconName = typeof toolbarNames[number] | typeof millenniumNames[number] | typeof menuIconNames[number]

const sprites = new Map<PixelIconName, { src: string; slot: number; count: number; size: number }>()
for (const [names, src, size] of [
  [toolbarNames, toolbarSprite, 18],
  [menuIconNames, menuSprite, 12],
  [millenniumNames, millenniumSprite, 16],
] as const) {
  names.forEach((name, slot) => sprites.set(name, { src, slot, count: names.length, size }))
}

export function PixelIcon({ name, size: requestedSize }: { name: PixelIconName; size?: number }) {
  const sprite = sprites.get(name)!
  const size = requestedSize ?? sprite.size
  return (
    <span
      aria-hidden="true"
      className="pixel-icon inline-block shrink-0"
      style={{
        width: size,
        height: size,
        imageRendering: 'pixelated',
        backgroundImage: `url(${sprite.src})`,
        backgroundSize: `${sprite.count * size}px ${size}px`,
        backgroundPosition: `${-sprite.slot * size}px 0`,
        backgroundRepeat: 'no-repeat',
      }}
    />
  )
}
