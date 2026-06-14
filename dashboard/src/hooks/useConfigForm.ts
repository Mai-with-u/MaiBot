/**
 * useConfigForm —— 配置表单编排（深模块）。
 *
 * 承载配置编辑页的共享脊梁：「并行加载配置 + schema → seed 可编辑草稿 → 脏跟踪」。
 *
 * - 内部自持两个查询：config（必需，驱动草稿）与 schema（可选，仅供渲染字段，失败不阻塞草稿）。
 * - 以 config 查询的 `dataUpdatedAt` 作版本标记，在**渲染期**重置草稿（React 官方推荐的
 *   派生 state 写法），规避在 effect 内 setState 触发的级联渲染——这是本模块的核心 leverage。
 * - 脏跟踪用 `JSON.stringify(draft) !== seededSnapshot`，与既有页面（mcp/plugin）一致。
 *
 * 保存、自动保存、源码(raw)模式、级联删除等留在各页面（页面自行 useMutation），不进本 hook；
 * 把它们塞进来会让本模块退化为「开关汤」浅壳。config/model 的多草稿 + 按草稿快照超出本脊梁，
 * 仍由 useModelConfig 承载，不纳入。
 */
import { useCallback, useState } from 'react'

import { useQuery, type QueryKey } from '@tanstack/react-query'

export interface UseConfigFormOptions<TDraft, TConfig, TSchema> {
  /** 查询键前缀；config/schema 子查询分别追加 'config'/'schema' */
  queryKey: QueryKey
  /** 加载配置（必需）；成功返回的数据经 seed 派生为草稿 */
  loadConfig: () => Promise<TConfig>
  /** 加载 schema（可选）；仅供渲染字段，失败不阻塞草稿 */
  loadSchema?: () => Promise<TSchema>
  /** 从配置（与可选 schema）派生初始草稿；应为纯函数（渲染期调用） */
  seed: (config: TConfig, schema: TSchema | undefined) => TDraft
}

export interface UseConfigFormResult<TDraft, TSchema> {
  /** 可编辑草稿；config 就绪前为 undefined（用 isLoading 守卫） */
  draft: TDraft | undefined
  /** 原始 schema 查询结果，供页面派生字段渲染数据 */
  schema: TSchema | undefined
  /** 更新草稿（接受新值或更新函数） */
  setDraft: (next: TDraft | ((prev: TDraft) => TDraft)) => void
  /** 草稿是否相对上次 seed 快照发生变化 */
  isDirty: boolean
  /** 把草稿还原到上次 seed 快照 */
  reset: () => void
  /** 重新拉取配置：刷新后版本标记跳变，草稿在渲染期重新 seed（服务端为准） */
  reload: () => void
  /** 首屏配置是否仍在加载 */
  isLoading: boolean
  /** 配置加载错误（schema 失败不计入，由页面按需读取） */
  error: unknown
}

export function useConfigForm<TDraft, TConfig = unknown, TSchema = unknown>(
  options: UseConfigFormOptions<TDraft, TConfig, TSchema>
): UseConfigFormResult<TDraft, TSchema> {
  const { queryKey, loadConfig, loadSchema, seed } = options

  const configQuery = useQuery({
    queryKey: [...queryKey, 'config'],
    queryFn: loadConfig,
  })
  const schemaQuery = useQuery({
    queryKey: [...queryKey, 'schema'],
    queryFn: loadSchema ?? (() => Promise.resolve(undefined as TSchema)),
    enabled: Boolean(loadSchema),
  })

  const [draft, setDraftState] = useState<TDraft>()
  const [seededVersion, setSeededVersion] = useState<string | null>(null)
  // seed 快照（序列化）放在 state（而非 ref），用于脏跟踪与 reset 还原；
  // 渲染期 setState 是 React 派生 state 的合法写法，不触发 react-hooks/refs 告警。
  const [seededSnapshot, setSeededSnapshot] = useState<string>('')

  // 仅在 config 就绪、且（未请求 schema 或 schema 查询已结算）后才 seed：
  // 用「已结算」（成功或失败）而非「有数据」，使 schema 失败不阻塞草稿；
  // 同时避免 schema 晚到触发的二次 seed 覆盖用户编辑。
  const schemaReady = !loadSchema || !schemaQuery.isPending
  const dataVersion =
    configQuery.data !== undefined && schemaReady
      ? `${configQuery.dataUpdatedAt}:${schemaQuery.dataUpdatedAt}`
      : null

  // 渲染期派生草稿：版本标记跳变（首次到达 / reload 刷新）时重新 seed
  if (dataVersion !== null && dataVersion !== seededVersion) {
    const seeded = seed(configQuery.data as TConfig, schemaQuery.data)
    setSeededVersion(dataVersion)
    setDraftState(seeded)
    setSeededSnapshot(JSON.stringify(seeded))
  }

  const setDraft = useCallback((next: TDraft | ((prev: TDraft) => TDraft)) => {
    setDraftState((prev) =>
      typeof next === 'function'
        ? (next as (p: TDraft) => TDraft)(prev as TDraft)
        : next
    )
  }, [])

  const reset = useCallback(() => {
    if (seededSnapshot) {
      setDraftState(JSON.parse(seededSnapshot) as TDraft)
    }
  }, [seededSnapshot])

  const reload = useCallback(() => {
    void configQuery.refetch()
  }, [configQuery])

  const isDirty = draft !== undefined && JSON.stringify(draft) !== seededSnapshot

  return {
    draft,
    schema: schemaQuery.data,
    setDraft,
    isDirty,
    reset,
    reload,
    isLoading: configQuery.isPending,
    error: configQuery.error,
  }
}
