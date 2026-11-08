import { useEffect, useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import type { FieldHookComponentProps } from '@/lib/field-hooks'
import { backendApi } from '@/lib/http'
import { Button } from '@/components/ui/button'

interface Frame { file: string; rect: [number, number, number, number]; duration_ms: number }
interface Skin {
  id: string; name: string; version: string
  canvas: { width: number; height: number }
  animations: Record<string, { sequence?: Frame[]; duration_ms: number; motion?: string }>
  resources?: Record<string, string>
}
const root = '/api/webui/desktop-pet/skins'

/** 内置麦麦是桌宠页面里的 SVG，没有皮肤包，这里放一份静态图用于预览；配色与桌宠页面一致。 */
function BuiltinPetPreview() {
  const ink = '#1d1d1b'
  const leaf = '#8dc85c'
  const leafPath = 'M-4 0C-13 -4 -18 -18 -14 -29C-10 -43 10 -43 14 -29C18 -18 13 -4 4 0Q0 2 -4 0Z'
  const body = 'M39 96C39 68 72 48 128 44C174 41 201 64 201 93C201 124 172 141 127 141C74 141 39 124 39 96Z'
  return (
    <svg viewBox="0 0 210 150" className="h-full w-full p-1" role="img" aria-label="内置麦麦">
      <defs><clipPath id="builtin-pet-clip"><path d={body} /></clipPath></defs>
      <g fill={leaf} stroke={ink} strokeWidth="1.9" strokeLinejoin="round">
        {[0, 90, 180, 270].map(angle => <path key={angle} d={leafPath} transform={`translate(44 96) rotate(${angle})`} />)}
      </g>
      <g fill={leaf} stroke={ink} strokeWidth="1.7" strokeLinejoin="round">
        <path d="M143.6 47C144.2 38 143.8 29 143.4 21H148.4C148.6 29 149.2 38 149 47Z" />
        <ellipse cx="129.5" cy="15.5" rx="16.5" ry="9.4" transform="rotate(-7 129.5 15.5)" />
        <ellipse cx="163.5" cy="18" rx="17.2" ry="9.6" transform="rotate(5 163.5 18)" />
      </g>
      <path d={body} fill="#ec9341" />
      <path d="M20 99L215 101.5V150H20Z" fill="#f4e0c6" clipPath="url(#builtin-pet-clip)" />
      <path d={body} fill="none" stroke={ink} strokeWidth="1.9" />
      {[135, 177.5].map(x => (
        <g key={x}>
          <circle cx={x} cy="85" r="8" fill="#fff" stroke={ink} strokeWidth="1.7" />
          <circle cx={x} cy="85" r="4.6" fill={ink} />
        </g>
      ))}
      <path d="M148.6 102H166L157.3 112.4Z" fill={ink} stroke={ink} strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  )
}

export function DesktopPetSkinField({ value, onChange }: FieldHookComponentProps) {
  const [skins, setSkins] = useState<Skin[]>([])
  const [preview, setPreview] = useState<Skin | null>(null)
  const [animation, setAnimation] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const canvas = useRef<HTMLCanvasElement>(null)
  const selected = typeof value === 'string' ? value : 'builtin'
  async function refresh() {
    const result = await backendApi.get<{ skins: Skin[]; errors: {package: string; error: string}[] }>(root)
    setSkins(result.skins)
    setError(result.errors.map(item => `${item.package}: ${item.error}`).join('；'))
  }
  useEffect(() => { void refresh().catch(e => setError(String(e))) }, [])
  useEffect(() => {
    const controller = new AbortController()
    setPreview(null)
    void backendApi.get<Skin>(`${root}/${encodeURIComponent(selected)}`, { signal: controller.signal }).then(skin => {
      setPreview(skin); setAnimation(Object.keys(skin.animations)[0] || '')
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [selected])
  useEffect(() => {
    if (!preview || preview.id === 'builtin') return
    let disposed = false, timer = 0
    const frames = preview.animations[animation]?.sequence
    if (!frames?.length) return
    const images: Record<string, HTMLImageElement> = {}
    void Promise.all(Object.entries(preview.resources || {}).map(async ([name, src]) => {
      const image = new Image(); image.src = src; await image.decode(); images[name] = image
    })).then(() => {
      if (disposed) return
      const started = performance.now()
      function draw(now: number) {
        if (disposed || !preview || !frames) return
        const element = canvas.current, context = element?.getContext('2d')
        if (!element || !context) return
        let elapsed = (now - started) % preview.animations[animation].duration_ms
        let frame = frames[0]
        for (const next of frames) { frame = next; if (elapsed < next.duration_ms) break; elapsed -= next.duration_ms }
        context.clearRect(0, 0, element.width, element.height)
        const [x, y, w, h] = frame.rect
        const motion = preview.animations[animation].motion
        const phase = (now - started) / Math.max(600, preview.animations[animation].duration_ms) * Math.PI * 2
        context.save()
        context.translate(element.width / 2, element.height - 4)
        if (motion === 'bob') context.translate(0, -2 - 2 * Math.sin(phase))
        if (motion === 'hop') context.translate(0, -Math.abs(Math.sin(phase)) * 8)
        if (motion === 'sway') context.rotate(Math.sin(phase) * .035)
        if (motion === 'shake') context.translate(Math.sin((now - started) / 40) * 3, 0)
        if (motion === 'pulse') context.scale(1 + Math.sin(phase) * .025, 1 - Math.sin(phase) * .025)
        context.translate(-element.width / 2, -element.height + 4)
        const scale = Math.min((element.width - 8) / w, (element.height - 12) / h)
        context.drawImage(images[frame.file], x, y, w, h, (element.width - w * scale) / 2, element.height - 4 - h * scale, w * scale, h * scale)
        context.restore()
        timer = requestAnimationFrame(draw)
      }
      timer = requestAnimationFrame(draw)
    }).catch(e => { if (!disposed) setError(String(e)) })
    return () => { disposed = true; cancelAnimationFrame(timer) }
  }, [preview, animation])
  const isBuiltin = selected === 'builtin' || preview?.id === 'builtin'
  return <div className="space-y-3">
    <div className="flex items-center gap-3">
      <div className="grid h-24 w-24 shrink-0 place-items-center overflow-hidden rounded-md border bg-background" aria-label="皮肤预览">
        {isBuiltin
          ? <BuiltinPetPreview />
          : preview && <canvas ref={canvas} width={220} height={240} className="h-full w-full object-contain" aria-label="皮肤动画预览" />}
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <label className="block text-[15px] leading-6" htmlFor="desktop-pet-skin">当前皮肤</label>
        <select id="desktop-pet-skin" className="w-full rounded-md border bg-background p-2" value={selected} onChange={e => onChange?.(e.target.value)}>
          {!skins.some(s => s.id === selected) && <option value={selected}>{selected}</option>}
          {skins.map(s => <option key={s.id} value={s.id}>{s.name} · {s.version}</option>)}
        </select>
        <p className="text-xs text-muted-foreground">选择后保存配置生效，预览不打断桌宠。</p>
      </div>
    </div>
    {!isBuiltin && preview && <div className="flex flex-wrap gap-2">{Object.keys(preview.animations).map(name => <Button key={name} type="button" variant={name === animation ? 'default' : 'outline'} size="sm" onClick={() => setAnimation(name)}>{name}</Button>)}</div>}
    <label className={`flex cursor-pointer items-center justify-center gap-2 rounded-md border-2 border-dashed px-3 py-3 text-sm font-medium hover:bg-accent ${busy ? 'pointer-events-none opacity-60' : ''}`}>
      <Upload className="h-4 w-4" aria-hidden="true" />导入皮肤 ZIP
      <input type="file" accept=".zip" disabled={busy} className="sr-only" onChange={async e => {
        const file = e.target.files?.[0]; e.target.value = ''; if (!file) return
        if (file.size > 32 * 1024 * 1024) { setError('皮肤包不能超过 32 MiB'); return }
        setBusy(true); setError('')
        try { const skin = await backendApi.post<Skin>(root, { body: file }); await refresh(); onChange?.(skin.id) }
        catch (error) { setError(String(error)) }
        finally { setBusy(false) }
      }} />
    </label>
    {busy && <p className="text-sm">正在校验并安装…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}
