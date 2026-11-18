import { useEffect, useRef, useState } from 'react'

import { getDesktopPetSkin, type DesktopPetSkin } from '@/lib/desktop-pet-api'
import { cn } from '@/lib/utils'
import '@/styles/desktop-pet-preview.css'

/** 形象当前的动作：待机、思考，或者不动（窗口隐藏时） */
export type DesktopPetMood = 'idle' | 'thinking' | 'still'

/** 内置麦麦没有皮肤包，桌宠页面里是一张 SVG；配色与桌宠窗口一致。动画由样式表驱动。 */
function BuiltinPet({ className, mood }: { className?: string; mood: DesktopPetMood }) {
  const ink = '#1d1d1b'
  const leaf = '#8dc85c'
  const leafPath = 'M-4 0C-13 -4 -18 -18 -14 -29C-10 -43 10 -43 14 -29C18 -18 13 -4 4 0Q0 2 -4 0Z'
  const body = 'M39 96C39 68 72 48 128 44C174 41 201 64 201 93C201 124 172 141 127 141C74 141 39 124 39 96Z'
  return (
    <svg
      // 四周留出余量，浮动和摇摆时不被裁掉
      viewBox="-8 -10 226 168"
      preserveAspectRatio="xMaxYMax meet"
      className={className}
      role="img"
      aria-label="麦麦"
      data-desktop-pet-builtin="true"
      data-mood={mood}
    >
      <defs>
        <clipPath id="home-pet-clip">
          <path d={body} />
        </clipPath>
      </defs>
      <g data-pet-part="body">
        <g data-pet-part="tail" fill={leaf} stroke={ink} strokeWidth="1.9" strokeLinejoin="round">
          {[0, 90, 180, 270].map((angle) => (
            <path key={angle} d={leafPath} transform={`translate(44 96) rotate(${angle})`} />
          ))}
        </g>
        <g data-pet-part="sprout" fill={leaf} stroke={ink} strokeWidth="1.7" strokeLinejoin="round">
          <path d="M143.6 47C144.2 38 143.8 29 143.4 21H148.4C148.6 29 149.2 38 149 47Z" />
          <ellipse cx="129.5" cy="15.5" rx="16.5" ry="9.4" transform="rotate(-7 129.5 15.5)" />
          <ellipse cx="163.5" cy="18" rx="17.2" ry="9.6" transform="rotate(5 163.5 18)" />
        </g>
        <path d={body} fill="#ec9341" />
        <path d="M20 99L215 101.5V150H20Z" fill="#f4e0c6" clipPath="url(#home-pet-clip)" />
        <path d={body} fill="none" stroke={ink} strokeWidth="1.9" />
        {[135, 177.5].map((x) => (
          <g key={x} data-pet-part="eye">
            <circle cx={x} cy="85" r="8" fill="#fff" stroke={ink} strokeWidth="1.7" />
            <circle cx={x} cy="85" r="4.6" fill={ink} />
          </g>
        ))}
        <path d="M148.6 102H166L157.3 112.4Z" fill={ink} stroke={ink} strokeWidth="1.4" strokeLinejoin="round" />
      </g>
    </svg>
  )
}

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** 按状态取皮肤里对应的动画：先看皮肤的状态映射，再找同名动画，最后用第一个有帧的动画。 */
function pickAnimation(skin: DesktopPetSkin, mood: DesktopPetMood) {
  const wanted = mood === 'thinking' ? 'thinking' : 'idle'
  const names = [skin.states?.[wanted], wanted, ...Object.keys(skin.animations)]
  for (const name of names) {
    const animation = name ? skin.animations[name] : undefined
    if (animation?.sequence?.length) return animation
  }
  return null
}

