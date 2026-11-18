import { useEffect, useRef, useState } from 'react'
import { ArrowLeftRight, Check, FlaskConical, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { FormRow } from '@/components/ui/form-section'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { probeModelThinking, type ThinkingProbeMode, type ThinkingProbeResult } from '@/lib/config-api'
import { applyThinkingProbeMode, isProbedThinkingEnabled, readThinkingProbeMode, resetThinkingProbeMode, THINKING_PROBE_LABELS } from '../thinkingProbe'

interface ThinkingProbeControlsProps {
  apiProvider: string
  modelIdentifier: string
  extraParams: Record<string, unknown>
  forceStreamMode: boolean
  disabled?: boolean
  onChange: (params: Record<string, unknown>) => void
  onBusyChange: (busy: boolean) => void
}

export function ThinkingProbeControls({
  apiProvider,
  modelIdentifier,
  extraParams,
  forceStreamMode,
  disabled = false,
  onChange,
  onBusyChange,
}: ThinkingProbeControlsProps) {
  const [running, setRunning] = useState(false)
  const [manual, setManual] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [result, setResult] = useState<{ data: ThinkingProbeResult; params: string } | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const paramsSignature = JSON.stringify(extraParams)
  const latestParams = useRef(paramsSignature)
  const mode = readThinkingProbeMode(extraParams)
  const currentResult = result?.params === paramsSignature ? result.data : null

  useEffect(() => {
    latestParams.current = paramsSignature
  }, [paramsSignature])

  // 父组件按提供商、模型与客户端挂载；关闭弹窗或切换目标后丢弃旧请求。
  useEffect(() => () => {
    requestRef.current?.abort()
    requestRef.current = null
    onBusyChange(false)
  }, [onBusyChange])

  const runProbe = async () => {
    if (requestRef.current || disabled || !apiProvider || !modelIdentifier.trim()) return
    const controller = new AbortController()
    requestRef.current = controller
    setRunning(true)
    onBusyChange(true)
    setFeedback('')
    setResult(null)
    const timeout = window.setTimeout(() => controller.abort(), 90_000)
    try {
      const data = await probeModelThinking({
        api_provider: apiProvider,
        model_identifier: modelIdentifier.trim(),
        extra_params: extraParams,
        force_stream_mode: forceStreamMode,
      }, controller.signal)
      if (requestRef.current !== controller) return
      if (latestParams.current !== paramsSignature) {
        setFeedback('额外参数已变化，本次结果未应用，请重新测试。')
        return
      }
      const matchedMode = data.status === 'matched' ? data.matches[0] : undefined
      const next = matchedMode ? applyThinkingProbeMode(extraParams, matchedMode, true) : extraParams
      setResult({ data, params: JSON.stringify(next) })
      if (matchedMode) onChange(next)
    } catch (error) {
      if (requestRef.current !== controller) return
      setFeedback(controller.signal.aborted
        ? '测试超时，请稍后重试。现有参数未改变。'
        : error instanceof Error ? error.message : '测试失败，请重试。')
    } finally {
      window.clearTimeout(timeout)
      if (requestRef.current === controller) {
        requestRef.current = null
        setRunning(false)
        onBusyChange(false)
      }
    }
  }

  const changeMode = (nextMode: ThinkingProbeMode, enabled: boolean) => {
    const next = applyThinkingProbeMode(extraParams, nextMode, enabled)
    // 手动选择未匹配的格式时，不能继续显示旧格式的测试通过状态。
    setResult(currentResult?.status === 'matched' && currentResult.matches.includes(nextMode)
      ? { data: currentResult, params: JSON.stringify(next) }
      : null
    )
    setFeedback('')
    onChange(next)
  }

  const selectMode = (value: string) => {
    if (value === 'provider_default') {
      setResult(null)
      setFeedback('')
      onChange(resetThinkingProbeMode(extraParams))
      return
    }
    changeMode(value as ThinkingProbeMode, mode ? isProbedThinkingEnabled(extraParams, mode) : true)
  }

  const canRun = !running && !disabled && Boolean(apiProvider) && Boolean(modelIdentifier.trim())
  // 和能力组里其他行同一种排布：切换按钮固定在最右侧，两种模式使用相同的行高。
  return (
      <FormRow label="思考" htmlFor={manual ? 'probed_thinking_mode' : mode ? 'probed_model_thinking' : undefined} alignTop aria-label="思考参数设置">
        <div className="flex min-h-[var(--control-height)] items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            {manual ? (
              <Select value={mode ?? ''} disabled={running || disabled} onValueChange={selectMode}>
                <SelectTrigger id="probed_thinking_mode"><SelectValue placeholder="选择参数格式" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="provider_default">清除设置，跟随服务商默认</SelectItem>
                  {Object.entries(THINKING_PROBE_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}{currentResult?.matches.includes(value as ThinkingProbeMode) ? '（已匹配）' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <span className="block truncate text-xs text-muted-foreground" title={mode ? THINKING_PROBE_LABELS[mode] : undefined}>
                {mode ? `已配置参数格式：${THINKING_PROBE_LABELS[mode]}` : '服务商未预置思考参数'}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {!manual && (
              <Button type="button" variant="outline" size="sm" disabled={!canRun} onClick={() => void runProbe()}>
                {running ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-1.5 h-3.5 w-3.5" />}
                {running ? '正在测试…' : '自动测试'}
              </Button>
            )}
            {mode && (
              <Switch
                id="probed_model_thinking"
                aria-label="启用思考"
                checked={isProbedThinkingEnabled(extraParams, mode)}
                disabled={running || disabled}
                onCheckedChange={(checked) => changeMode(mode, checked)}
              />
            )}
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="w-20 shrink-0"
              disabled={running || disabled}
              aria-label={manual ? '切换到自动测试' : '切换到手动设置'}
              onClick={() => setManual((previous) => !previous)}
            >
              <ArrowLeftRight className="h-3.5 w-3.5" />
              {manual ? '自动' : '手动'}
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {running
            ? '每批并行测试两种格式，分两批对比开关响应，约需 1 分钟…'
            : '使用实际请求探测思考格式，也可手动设置，会消耗少量Token'}
        </p>
        {currentResult && (
          <div className="space-y-1 text-xs" role="status">
            <p className={`flex items-start gap-1.5 ${currentResult.status === 'matched' ? '' : 'text-muted-foreground'}`}>
              {currentResult.status === 'matched' && <Check className="h-4 w-4 shrink-0 text-green-600" />}
              {currentResult.message}
            </p>
            <details className="text-muted-foreground">
              <summary className="cursor-pointer">查看测试详情</summary>
              <ul className="mt-1 space-y-1">
                {currentResult.attempts.map((attempt) => <li key={attempt.mode}>{THINKING_PROBE_LABELS[attempt.mode]}：{attempt.message}</li>)}
              </ul>
            </details>
          </div>
        )}
        {feedback && <p role="alert" className="text-xs text-destructive">{feedback}</p>}
      </FormRow>
  )
}
