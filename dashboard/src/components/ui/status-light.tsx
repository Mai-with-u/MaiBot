import '@/styles/status-light.css'

export type LightStatus = 'on' | 'off' | 'mixed' | 'error'

/** 纯指示灯：状态色、辉光和形状统一由主题控制，文字由使用方提供。 */
export function StatusLight({
  status,
  size = 'sm',
  glow = true,
  label,
  decorative = false,
}: {
  status: LightStatus
  size?: 'sm' | 'md' | 'lg'
  glow?: boolean
  label?: string
  decorative?: boolean
}) {
  const labels = { on: '运行中', off: '未运行', mixed: '处理中', error: '异常' }
  return (
    <span
      data-status-light="true"
      data-status={status}
      data-size={size}
      data-glow={glow && status !== 'off'}
      role={decorative ? undefined : 'img'}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : label ?? labels[status]}
    />
  )
}