/** 自定义皮肤：按皮肤自带的帧动画播放；隐藏或系统要求减少动态效果时停在第一帧。 */
function SkinAnimation({
  skinId,
  mood,
  className,
}: {
  skinId: string
  mood: DesktopPetMood
  className?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [skin, setSkin] = useState<DesktopPetSkin | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    void getDesktopPetSkin(skinId, controller.signal)
      .then(setSkin)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true)
      })
    return () => controller.abort()
  }, [skinId])

  const animation = skin ? pickAnimation(skin, mood) : null

  useEffect(() => {
    const frames = animation?.sequence
    if (!skin || !animation || !frames?.length) return
    let disposed = false
    let timer = 0
    const images: Record<string, HTMLImageElement> = {}
    const files = [...new Set(frames.map((frame) => frame.file))]
    void Promise.all(
      files.map(async (file) => {
        const src = skin.resources?.[file]
        if (!src) throw new Error(`皮肤缺少资源 ${file}`)
        const image = new Image()
        image.src = src
        await image.decode()
        images[file] = image
      })
    )
      .then(() => {
        if (disposed) return
        const animate = mood !== 'still' && !prefersReducedMotion()
        const total = frames.reduce((sum, frame) => sum + frame.duration_ms, 0) || 1
        const started = performance.now()
        const draw = (now: number) => {
          const element = canvas.current
          const context = element?.getContext('2d')
          if (disposed || !element || !context) return
          let elapsed = animate ? (now - started) % total : 0
          let frame = frames[0]
          for (const next of frames) {
            frame = next
            if (elapsed < next.duration_ms) break
            elapsed -= next.duration_ms
          }
          const [x, y, w, h] = frame.rect
          const scale = Math.min(element.width / w, element.height / h)
          const phase = ((now - started) / Math.max(600, total)) * Math.PI * 2
          context.clearRect(0, 0, element.width, element.height)
          context.save()
          if (animate) {
            // 皮肤声明的整体动作，和桌宠窗口一致
            const motion = animation.motion
            context.translate(element.width / 2, element.height)
            if (motion === 'bob') context.translate(0, -2 - 2 * Math.sin(phase))
            if (motion === 'hop') context.translate(0, -Math.abs(Math.sin(phase)) * 8)
            if (motion === 'sway') context.rotate(Math.sin(phase) * 0.035)
            if (motion === 'shake') context.translate(Math.sin((now - started) / 40) * 3, 0)
            if (motion === 'pulse') context.scale(1 + Math.sin(phase) * 0.025, 1 - Math.sin(phase) * 0.025)
            context.translate(-element.width / 2, -element.height)
          }
          context.drawImage(
            images[frame.file],
            x,
            y,
            w,
            h,
            element.width - w * scale,
            element.height - h * scale,
            w * scale,
            h * scale
          )
          context.restore()
          if (animate) timer = requestAnimationFrame(draw)
        }
        timer = requestAnimationFrame(draw)
      })
      .catch(() => {
        if (!disposed) setFailed(true)
      })
    return () => {
      disposed = true
      cancelAnimationFrame(timer)
    }
  }, [skin, animation, mood])

  // 读不到皮肤、或皮肤里没有可播的动画时退回内置形象
  if (failed || (skin && !animation)) return <BuiltinPet className={className} mood={mood} />
  return <canvas ref={canvas} width={320} height={240} className={className} role="img" aria-label="桌宠形象" />
}

/** 桌宠形象预览：内置麦麦用 SVG 加样式动画，其它皮肤播放自带帧动画；读不到皮肤时退回内置形象。 */
export function DesktopPetPreview({
  skinId,
  mood = 'idle',
  className,
}: {
  skinId: string
  mood?: DesktopPetMood
  className?: string
}) {
  const classes = cn('h-full w-full object-contain object-right-bottom', className)
  return skinId === 'builtin' || !skinId ? (
    <BuiltinPet className={classes} mood={mood} />
  ) : (
    // 换皮肤时整块重建，失败状态不会带到新皮肤上
    <SkinAnimation key={skinId} skinId={skinId} mood={mood} className={classes} />
  )
}
