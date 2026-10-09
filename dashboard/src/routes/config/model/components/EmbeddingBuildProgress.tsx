import { useEffect, useState } from 'react'

import { Progress } from '@/components/ui/progress'
import {
  getExpressionVectorBuildProgress,
  type ExpressionVectorBuildProgress,
} from '@/lib/expression-api'
import { getMemoryRuntimeConfig, type MemoryRuntimeConfigPayload } from '@/lib/memory-api'

interface BuildProgressRow {
  label: string
  detail: string
  percent: number
}

function expressionRow(data: ExpressionVectorBuildProgress | null, error: boolean): BuildProgressRow {
  if (error) return { label: '表达库构建', detail: '状态获取失败', percent: 0 }
  if (!data) return { label: '表达库构建', detail: '正在读取状态', percent: 0 }

  const descriptions: Record<ExpressionVectorBuildProgress['status'], string> = {
    disabled: '表达向量模式未启用',
    unconfigured: '未配置嵌入模型',
    waiting_profile: '等待新模型标定',
    pending: `等待后台补建 · ${data.completed}/${data.total}`,
    running: `后台补建中 · ${data.completed}/${data.total}`,
    completed: `已完成 · ${data.completed}/${data.total}`,
  }
  return {
    label: '表达库构建',
    detail: descriptions[data.status],
    percent: data.status === 'disabled' || data.status === 'unconfigured' ? 0 : data.percent,
  }
}

function memoryRow(
  data: MemoryRuntimeConfigPayload | null,
  error: boolean,
  selectedEmbeddingModel: string
): BuildProgressRow {
  if (error || (data && !data.success)) {
    return { label: '记忆库构建', detail: '状态获取失败', percent: 0 }
  }
  if (!data) return { label: '记忆库构建', detail: '正在读取状态', percent: 0 }
  if (data.memory_enabled === false) {
    return { label: '记忆库构建', detail: '长期记忆未启用', percent: 0 }
  }
  const build = data.vector_pools?.auto_migration
  if (build?.running || build?.stage === 'failed' || build?.stage === 'cancelled') {
    const progress = build.progress
    const processed = progress?.processed || 0
    const total = progress?.total || 0
    const failed = (progress?.paragraph_failed || 0) + (progress?.entity_failed || 0) + (progress?.relation_failed || 0)
    const stages: Record<string, string> = {
      prepare_rebuild: '准备数据',
      paragraphs_start: '段落向量', paragraphs_done: '段落向量完成',
      entities_start: '实体向量', entities_done: '实体向量完成',
      relations_start: '关系向量', relations_done: '关系向量完成',
      activation_check: '校验数量', paragraph_pool_warmup: '构建段落索引',
      graph_pool_warmup: '构建图谱索引', paragraph_pool_save: '保存段落向量',
      graph_pool_save: '保存图谱向量', activate_dirs: '切换向量库',
      write_manifest: '登记向量库', reload_dual_stores: '加载向量库',
      runtime_rebuild: '更新检索服务', self_check: '校验检索服务', persist: '保存结果',
    }
    const task = build.task === 'sync' ? '模型向量库同步' : '向量重建'
    const state = build.running ? `中 · ${progress?.retrying ? '补算失败项 · ' : ''}${stages[build.stage || ''] || '处理中'}`
      : build.stage === 'cancelled' ? '已取消' : '失败'
    return {
      label: '记忆库构建',
      detail: `${task}${state}${total ? ` · 已处理 ${processed}/${total}` : ''}${failed ? ` · ${failed} 条${build.running ? '待补算' : '失败'}` : ''}`,
      percent: Math.min(99, progress?.percent || 0),
    }
  }
  const fingerprintModel = data.embedding_fingerprint?.model
  if (selectedEmbeddingModel && !fingerprintModel) {
    return { label: '记忆库构建', detail: '记忆探针尚未确认当前模型', percent: 0 }
  }
  if (selectedEmbeddingModel && fingerprintModel !== selectedEmbeddingModel) {
    return { label: '记忆库构建', detail: '记忆服务仍在使用旧模型', percent: 0 }
  }
  if (data.vector_rebuild_required) {
    return { label: '记忆库构建', detail: '需要在长期记忆页重建向量', percent: 0 }
  }
  if (data.embedding_degraded) {
    return { label: '记忆库构建', detail: data.embedding_degraded_reason || '嵌入服务不可用', percent: 0 }
  }

  const pending = data.paragraph_vector_backfill_pending || 0
  const running = data.paragraph_vector_backfill_running || 0
  const failed = data.paragraph_vector_backfill_failed || 0
  const done = data.paragraph_vector_backfill_done || 0
  const total = pending + running + failed + done
  if (total === 0) {
    return { label: '记忆库构建', detail: '向量库已就绪', percent: 100 }
  }
  return {
    label: '记忆库构建',
    detail:
      pending + running > 0
        ? `段落向量回填中 · ${done}/${total}${failed ? ` · ${failed} 条失败` : ''}`
        : failed > 0
          ? `段落向量回填有 ${failed} 条失败 · ${done}/${total}`
          : `段落向量已完成 · ${done}/${total}`,
    percent: Math.round((done / total) * 100),
  }
}

function ProgressLine({ row }: { row: BuildProgressRow }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 text-xs">
        <span className="font-medium text-foreground">{row.label}</span>
        <span className="text-muted-foreground">{row.detail}</span>
      </div>
      <Progress value={row.percent} aria-label={row.label} className="h-2" />
    </div>
  )
}

export function EmbeddingBuildProgress({ selectedEmbeddingModel }: { selectedEmbeddingModel: string }) {
  const [expression, setExpression] = useState<ExpressionVectorBuildProgress | null>(null)
  const [memory, setMemory] = useState<MemoryRuntimeConfigPayload | null>(null)
  const [expressionError, setExpressionError] = useState(false)
  const [memoryError, setMemoryError] = useState(false)

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined

    async function refresh() {
      const [expressionResult, memoryResult] = await Promise.allSettled([
        getExpressionVectorBuildProgress(),
        getMemoryRuntimeConfig(),
      ])
      if (!active) return

      if (expressionResult.status === 'fulfilled') {
        setExpression(expressionResult.value)
        setExpressionError(false)
      } else {
        setExpressionError(true)
      }
      if (memoryResult.status === 'fulfilled') {
        setMemory(memoryResult.value)
        setMemoryError(false)
      } else {
        setMemoryError(true)
      }
      timer = setTimeout(refresh, 10_000)
    }

    void refresh()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [])

  return (
    <div className="space-y-4 border-t pt-4" aria-label="嵌入模型构建进度">
      <ProgressLine row={expressionRow(expression, expressionError)} />
      <ProgressLine row={memoryRow(memory, memoryError, selectedEmbeddingModel)} />
    </div>
  )
}
