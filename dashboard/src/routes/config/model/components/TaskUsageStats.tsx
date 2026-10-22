import { useEffect, useState, type ReactNode } from 'react'

import { PageFrame } from '@/components/ui/surface'
import { backendApi } from '@/lib/http'

interface TaskModelUsage {
  model_name: string
  model_identifier: string
  provider_name: string
  request_count: number
  failure_count: number
  avg_duration: number | null
  failure_rate: number
}

interface TaskUsage {
  task_name: string
  hours: number
  request_count: number
  models: TaskModelUsage[]
}

export function TaskUsageStats({ taskName, summary }: { taskName: string; summary?: ReactNode }) {
  const [data, setData] = useState<TaskUsage | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    const load = async () => {
      setError('')
      try {
        const result = await backendApi.get<TaskUsage>('/api/webui/statistics/task-usage', {
          query: { task_name: taskName, hours: 24 },
          errorMessage: '获取任务使用情况失败',
        })
        if (active) setData(result)
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : '获取任务使用情况失败')
      }
    }
    void load()
    const timer = window.setInterval(load, 30_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [taskName])

  const current = data?.task_name === taskName ? data : null
  if (error) return <p role="alert" className="text-destructive text-sm">{error}</p>
  if (!current || current.request_count === 0) return null

  return (
    <PageFrame asChild>
      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden lg:flex-1" aria-label="状态">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2">
          <h5 className="text-sm font-semibold">状态</h5>
          <span className="text-xs text-muted-foreground">最近 24 小时</span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
          {summary && <div className="shrink-0 border-b pb-2">{summary}</div>}
          <p className="shrink-0 text-sm">最近 24 小时请求数 <span className="ml-2 text-xl font-semibold tabular-nums">{current.request_count.toLocaleString()}</span></p>
          {current.models.length === 0 ? (
            <p className="text-muted-foreground text-sm">最近 24 小时暂无调用记录。</p>
          ) : (
            <div className="min-h-0 max-h-80 overflow-auto overscroll-contain lg:max-h-none lg:flex-1">
              <table className="w-full text-left text-sm">
                <thead className="sr-only">
                  <tr>
                    <th className="py-2 pr-4 font-medium">模型 / 提供商</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right font-medium">平均耗时</th>
                    <th className="whitespace-nowrap py-2 pl-3 text-right font-medium">失败率</th>
                  </tr>
                </thead>
                <tbody>
                  {current.models.map((model) => (
                    <tr key={`${model.provider_name}/${model.model_name}/${model.model_identifier}`} className="border-b-2 border-border last:border-b-0">
                      <td className="py-3 pr-4">
                        <div className="break-all font-medium">{model.model_name}</div>
                        <div className="text-muted-foreground break-all text-xs">{model.provider_name}{model.model_identifier !== model.model_name ? ` · ${model.model_identifier}` : ''}</div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">{model.avg_duration === null ? '—' : `${model.avg_duration.toFixed(2)} 秒`}</td>
                      <td className="whitespace-nowrap py-3 pl-3 text-right tabular-nums">{(model.failure_rate * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </PageFrame>
  )
}
