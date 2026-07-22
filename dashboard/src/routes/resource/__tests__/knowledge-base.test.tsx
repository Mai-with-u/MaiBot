import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { KnowledgeBasePage } from '../knowledge-base'
import * as memoryApi from '@/lib/memory-api'

// 页面现已依赖 TanStack Query（导入队列/表单 hook），渲染需提供 QueryClient（与 main.tsx 一致）。
// 每次渲染用全新 client，避免测试间缓存泄漏。
function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeBasePage />
    </QueryClientProvider>,
  )
}

const navigateMock = vi.fn()
const toastMock = vi.fn()

afterEach(() => {
  cleanup()
})

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/components', () => ({
  CodeEditor: ({ value }: { value: string }) => <pre data-testid="code-editor">{value}</pre>,
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}))

vi.mock('@/components/memory/MemoryConfigEditor', () => ({
  MemoryConfigEditor: () => <div data-testid="memory-config-editor">memory-config-editor</div>,
}))

vi.mock('@/components/memory/MemoryDeleteDialog', () => ({
  MemoryDeleteDialog: ({
    open,
    onExecute,
    onRestore,
    preview,
    result,
  }: {
    open: boolean
    preview?: { mode?: string; item_count?: number } | null
    result?: { operation_id?: string } | null
    onExecute?: () => void
    onRestore?: () => void
  }) => (
    open ? (
      <div data-testid="memory-delete-dialog">
        <div>{`preview:${preview?.mode ?? 'none'}:${preview?.item_count ?? 0}`}</div>
        <div>{`result:${result?.operation_id ?? 'none'}`}</div>
        <button type="button" onClick={onExecute}>执行删除</button>
        <button type="button" onClick={onRestore}>执行恢复</button>
      </div>
    ) : null
  ),
}))

vi.mock('@/lib/memory-api', () => ({
  getMemoryConfigSchema: vi.fn(),
  getMemoryConfig: vi.fn(),
  getMemoryConfigRaw: vi.fn(),
  getMemoryRuntimeConfig: vi.fn(),
  getMemoryImportGuide: vi.fn(),
  getMemoryImportSettings: vi.fn(),
  getMemoryImportPathAliases: vi.fn(),
  getMemoryImportChatTargets: vi.fn(),
  getMemoryImportTasks: vi.fn(),
  getMemoryImportTask: vi.fn(),
  getMemoryImportTaskChunks: vi.fn(),
  createMemoryUploadImport: vi.fn(),
  createMemoryPasteImport: vi.fn(),
  createMemoryRawScanImport: vi.fn(),
  createMemoryLpmmOpenieImport: vi.fn(),
  createMemoryLpmmConvertImport: vi.fn(),
  createMemoryTemporalBackfillImport: vi.fn(),
  createMemoryMaibotMigrationImport: vi.fn(),
  cancelMemoryImportTask: vi.fn(),
  retryMemoryImportTask: vi.fn(),
  resolveMemoryImportPath: vi.fn(),
  refreshMemoryRuntimeSelfCheck: vi.fn(),
  rebuildMemoryRuntimeVectors: vi.fn(),
  updateMemoryConfig: vi.fn(),
  updateMemoryConfigRaw: vi.fn(),
  getMemoryTuningProfile: vi.fn(),
  getMemoryTuningTasks: vi.fn(),
  createMemoryTuningTask: vi.fn(),
  applyBestMemoryTuningProfile: vi.fn(),
  getMemorySources: vi.fn(),
  getMemoryDeleteOperations: vi.fn(),
  getMemoryDeleteOperation: vi.fn(),
  previewMemoryCorrection: vi.fn(),
  executeMemoryCorrection: vi.fn(),
  getMemoryCorrectionPlans: vi.fn(),
  getMemoryCorrectionPlan: vi.fn(),
  rollbackMemoryCorrectionPlan: vi.fn(),
  getMemoryFeedbackCorrections: vi.fn(),
  getMemoryFeedbackCorrection: vi.fn(),
  getMemoryTimeline: vi.fn(),
  getMemoryEpisodes: vi.fn(),
  getMemoryEpisodeStatus: vi.fn(),
  getMemoryEpisode: vi.fn(),
  rebuildMemoryEpisodes: vi.fn(),
  processMemoryEpisodePending: vi.fn(),
  getMemoryProfiles: vi.fn(),
  searchMemoryProfiles: vi.fn(),
  queryMemoryProfile: vi.fn(),
  setMemoryProfileOverride: vi.fn(),
  deleteMemoryProfileOverride: vi.fn(),
  getMemoryRecycleBin: vi.fn(),
  restoreMaintainedMemory: vi.fn(),
  reinforceMemory: vi.fn(),
  freezeMemory: vi.fn(),
  protectMemory: vi.fn(),
  getMemoryProfileEvidence: vi.fn(),
  correctMemoryProfileEvidence: vi.fn(),
  getMemoryGraph: vi.fn(),
  getMemoryGraphSearch: vi.fn(),
  getMemoryGraphNodeDetail: vi.fn(),
  getMemoryGraphEdgeDetail: vi.fn(),
  getMemoryGraphParagraphDetail: vi.fn(),
  previewMemoryDelete: vi.fn(),
  executeMemoryDelete: vi.fn(),
  restoreMemoryDelete: vi.fn(),
  rollbackMemoryFeedbackCorrection: vi.fn(),
}))

function mockImportTask(taskId: string, status: string = 'running'): memoryApi.MemoryImportTaskPayload {
  return {
    task_id: taskId,
    source: 'webui',
    status,
    current_step: status === 'completed' ? 'completed' : 'running',
    total_chunks: 120,
    done_chunks: status === 'completed' ? 120 : 36,
    failed_chunks: status === 'completed' ? 0 : 2,
    cancelled_chunks: 0,
    progress: status === 'completed' ? 100 : 30,
    error: '',
    file_count: 2,
    created_at: 1_710_000_000,
    started_at: 1_710_000_001,
    finished_at: status === 'completed' ? 1_710_000_099 : null,
    updated_at: 1_710_000_100,
    task_kind: 'paste',
    params: {},
    files: [],
  }
}

function mockImportDetail(taskId: string): memoryApi.MemoryImportTaskPayload {
  return {
    ...mockImportTask(taskId),
    files: [
      {
        file_id: 'file-alpha',
        name: 'alpha.txt',
        source_kind: 'paste',
        input_mode: 'text',
        status: 'running',
        current_step: 'running',
        detected_strategy_type: 'auto',
        total_chunks: 80,
        done_chunks: 30,
        failed_chunks: 1,
        cancelled_chunks: 0,
        progress: 37.5,
        error: '',
        created_at: 1_710_000_000,
        updated_at: 1_710_000_100,
      },
      {
        file_id: 'file-beta',
        name: 'beta.txt',
        source_kind: 'paste',
        input_mode: 'text',
        status: 'failed',
        current_step: 'extracting',
        detected_strategy_type: 'auto',
        total_chunks: 40,
        done_chunks: 6,
        failed_chunks: 4,
        cancelled_chunks: 0,
        progress: 25,
        error: 'mock error',
        created_at: 1_710_000_000,
        updated_at: 1_710_000_100,
      },
    ],
  }
}

function mockImportCompletedWithErrorsDetail(taskId: string): memoryApi.MemoryImportTaskPayload {
  return {
    ...mockImportDetail(taskId),
    status: 'completed_with_errors',
    current_step: 'completed_with_errors',
    total_chunks: 12,
    done_chunks: 9,
    failed_chunks: 3,
    cancelled_chunks: 0,
    progress: 75,
    files: [
      {
        file_id: 'file-error',
        name: 'error.txt',
        source_kind: 'paste',
        input_mode: 'text',
        status: 'failed',
        current_step: 'failed',
        detected_strategy_type: 'auto',
        total_chunks: 12,
        done_chunks: 9,
        failed_chunks: 3,
        cancelled_chunks: 0,
        progress: 75,
        error: 'mock error',
        created_at: 1_710_000_000,
        updated_at: 1_710_000_100,
      },
    ],
  }
}

async function waitForConsoleReady() {
  await screen.findByRole('tab', { name: '图谱' }, { timeout: 10_000 })
}

describe('KnowledgeBasePage import workflow', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh')
    if (!Element.prototype.hasPointerCapture) {
      Element.prototype.hasPointerCapture = vi.fn()
    }
    if (!Element.prototype.setPointerCapture) {
      Element.prototype.setPointerCapture = vi.fn()
    }
    if (!Element.prototype.releasePointerCapture) {
      Element.prototype.releasePointerCapture = vi.fn()
    }
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = vi.fn()
    }

    window.history.replaceState(null, '', '/resource/knowledge-base')
    navigateMock.mockReset()
    toastMock.mockReset()
    vi.mocked(memoryApi.createMemoryUploadImport).mockReset()
    vi.mocked(memoryApi.createMemoryPasteImport).mockReset()
    vi.mocked(memoryApi.createMemoryRawScanImport).mockReset()
    vi.mocked(memoryApi.createMemoryLpmmOpenieImport).mockReset()
    vi.mocked(memoryApi.createMemoryLpmmConvertImport).mockReset()
    vi.mocked(memoryApi.createMemoryTemporalBackfillImport).mockReset()
    vi.mocked(memoryApi.createMemoryMaibotMigrationImport).mockReset()
    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockReset()

    vi.mocked(memoryApi.getMemoryConfigSchema).mockResolvedValue({
      success: true,
      path: 'config/a_memorix.toml',
      schema: {
        plugin_id: 'a_memorix',
        plugin_info: {
          name: 'A_Memorix',
          version: '2.0.0',
          description: '长期记忆子系统',
          author: 'A_Dawn',
        },
        _note: 'raw-only 字段仍可通过 TOML 编辑',
        layout: {
          type: 'tabs',
          tabs: [{ id: 'basic', title: '基础', sections: ['plugin'], order: 1 }],
        },
        sections: {
          plugin: {
            name: 'plugin',
            title: '子系统状态',
            collapsed: false,
            order: 1,
            fields: {},
          },
        },
      },
    })
    vi.mocked(memoryApi.getMemoryConfig).mockResolvedValue({
      success: true,
      path: 'config/a_memorix.toml',
      config: { plugin: { enabled: true } },
    })
    vi.mocked(memoryApi.getMemoryConfigRaw).mockResolvedValue({
      success: true,
      path: 'config/a_memorix.toml',
      config: '[plugin]\nenabled = true\n',
    })
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValue({
      success: true,
      config: { plugin: { enabled: true }, integration: { fuzzy_modify_candidate_limit: 12 } },
      data_dir: 'data/plugins/a-dawn.a-memorix',
      embedding_dimension: 1024,
      fuzzy_modify_candidate_limit: 12,
      auto_save: true,
      relation_vectors_enabled: false,
      vector_pools: {
        configured_mode: 'dual',
        effective_mode: 'dual',
        ready: true,
        single_pool: { available: true, dimension: 1024, num_vectors: 4, has_data: true },
        paragraph_pool: { available: true, dimension: 1024, num_vectors: 7, has_data: true },
        graph_pool: { available: true, dimension: 1024, num_vectors: 5, has_data: true },
      },
      vector_pools_ready: true,
      vector_pools_effective_mode: 'dual',
      runtime_ready: true,
      embedding_degraded: false,
      embedding_degraded_reason: '',
      embedding_degraded_since: null,
      embedding_last_check: null,
      vector_rebuild_required: true,
      vector_rebuild_message: '需要重建向量',
      paragraph_vector_backfill_pending: 2,
      paragraph_vector_backfill_running: 0,
      paragraph_vector_backfill_failed: 1,
      paragraph_vector_backfill_done: 3,
    })
    vi.mocked(memoryApi.getMemoryGraph).mockResolvedValue({
      success: true,
      nodes: [{ id: 'alpha', name: 'Alpha' }],
      edges: [],
      total_nodes: 1,
      total_edges: 0,
    })
    vi.mocked(memoryApi.getMemoryGraphSearch).mockResolvedValue({
      success: true,
      query: '',
      limit: 50,
      count: 0,
      items: [],
    })
    vi.mocked(memoryApi.getMemoryGraphNodeDetail).mockResolvedValue({
      success: true,
      node: { id: 'alpha', type: 'entity', content: 'Alpha', hash: 'entity-1', appearance_count: 1 },
      relations: [],
      paragraphs: [],
      evidence_graph: { nodes: [], edges: [], focus_entities: [] },
    })
    vi.mocked(memoryApi.getMemoryGraphEdgeDetail).mockResolvedValue({
      success: true,
      edge: {
        source: 'alpha',
        target: 'beta',
        weight: 1,
        predicates: [],
        relation_count: 0,
        evidence_count: 0,
        relation_hashes: [],
      },
      relations: [],
      paragraphs: [],
      evidence_graph: { nodes: [], edges: [], focus_entities: [] },
    })
    vi.mocked(memoryApi.getMemoryGraphParagraphDetail).mockResolvedValue({
      success: true,
      paragraph: {
        hash: 'paragraph-jump',
        content: '跳转段落内容',
        preview: '跳转段落内容',
        source: 'chat_summary:chat-1',
        entity_count: 1,
        relation_count: 0,
        entities: ['Alpha'],
        relations: [],
      },
      evidence_graph: {
        nodes: [
          { id: 'paragraph:paragraph-jump', type: 'paragraph', content: '跳转段落内容', metadata: { hash: 'paragraph-jump' } },
        ],
        edges: [],
        focus_entities: ['Alpha'],
      },
    })

    vi.mocked(memoryApi.getMemoryImportGuide).mockResolvedValue({
      success: true,
      content: '# 导入指南\n导入说明',
    })
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: {
        max_paste_chars: 200_000,
        max_file_concurrency: 8,
        max_chunk_concurrency: 16,
        default_file_concurrency: 2,
        default_chunk_concurrency: 4,
        poll_interval_ms: 60_000,
        maibot_source_db_default: 'data/maibot.db',
      },
    })
    vi.mocked(memoryApi.getMemoryImportPathAliases).mockResolvedValue({
      success: true,
      path_aliases: {
        lpmm: 'data/lpmm',
        plugin_data: 'data/plugins/a-dawn.a-memorix',
        raw: 'data/raw',
      },
    })
    vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({
      success: true,
      data: [
        {
          chat_id: 'chat-1',
          chat_name: '测试群',
          platform: 'qq',
          group_id: '10001',
          user_id: null,
          is_group: true,
        },
      ],
    })
    vi.mocked(memoryApi.getMemoryImportTasks).mockResolvedValue({
      success: true,
      items: [
        mockImportTask('import-run-1', 'running'),
        mockImportTask('import-queued-1', 'queued'),
        mockImportTask('import-done-1', 'completed'),
      ],
    })
    vi.mocked(memoryApi.getMemoryImportTask).mockResolvedValue({
      success: true,
      task: mockImportDetail('import-run-1'),
    })
    vi.mocked(memoryApi.getMemoryImportTaskChunks).mockImplementation(async (_taskId, fileId, offset = 0) => ({
      success: true,
      task_id: 'import-run-1',
      file_id: fileId,
      offset,
      limit: 50,
      total: 120,
      items: [
        {
          chunk_id: `${fileId}-${offset + 0}`,
          index: offset + 0,
          chunk_type: 'text',
          status: 'running',
          step: 'extracting',
          failed_at: '',
          retryable: true,
          error: '',
          progress: 50,
          content_preview: `chunk-preview-${offset + 0}`,
          updated_at: 1_710_000_111,
        },
      ],
    }))

    vi.mocked(memoryApi.createMemoryUploadImport).mockResolvedValue({
      success: true,
      task: mockImportTask('upload-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValue({
      success: true,
      task: mockImportTask('paste-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryRawScanImport).mockResolvedValue({
      success: true,
      task: mockImportTask('raw-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryLpmmOpenieImport).mockResolvedValue({
      success: true,
      task: mockImportTask('openie-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryLpmmConvertImport).mockResolvedValue({
      success: true,
      task: mockImportTask('convert-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryTemporalBackfillImport).mockResolvedValue({
      success: true,
      task: mockImportTask('backfill-task-1', 'queued'),
    })
    vi.mocked(memoryApi.createMemoryMaibotMigrationImport).mockResolvedValue({
      success: true,
      task: mockImportTask('migration-task-1', 'queued'),
    })
    vi.mocked(memoryApi.cancelMemoryImportTask).mockResolvedValue({
      success: true,
      task: mockImportTask('import-run-1', 'cancel_requested'),
    })
    vi.mocked(memoryApi.retryMemoryImportTask).mockResolvedValue({
      success: true,
      task: mockImportTask('retry-task-1', 'queued'),
    })
    vi.mocked(memoryApi.resolveMemoryImportPath).mockResolvedValue({
      success: true,
      alias: 'raw',
      relative_path: 'exports',
      resolved_path: 'D:/Dev/rdev/MaiBot/data/raw/exports',
      exists: true,
      is_file: false,
      is_dir: true,
    })

    vi.mocked(memoryApi.getMemoryTuningProfile).mockResolvedValue({
      success: true,
      profile: { retrieval: { top_k: 10 } },
      toml: '[retrieval]\ntop_k = 10\n',
    })
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{
        task_id: 'tune-1',
        status: 'completed',
        progress: 100,
        rounds_done: 20,
        rounds_total: 20,
        best_score: 0.58,
        recommended: true,
        validation_summary: {
          recommended: true,
          holdout_case_count: 6,
          deltas: {
            score: 0.08,
            precision_at_1: 0.1,
            recall_at_k: 0.12,
            empty_rate: -0.02,
            avg_elapsed_ms: 14,
          },
          online_like: {
            baseline: {
              score: 0.5,
              metrics: {
                precision_at_1: 0.6,
                recall_at_k: 0.5,
                empty_rate: 0.08,
                avg_elapsed_ms: 80,
              },
            },
            best: {
              score: 0.58,
              metrics: {
                precision_at_1: 0.7,
                recall_at_k: 0.62,
                empty_rate: 0.06,
                avg_elapsed_ms: 94,
              },
            },
          },
        },
      }],
    })
    vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValue({ success: true } as never)

    vi.mocked(memoryApi.getMemorySources).mockResolvedValue({
      success: true,
      items: [{ source: 'demo-1', paragraph_count: 2, relation_count: 1 }],
      count: 1,
    })
    vi.mocked(memoryApi.getMemoryDeleteOperations).mockResolvedValue({
      success: true,
      items: [
        {
          operation_id: 'del-1',
          mode: 'source',
          status: 'executed',
          summary: { counts: { paragraphs: 2, relations: 1, sources: 1 } },
        },
      ],
      count: 1,
    })
    vi.mocked(memoryApi.getMemoryDeleteOperation).mockResolvedValue({
      success: true,
      operation: {
        operation_id: 'del-1',
        mode: 'source',
        status: 'executed',
        selector: { sources: ['demo-1'] },
        summary: { counts: { paragraphs: 2, relations: 1, sources: 1 }, sources: ['demo-1'] },
        items: [],
      },
    })
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items: [
        {
          plan_id: 'correction-plan-1',
          request_text: '把测试用户的常住城市改为杭州',
          scope: 'person_profile',
          target_person_id: 'person-1',
          target_chat_id: 'chat-1',
          status: 'awaiting_confirmation',
          confidence: 0.91,
          plan: {
            scope: 'person_profile',
            request_text: '把测试用户的常住城市改为杭州',
            person_id: 'person-1',
            chat_id: 'chat-1',
            confidence: 0.91,
            risk_level: 'medium',
            reason: '用户明确修正',
            operations: [
              {
                action: 'mark_superseded',
                candidate_id: 'paragraph:p-old',
                target_type: 'paragraph',
                hash: 'p-old',
                reason: '旧城市已过期',
              },
            ],
          },
          preview: {
            request_text: '把测试用户的常住城市改为杭州',
            scope: 'person_profile',
            person_id: 'person-1',
            person_keyword: '测试用户',
            chat_id: 'chat-1',
            candidates: [
              {
                candidate_id: 'paragraph:p-old',
                target_type: 'paragraph',
                evidence_type: 'person_fact',
                hash: 'p-old',
                content: '测试用户常住城市是上海',
                source: 'chat_summary:chat-1',
                metadata: {},
                score: 0.87,
              },
            ],
            operations: [
              {
                action: 'mark_superseded',
                candidate_id: 'paragraph:p-old',
                target_type: 'paragraph',
                hash: 'p-old',
                reason: '旧城市已过期',
              },
            ],
            requires_confirmation: true,
            confirm_threshold: 0.75,
            reason: '用户明确修正',
          },
          execution: {},
          created_at: 1_710_000_020,
          updated_at: 1_710_000_021,
          executed_at: null,
          requested_by: 'knowledge_base',
          reason: '用户明确修正',
        },
      ],
      count: 1,
    })
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockResolvedValue({
      success: true,
      plan: {
        plan_id: 'correction-plan-1',
        request_text: '把测试用户的常住城市改为杭州',
        scope: 'person_profile',
        target_person_id: 'person-1',
        target_chat_id: 'chat-1',
        status: 'awaiting_confirmation',
        confidence: 0.91,
        plan: {
          scope: 'person_profile',
          request_text: '把测试用户的常住城市改为杭州',
          person_id: 'person-1',
          chat_id: 'chat-1',
          confidence: 0.91,
          risk_level: 'medium',
          reason: '用户明确修正',
          operations: [
            {
              action: 'mark_superseded',
              candidate_id: 'paragraph:p-old',
              target_type: 'paragraph',
              hash: 'p-old',
              reason: '旧城市已过期',
            },
          ],
        },
        preview: {
          request_text: '把测试用户的常住城市改为杭州',
          scope: 'person_profile',
          person_id: 'person-1',
          person_keyword: '测试用户',
          chat_id: 'chat-1',
          candidates: [
            {
              candidate_id: 'paragraph:p-old',
              target_type: 'paragraph',
              evidence_type: 'person_fact',
              hash: 'p-old',
              content: '测试用户常住城市是上海',
              source: 'chat_summary:chat-1',
              metadata: {},
              score: 0.87,
            },
          ],
          operations: [
            {
              action: 'mark_superseded',
              candidate_id: 'paragraph:p-old',
              target_type: 'paragraph',
              hash: 'p-old',
              reason: '旧城市已过期',
            },
          ],
          requires_confirmation: true,
          confirm_threshold: 0.75,
          reason: '用户明确修正',
        },
        execution: {},
        created_at: 1_710_000_020,
        updated_at: 1_710_000_021,
        executed_at: null,
        requested_by: 'knowledge_base',
        reason: '用户明确修正',
      },
    })
    vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
      success: true,
      plan_id: 'correction-plan-2',
      requires_confirmation: true,
      preview: {
        request_text: '把测试用户的常住城市改为杭州',
        scope: 'person_profile',
        person_id: 'person-1',
        person_keyword: '测试用户',
        chat_id: 'chat-1',
        candidates: [],
        operations: [
          {
            action: 'refresh_person_profile',
            person_id: 'person-1',
          },
        ],
        requires_confirmation: true,
        confirm_threshold: 0.75,
        reason: '用户明确修正',
      },
    })
    vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
      success: true,
      plan: null,
      execution: { success: true },
    })
    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: true,
      plan: null,
      rollback: {
        success: true,
        new_relations_deactivated: [],
        restored_targets: [],
        items: [],
        requested_by: 'knowledge_base',
        reason: '测试回滚',
      },
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrections).mockResolvedValue({
      success: true,
      items: [
        {
          task_id: 11,
          query_tool_id: 'tool-query-11',
          session_id: 'session-1',
          query_text: '测试用户最喜欢的颜色是什么',
          query_timestamp: 1_710_000_010,
          task_status: 'applied',
          decision: 'correct',
          decision_confidence: 0.97,
          feedback_message_count: 1,
          rollback_status: 'none',
          affected_counts: {
            relations: 1,
            stale_paragraphs: 1,
            episode_sources: 2,
            profile_person_ids: 1,
            correction_paragraphs: 1,
            corrected_relations: 1,
          },
          created_at: 1_710_000_011,
          updated_at: 1_710_000_012,
        },
      ],
      count: 1,
    })
    vi.mocked(memoryApi.getMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      task: {
        task_id: 11,
        query_tool_id: 'tool-query-11',
        session_id: 'session-1',
        query_text: '测试用户最喜欢的颜色是什么',
        query_timestamp: 1_710_000_010,
        task_status: 'applied',
        decision: 'correct',
        decision_confidence: 0.97,
        feedback_message_count: 1,
        rollback_status: 'none',
        affected_counts: {
          relations: 1,
          stale_paragraphs: 1,
          episode_sources: 2,
          profile_person_ids: 1,
          correction_paragraphs: 1,
          corrected_relations: 1,
        },
        query_snapshot: { query: '测试用户最喜欢的颜色是什么', hits: [{ hash: 'paragraph-1' }] },
        decision_payload: { decision: 'correct', confidence: 0.97 },
        rollback_plan_summary: {
          forgotten_relations: [{ hash: 'rel-old', subject: '测试用户', predicate: '最喜欢的颜色是', object: '蓝色' }],
          corrected_write: {
            paragraph_hashes: ['paragraph-new'],
            corrected_relations: [{ hash: 'rel-new', subject: '测试用户', predicate: '最喜欢的颜色是', object: '绿色' }],
          },
        },
        rollback_result: {},
        action_logs: [
          {
            id: 1,
            task_id: 11,
            query_tool_id: 'tool-query-11',
            action_type: 'forget_relation',
            target_hash: 'rel-old',
            reason: '用户明确纠正为绿色',
            before_payload: { hash: 'rel-old', subject: '测试用户', predicate: '最喜欢的颜色是', object: '蓝色' },
            after_payload: { is_inactive: true },
            created_at: 1_710_000_013,
          },
        ],
        created_at: 1_710_000_011,
        updated_at: 1_710_000_012,
      },
    })
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue({
      success: true,
      chat: {
        chat_id: 'chat-1',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: null,
        is_group: true,
      },
      range: {
        time_start: 1_710_000_000,
        time_end: 1_710_003_600,
        min_time: 1_710_000_000,
        max_time: 1_710_003_600,
      },
      summary: {
        total: 1,
        by_type: { episode: 1, episode_created: 1 },
      },
      items: [
        {
          event_id: 'episode_created:ep-1:1710000100',
          event_type: 'episode_created',
          category: 'episode',
          occurred_at: 1_710_000_100,
          chat_id: 'chat-1',
          chat_name: '测试群',
          title: 'Episode 新增：测试 Episode',
          summary: '测试 Episode 摘要',
          object_count: 2,
          key_id: 'ep-1',
          source: 'chat_summary:chat-1',
          attribution: 'source',
          metadata: { episode_id: 'ep-1' },
          jump_target: {
            tab: 'episodes',
            params: {
              episode_id: 'ep-1',
              source: 'chat_summary:chat-1',
            },
          },
        },
      ],
    })
    vi.mocked(memoryApi.getMemoryEpisodeStatus).mockResolvedValue({
      success: true,
      counts: { pending: 2, running: 1, done: 3, failed: 0, total: 6 },
      failed: [],
    })
    vi.mocked(memoryApi.getMemoryEpisodes).mockResolvedValue({
      success: true,
      items: [
        {
          episode_id: 'ep-1',
          title: '测试 Episode',
          summary: '测试 Episode 摘要',
          source: 'chat_summary:chat-1',
          created_at: 1_710_000_100,
          updated_at: 1_710_000_100,
        },
      ],
      count: 1,
    })
    vi.mocked(memoryApi.getMemoryEpisode).mockResolvedValue({
      success: true,
      episode: {
        episode_id: 'ep-1',
        title: '测试 Episode',
        summary: '测试 Episode 摘要',
        source: 'chat_summary:chat-1',
        paragraphs: [],
      },
    })
    vi.mocked(memoryApi.rebuildMemoryEpisodes).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.processMemoryEpisodePending).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.getMemoryProfiles).mockResolvedValue({
      success: true,
      items: [],
      count: 0,
    })
    vi.mocked(memoryApi.searchMemoryProfiles).mockResolvedValue({
      success: true,
      items: [],
      count: 0,
    })
    vi.mocked(memoryApi.queryMemoryProfile).mockResolvedValue({
      success: true,
      person_id: 'person-1',
      profile_text: '测试画像',
    } as never)
    vi.mocked(memoryApi.getMemoryProfileEvidence).mockResolvedValue({
      success: true,
      person_id: 'person-1',
      evidence: [],
    } as never)
    vi.mocked(memoryApi.setMemoryProfileOverride).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.deleteMemoryProfileOverride).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.getMemoryRecycleBin).mockResolvedValue({
      success: true,
      items: [],
      count: 0,
    } as never)
    vi.mocked(memoryApi.restoreMaintainedMemory).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.reinforceMemory).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.freezeMemory).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.protectMemory).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.previewMemoryDelete).mockResolvedValue({
      success: true,
      mode: 'source',
      selector: { sources: ['demo-1'] },
      counts: { sources: 1, paragraphs: 2, relations: 1 },
      sources: ['demo-1'],
      items: [{ item_type: 'paragraph', item_hash: 'p-1', label: 'demo-1' }],
      item_count: 1,
      dry_run: true,
    } as never)
    vi.mocked(memoryApi.executeMemoryDelete).mockResolvedValue({
      success: true,
      mode: 'source',
      operation_id: 'del-2',
      counts: { sources: 1, paragraphs: 2, relations: 1 },
      sources: ['demo-1'],
      deleted_count: 4,
      deleted_entity_count: 0,
      deleted_relation_count: 1,
      deleted_paragraph_count: 2,
      deleted_source_count: 1,
    } as never)
    vi.mocked(memoryApi.restoreMemoryDelete).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.rollbackMemoryFeedbackCorrection).mockResolvedValue({
      success: true,
      result: { restored_relation_hashes: ['rel-old'] },
      task: {
        task_id: 11,
        query_tool_id: 'tool-query-11',
        session_id: 'session-1',
        query_text: '测试用户最喜欢的颜色是什么',
        query_timestamp: 1_710_000_010,
        task_status: 'applied',
        decision: 'correct',
        decision_confidence: 0.97,
        feedback_message_count: 1,
        rollback_status: 'rolled_back',
        affected_counts: {
          relations: 1,
          stale_paragraphs: 1,
          episode_sources: 2,
          profile_person_ids: 1,
          correction_paragraphs: 1,
          corrected_relations: 1,
        },
        query_snapshot: { query: '测试用户最喜欢的颜色是什么', hits: [{ hash: 'paragraph-1' }] },
        decision_payload: { decision: 'correct', confidence: 0.97 },
        rollback_plan_summary: {},
        rollback_result: { restored_relation_hashes: ['rel-old'] },
        action_logs: [],
        created_at: 1_710_000_011,
        updated_at: 1_710_000_012,
      },
    })
    vi.mocked(memoryApi.refreshMemoryRuntimeSelfCheck).mockResolvedValue({
      success: true,
      report: { ok: true },
    })
    vi.mocked(memoryApi.rebuildMemoryRuntimeVectors).mockImplementation(async (payload = {}) => ({
      success: true,
      dry_run: Boolean(payload.dry_run),
      counts: { paragraphs: 2, entities: 1, relations: 0 },
      total: 3,
      done: payload.dry_run ? 0 : 3,
      failed: 0,
    }))
    vi.mocked(memoryApi.updateMemoryConfig).mockResolvedValue({ success: true } as never)
    vi.mocked(memoryApi.updateMemoryConfigRaw).mockResolvedValue({ success: true } as never)
  })

  it('loads import settings/guide/tasks on first render', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))

    expect(screen.getByText('向量池')).toBeInTheDocument()
    expect(screen.getByText('双池')).toBeInTheDocument()
    expect(screen.getByText('段落 7 · 图谱 5')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '创建导入任务' })).toBeInTheDocument()
    expect((await screen.findAllByText('import-run-1')).length).toBeGreaterThan(0)
    expect(memoryApi.getMemoryImportSettings).toHaveBeenCalled()
    expect(memoryApi.getMemoryImportPathAliases).toHaveBeenCalled()
    expect(memoryApi.getMemoryImportTasks).toHaveBeenCalled()
  })

  it('shows vector pool migration progress in runtime badges', async () => {
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValueOnce({
      success: true,
      config: { plugin: { enabled: true } },
      data_dir: 'data/plugins/a-dawn.a-memorix',
      embedding_dimension: 1024,
      auto_save: true,
      relation_vectors_enabled: false,
      vector_pools: {
        configured_mode: 'dual',
        effective_mode: 'single',
        ready: false,
        single_pool: { available: true, dimension: 1024, num_vectors: 10967, has_data: true },
        paragraph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        graph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        auto_migration: {
          running: true,
          attempted: true,
          success: false,
          stage: 'entities_done',
          progress: {
            total: 12000,
            processed: 11183,
            percent: 93.2,
            elapsed_seconds: 192,
            estimated_remaining_seconds: 120,
            paragraph_done: 10967,
            paragraph_failed: 0,
            entity_done: 216,
            entity_failed: 0,
          },
          last_error: '',
          started_at: 1782662070,
          finished_at: null,
          updated_at: 1782662262,
        },
      },
      vector_pools_ready: false,
      vector_pools_effective_mode: 'single',
      runtime_ready: true,
      embedding_degraded: false,
      embedding_degraded_reason: '',
      embedding_degraded_since: null,
      embedding_last_check: null,
      vector_rebuild_required: false,
      vector_rebuild_message: '',
      paragraph_vector_backfill_pending: 0,
      paragraph_vector_backfill_running: 0,
      paragraph_vector_backfill_failed: 0,
      paragraph_vector_backfill_done: 0,
    })

    renderPage()

    await waitForConsoleReady()

    expect(screen.getByText('双池迁移中')).toBeInTheDocument()
    expect(screen.getByText('实体完成 · 11183/12000 · 预计剩余 2分0秒')).toBeInTheDocument()
    expect(screen.getByText('93.2%')).toBeInTheDocument()
  })

  it('shows pending ETA while vector pool migration rate is unavailable', async () => {
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValueOnce({
      success: true,
      config: { plugin: { enabled: true } },
      data_dir: 'data/plugins/a-dawn.a-memorix',
      embedding_dimension: 1024,
      auto_save: true,
      relation_vectors_enabled: false,
      vector_pools: {
        configured_mode: 'dual',
        effective_mode: 'single',
        ready: false,
        single_pool: { available: true, dimension: 1024, num_vectors: 10967, has_data: true },
        paragraph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        graph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        auto_migration: {
          running: true,
          attempted: true,
          success: false,
          stage: 'prepare_rebuild',
          progress: {
            total: 12000,
            processed: 0,
            percent: 0,
            elapsed_seconds: 0,
            estimated_remaining_seconds: null,
          },
          last_error: '',
          started_at: 1782662070,
          finished_at: null,
          updated_at: 1782662070,
        },
      },
      vector_pools_ready: false,
      vector_pools_effective_mode: 'single',
      runtime_ready: true,
      embedding_degraded: false,
      embedding_degraded_reason: '',
      embedding_degraded_since: null,
      embedding_last_check: null,
      vector_rebuild_required: false,
      vector_rebuild_message: '',
      paragraph_vector_backfill_pending: 0,
      paragraph_vector_backfill_running: 0,
      paragraph_vector_backfill_failed: 0,
      paragraph_vector_backfill_done: 0,
    })

    renderPage()

    await waitForConsoleReady()

    expect(screen.getByText('双池迁移中')).toBeInTheDocument()
    expect(screen.getByText('准备迁移 · 0/12000 · 预计计算中')).toBeInTheDocument()
  })

  it('clamps vector pool migration percent inside the progress bar label', async () => {
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValueOnce({
      success: true,
      config: { plugin: { enabled: true } },
      data_dir: 'data/plugins/a-dawn.a-memorix',
      embedding_dimension: 1024,
      auto_save: true,
      relation_vectors_enabled: false,
      vector_pools: {
        configured_mode: 'dual',
        effective_mode: 'single',
        ready: false,
        single_pool: { available: true, dimension: 1024, num_vectors: 10, has_data: true },
        paragraph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        graph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        auto_migration: {
          running: true,
          attempted: true,
          success: false,
          stage: 'paragraphs_done',
          progress: {
            total: 10,
            processed: 10,
            percent: 150,
            elapsed_seconds: 10,
            estimated_remaining_seconds: null,
          },
          last_error: '',
          started_at: 1782662070,
          finished_at: null,
          updated_at: 1782662080,
        },
      },
      vector_pools_ready: false,
      vector_pools_effective_mode: 'single',
      runtime_ready: true,
      embedding_degraded: false,
      embedding_degraded_reason: '',
      embedding_degraded_since: null,
      embedding_last_check: null,
      vector_rebuild_required: false,
      vector_rebuild_message: '',
      paragraph_vector_backfill_pending: 0,
      paragraph_vector_backfill_running: 0,
      paragraph_vector_backfill_failed: 0,
      paragraph_vector_backfill_done: 0,
    })

    renderPage()

    await waitForConsoleReady()

    expect(screen.getByText('段落完成 · 10/10 · 预计计算中')).toBeInTheDocument()
    expect(screen.getByText('100.0%')).toBeInTheDocument()
  })

  it('keeps displaying legacy vector pool migration details without stable totals', async () => {
    vi.mocked(memoryApi.getMemoryRuntimeConfig).mockResolvedValueOnce({
      success: true,
      config: { plugin: { enabled: true } },
      data_dir: 'data/plugins/a-dawn.a-memorix',
      embedding_dimension: 1024,
      auto_save: true,
      relation_vectors_enabled: false,
      vector_pools: {
        configured_mode: 'dual',
        effective_mode: 'single',
        ready: false,
        single_pool: { available: true, dimension: 1024, num_vectors: 10967, has_data: true },
        paragraph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        graph_pool: { available: true, dimension: 1024, num_vectors: 0, has_data: false },
        auto_migration: {
          running: true,
          attempted: true,
          success: false,
          stage: 'entities_done',
          progress: {
            paragraph_done: 10967,
            paragraph_failed: 1,
            entity_done: 216,
            entity_failed: 0,
          },
          last_error: '',
          started_at: 1782662070,
          finished_at: null,
          updated_at: 1782662262,
        },
      },
      vector_pools_ready: false,
      vector_pools_effective_mode: 'single',
      runtime_ready: true,
      embedding_degraded: false,
      embedding_degraded_reason: '',
      embedding_degraded_since: null,
      embedding_last_check: null,
      vector_rebuild_required: false,
      vector_rebuild_message: '',
      paragraph_vector_backfill_pending: 0,
      paragraph_vector_backfill_running: 0,
      paragraph_vector_backfill_failed: 0,
      paragraph_vector_backfill_done: 0,
    })

    renderPage()

    await waitForConsoleReady()

    expect(screen.getByText('双池迁移中')).toBeInTheDocument()
    expect(screen.getByText('实体完成 · 段落 10967/1 失败 · 实体 216')).toBeInTheDocument()
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument()
  })

  it('rebuilds all vectors from overview controls', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('button', { name: '重建向量' }))
    await waitFor(() =>
      expect(memoryApi.rebuildMemoryRuntimeVectors).toHaveBeenCalledWith({ dry_run: true }),
    )
    expect(await screen.findByText('重建全部向量')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认重建' }))
    await waitFor(() =>
      expect(memoryApi.rebuildMemoryRuntimeVectors).toHaveBeenCalledWith({ dry_run: false }),
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith(expect.objectContaining({ title: '向量重建完成' })),
    )
  })

  it('creates import tasks for all 7 modes and calls correct endpoints', async () => {
    const user = userEvent.setup()
    const { container } = renderPage()

    const openImportTab = async () => {
      await user.click(screen.getByRole('tab', { name: '导入' }))
      await screen.findByRole('button', { name: '创建导入任务' })
    }

    await waitForConsoleReady()
    await openImportTab()

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const uploadFiles = [
      new File(['hello'], 'demo.txt', { type: 'text/plain' }),
      new File(['{"name":"mai"}'], 'demo.json', { type: 'application/json' }),
      new File(['a,b\n1,2'], 'demo.csv', { type: 'text/csv' }),
      new File(['# note'], 'demo.md', { type: 'text/markdown' }),
    ]
    await user.upload(fileInput, uploadFiles)
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryUploadImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: '粘贴导入' }))
    const editableTextarea = Array.from(container.querySelectorAll('textarea')).find((item) => !item.readOnly)
    if (!editableTextarea) {
      throw new Error('missing editable textarea')
    }
    await user.type(editableTextarea, 'paste content')
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryPasteImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: '本地扫描' }))
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryRawScanImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: 'LPMM OpenIE' }))
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryLpmmOpenieImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: 'LPMM 转换' }))
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryLpmmConvertImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: '时序回填' }))
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryTemporalBackfillImport).toHaveBeenCalledTimes(1))

    await openImportTab()
    await user.click(screen.getByRole('tab', { name: 'MaiBot 迁移' }))
    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryMaibotMigrationImport).toHaveBeenCalledTimes(1))

    const [uploadedFiles, uploadPayload] = vi.mocked(memoryApi.createMemoryUploadImport).mock.calls[0]
    expect(uploadedFiles).toHaveLength(4)
    expect(uploadedFiles.map((file) => file.name)).toEqual(['demo.txt', 'demo.json', 'demo.csv', 'demo.md'])
    expect(uploadPayload).toMatchObject({
      input_mode: 'text',
      llm_enabled: true,
      strategy_override: 'auto',
      dedupe_policy: 'content_hash',
    })
  }, 60_000)

  it('formats MaiBot migration datetime-local values and numeric options', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))
    await screen.findByRole('button', { name: '创建导入任务' })
    await user.click(screen.getByRole('tab', { name: 'MaiBot 迁移' }))

    fireEvent.change(await screen.findByLabelText('源数据库路径'), { target: { value: ' data/old-maibot.db ' } })
    fireEvent.change(screen.getByLabelText('起始时间'), { target: { value: '2024-01-02T03:04' } })
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2024-01-03T05:06:07' } })
    fireEvent.change(screen.getByLabelText('起始 ID'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('结束 ID'), { target: { value: '20' } })
    await user.click(screen.getByText('高级选项'))
    fireEvent.change(screen.getByLabelText('读取批大小'), { target: { value: '123' } })
    fireEvent.change(screen.getByLabelText('提交窗口行数'), { target: { value: '456' } })
    fireEvent.change(screen.getByLabelText('向量线程数'), { target: { value: '7' } })

    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    await waitFor(() => expect(memoryApi.createMemoryMaibotMigrationImport).toHaveBeenCalledTimes(1))
    const expectedTimeFrom = new Date('2024-01-02T03:04').toISOString()
    const expectedTimeTo = new Date('2024-01-03T05:06:07').toISOString()
    expect(vi.mocked(memoryApi.createMemoryMaibotMigrationImport).mock.calls[0][0]).toMatchObject({
      source_db: 'data/old-maibot.db',
      time_from: expectedTimeFrom,
      time_to: expectedTimeTo,
      start_id: 10,
      end_id: 20,
      read_batch_size: 123,
      commit_window_rows: 456,
      embed_workers: 7,
    })
  }, 20_000)

  it('blocks invalid MaiBot migration input before creating a task', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))
    await screen.findByRole('button', { name: '创建导入任务' })
    await user.click(screen.getByRole('tab', { name: 'MaiBot 迁移' }))

    const sourceDbInput = await screen.findByLabelText('源数据库路径')
    const createButton = screen.getByRole('button', { name: '创建导入任务' })
    fireEvent.change(sourceDbInput, { target: { value: '   ' } })
    await user.click(createButton)
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith(expect.objectContaining({ description: '请填写源数据库路径' })),
    )
    expect(memoryApi.createMemoryMaibotMigrationImport).not.toHaveBeenCalled()

    fireEvent.change(sourceDbInput, { target: { value: 'data/old-maibot.db' } })
    fireEvent.change(screen.getByLabelText('起始时间'), { target: { value: '2024-01-03T00:00' } })
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2024-01-02T00:00' } })
    await user.click(createButton)
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith(expect.objectContaining({ description: '起始时间不能晚于结束时间' })),
    )
    expect(memoryApi.createMemoryMaibotMigrationImport).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('起始时间'), { target: { value: '2024-01-02T00:00' } })
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2024-01-03T00:00' } })
    fireEvent.change(screen.getByLabelText('起始 ID'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('结束 ID'), { target: { value: '10' } })
    await user.click(createButton)
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith(expect.objectContaining({ description: '起始 ID 不能大于结束 ID' })),
    )
    expect(memoryApi.createMemoryMaibotMigrationImport).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('起始 ID'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('结束 ID'), { target: { value: '20' } })
    await user.click(screen.getByText('高级选项'))
    fireEvent.change(screen.getByLabelText('读取批大小'), { target: { value: '0' } })
    await user.click(createButton)
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith(expect.objectContaining({ description: '读取批大小 必须填写正整数' })),
    )
    expect(memoryApi.createMemoryMaibotMigrationImport).not.toHaveBeenCalled()
  }, 20_000)

  it('loads task detail and supports chunk pagination', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))

    expect(await screen.findByText('alpha.txt')).toBeInTheDocument()
    expect(await screen.findByText('chunk-preview-0')).toBeInTheDocument()

    const betaButton = screen.getByText('beta.txt').closest('button')
    if (!betaButton) {
      throw new Error('missing file beta button')
    }
    await user.click(betaButton)
    await waitFor(() =>
      expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith('import-run-1', 'file-beta', 0, 50),
    )

    await user.click(screen.getByRole('button', { name: '下一页分块' }))
    await waitFor(() =>
      expect(memoryApi.getMemoryImportTaskChunks).toHaveBeenCalledWith('import-run-1', 'file-beta', 50, 50),
    )
  }, 20_000)

  it('shows import failures separately from successful chunks', async () => {
    vi.mocked(memoryApi.getMemoryImportTask).mockResolvedValue({
      success: true,
      task: mockImportCompletedWithErrorsDetail('import-run-1'),
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))

    expect((await screen.findAllByText('完成（有错误）')).length).toBeGreaterThan(0)
    expect(await screen.findByText('成功 9 / 12 分块 · 失败 3')).toBeInTheDocument()
  }, 20_000)

  it('supports cancel and retry actions for selected task', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))
    await screen.findByText('任务详情')

    await user.click(screen.getByRole('button', { name: '取消选中导入任务' }))
    await waitFor(() => expect(memoryApi.cancelMemoryImportTask).toHaveBeenCalledWith('import-run-1'))

    await user.click(screen.getByRole('button', { name: '重试选中导入任务' }))
    await waitFor(() => expect(memoryApi.retryMemoryImportTask).toHaveBeenCalled())
    const [taskId, retryPayload] = vi.mocked(memoryApi.retryMemoryImportTask).mock.calls[0]
    expect(taskId).toBe('import-run-1')
    expect(retryPayload).toMatchObject({
      overrides: {
        llm_enabled: true,
        strategy_override: 'auto',
      },
    })
  }, 20_000)

  it('auto polling updates queue and keeps page stable when refresh fails once', async () => {
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: {
        max_paste_chars: 200_000,
        max_file_concurrency: 8,
        max_chunk_concurrency: 16,
        default_file_concurrency: 2,
        default_chunk_concurrency: 4,
        poll_interval_ms: 200,
        maibot_source_db_default: 'data/maibot.db',
      },
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '导入' }))
    await screen.findByText('导入队列')

    const initialCalls = vi.mocked(memoryApi.getMemoryImportTasks).mock.calls.length
    vi.mocked(memoryApi.getMemoryImportTasks).mockRejectedValueOnce(new Error('poll failure'))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350))
    })

    expect(screen.getByRole('tab', { name: '图谱' })).toBeInTheDocument()
    expect(vi.mocked(memoryApi.getMemoryImportTasks).mock.calls.length).toBeGreaterThan(initialCalls)
  }, 20_000)

  it('creates tuning task and applies best profile (tuning module)', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')

    await user.click(screen.getByRole('button', { name: '开始调优' }))
    await waitFor(() =>
      expect(memoryApi.createMemoryTuningTask).toHaveBeenCalledWith({
        objective: 'precision_priority',
        intensity: 'standard',
        sample_size: 24,
        top_k_eval: 20,
      }),
    )

    await user.click(screen.getByRole('button', { name: '应用推荐结果' }))
    await waitFor(() =>
      expect(memoryApi.applyBestMemoryTuningProfile).toHaveBeenCalledWith('tune-1', {
        persist: false,
        validate: true,
      }),
    )
  }, 20_000)

  it('shows business failure when tuning task creation is rejected', async () => {
    vi.mocked(memoryApi.createMemoryTuningTask).mockResolvedValueOnce({
      success: false,
      error: '样本不足',
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')
    await user.click(screen.getByRole('button', { name: '开始调优' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '创建调优任务失败',
        description: '样本不足',
        variant: 'destructive',
      }),
    )
  }, 20_000)

  it('shows business failure when applying tuning profile is rejected', async () => {
    vi.mocked(memoryApi.applyBestMemoryTuningProfile).mockResolvedValueOnce({
      success: false,
      error: '独立样本验证未通过',
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')
    await user.click(screen.getByRole('button', { name: '应用推荐结果' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '应用最佳参数失败',
        description: '独立样本验证未通过',
        variant: 'destructive',
      }),
    )
  }, 20_000)

  it('keeps tuning parameters collapsed by default', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('当前调优结果')

    expect(screen.getByText('验证通过，建议应用。')).toBeInTheDocument()
    expect(screen.getByText('0.500 → 0.580 · Δ +0.080')).toBeInTheDocument()
    expect(screen.getByText('通过，6 个样本')).toBeInTheDocument()
    const parameterLabels = await screen.findAllByText('初步查找数量')
    parameterLabels.forEach((label) => expect(label).not.toBeVisible())

    await user.click(screen.getByText('设置详情'))
    expect(parameterLabels[0]).toBeVisible()
  }, 20_000)

  it('shows tuning progress before evaluation result exists', async () => {
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{
        task_id: 'tune-running',
        status: 'running',
        progress: 45,
        rounds_done: 9,
        rounds_total: 20,
        recommended: false,
      }],
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('当前调优结果')

    expect(screen.getByText('任务状态')).toBeInTheDocument()
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0)
    expect(screen.getByText('任务进度')).toBeInTheDocument()
    expect(screen.getByText('45%')).toBeInTheDocument()
    expect(screen.getByText('已尝试次数')).toBeInTheDocument()
    expect(screen.getByText('9/20')).toBeInTheDocument()
    expect(screen.queryByText('综合评分')).not.toBeInTheDocument()
  }, 20_000)

  it('shows failed tuning task reason in a readable way', async () => {
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{
        task_id: 'tune-failed',
        status: 'failed',
        progress: 20,
        rounds_done: 4,
        rounds_total: 20,
        recommended: false,
        error: 'mock failure',
      }],
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('当前调优结果')

    expect(screen.getAllByText('失败').length).toBeGreaterThan(0)
    expect(screen.getByText('失败原因：mock failure')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
  }, 20_000)

  it('shows non-recommended tuning result with localized validation reason', async () => {
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{
        task_id: 'tune-bad',
        status: 'completed',
        progress: 100,
        rounds_done: 20,
        rounds_total: 20,
        best_score: 0.49,
        recommended: false,
        validation_summary: {
          recommended: false,
          reason: 'holdout_online_like_validation_failed',
          holdout_case_count: 5,
          deltas: {
            score: -0.01,
            precision_at_1: -0.02,
            recall_at_k: -0.01,
            empty_rate: 0.06,
            avg_elapsed_ms: 120,
          },
          online_like: {
            baseline: {
              score: 0.5,
              metrics: {
                precision_at_1: 0.6,
                recall_at_k: 0.5,
                empty_rate: 0.08,
                avg_elapsed_ms: 80,
              },
            },
            best: {
              score: 0.49,
              metrics: {
                precision_at_1: 0.58,
                recall_at_k: 0.49,
                empty_rate: 0.14,
                avg_elapsed_ms: 200,
              },
            },
          },
        },
      }],
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('当前调优结果')

    expect(screen.getByText('验证未通过，不建议应用。')).toBeInTheDocument()
    expect(screen.getByText('0.500 → 0.490 · Δ -0.010')).toBeInTheDocument()
    expect(screen.getByText('未通过，5 个样本')).toBeInTheDocument()
    expect(screen.getByText('原因：独立样本和实际搜索效果验证未通过。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '应用推荐结果' })).toBeDisabled()
  }, 20_000)

  it('applies tuning best profile with persist option enabled', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')

    await user.click(screen.getByLabelText('同时保存为默认设置'))
    await user.click(screen.getByRole('button', { name: '应用推荐结果' }))
    await waitFor(() =>
      expect(memoryApi.applyBestMemoryTuningProfile).toHaveBeenCalledWith('tune-1', {
        persist: true,
        validate: true,
      }),
    )
  }, 20_000)

  it('disables tuning apply button when task is not recommended', async () => {
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [{ task_id: 'tune-2', status: 'completed', recommended: false }],
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')

    expect(screen.getByRole('button', { name: '应用推荐结果' })).toBeDisabled()
  }, 20_000)

  it('uses validation recommendation when enabling tuning apply button', async () => {
    vi.mocked(memoryApi.getMemoryTuningTasks).mockResolvedValue({
      success: true,
      items: [
        {
          task_id: 'tune-3',
          status: 'completed',
          recommended: false,
          validation_summary: { recommended: true },
        },
      ],
    })
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '调优' }))
    await screen.findByText('记忆搜索调优')

    const applyButton = screen.getByRole('button', { name: '应用推荐结果' })
    expect(applyButton).not.toBeDisabled()

    await user.click(applyButton)
    await waitFor(() =>
      expect(memoryApi.applyBestMemoryTuningProfile).toHaveBeenCalledWith('tune-3', {
        persist: false,
        validate: true,
      }),
    )
  }, 20_000)

  it('previews executes and restores source delete (delete module)', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '删除' }))
    await screen.findByText('来源批量删除')

    const sourceCellCandidates = await screen.findAllByText('demo-1')
    const sourceRow = sourceCellCandidates
      .map((item) => item.closest('tr'))
      .find((row): row is HTMLTableRowElement => Boolean(row && within(row).queryByRole('checkbox')))
    if (!sourceRow) {
      throw new Error('missing source row')
    }
    await user.click(within(sourceRow).getByRole('checkbox'))

    await user.click(screen.getByRole('button', { name: '预览删除' }))
    await waitFor(() =>
      expect(memoryApi.previewMemoryDelete).toHaveBeenCalledWith({
        mode: 'source',
        selector: { sources: ['demo-1'] },
        reason: 'knowledge_base_source_delete',
        requested_by: 'knowledge_base',
      }),
    )

    const dialog = await screen.findByTestId('memory-delete-dialog')
    expect(dialog).toHaveTextContent('preview:source:1')

    await user.click(screen.getByRole('button', { name: '执行删除' }))
    await waitFor(() =>
      expect(memoryApi.executeMemoryDelete).toHaveBeenCalledWith({
        mode: 'source',
        selector: { sources: ['demo-1'] },
        reason: 'knowledge_base_source_delete',
        requested_by: 'knowledge_base',
      }),
    )

    await user.click(screen.getByRole('button', { name: '执行恢复' }))
    await waitFor(() =>
      expect(memoryApi.restoreMemoryDelete).toHaveBeenCalledWith({
        operation_id: 'del-2',
        requested_by: 'knowledge_base',
      }),
    )
  }, 20_000)

  it('shows feedback correction history and supports rollback', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '纠错历史' }))
    await screen.findByText('反馈纠错历史')
    await screen.findByText('测试用户最喜欢的颜色是什么')
    await waitFor(() => expect(memoryApi.getMemoryFeedbackCorrection).toHaveBeenCalledWith(11))

    await user.click(screen.getByRole('button', { name: '回退本次纠错' }))
    const rollbackReason = await screen.findByLabelText('回退原因')
    await user.type(rollbackReason, '人工确认回退')
    await user.click(screen.getByRole('button', { name: '确认回退' }))

    await waitFor(() =>
      expect(memoryApi.rollbackMemoryFeedbackCorrection).toHaveBeenCalledWith(11, {
        requested_by: 'knowledge_base',
        reason: '人工确认回退',
      }),
    )
  }, 20_000)

  it('shows memory correction entry and submits preview', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '记忆修正' }))
    await screen.findByLabelText('修正内容')
    await screen.findByText('correction-plan-1')
    await waitFor(() => expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('correction-plan-1'))

    await user.type(screen.getByLabelText('修正内容'), '把测试用户的常住城市改为杭州')
    await user.type(screen.getByLabelText('人物 ID'), 'person-1')
    await user.click(screen.getByRole('button', { name: '生成预览' }))

    await waitFor(() =>
      expect(memoryApi.previewMemoryCorrection).toHaveBeenCalledWith(expect.objectContaining({
        request_text: '把测试用户的常住城市改为杭州',
        scope: 'person_profile',
        person_id: 'person-1',
        limit: 12,
        requested_by: 'knowledge_base',
      })),
    )
  }, 20_000)

  it('selects a chat target before submitting memory correction preview', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '记忆修正' }))
    await screen.findByLabelText('修正内容')

    await user.type(screen.getByLabelText('修正内容'), '把测试用户的常住城市改为杭州')
    await user.type(screen.getByLabelText('人物 ID'), 'person-1')
    const chatInput = screen.getByLabelText('聊天流 ID / 名称')
    await user.type(chatInput, '测试')
    await user.click(screen.getByRole('button', { name: /测试群/ }))

    expect(chatInput).toHaveValue('chat-1')
    await user.click(screen.getByRole('button', { name: '生成预览' }))

    await waitFor(() =>
      expect(memoryApi.previewMemoryCorrection).toHaveBeenCalledWith(expect.objectContaining({
        chat_id: 'chat-1',
      })),
    )
  }, 20_000)

  it('asks for confirmation before executing memory correction plan', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '记忆修正' }))
    await screen.findByText('correction-plan-1')
    await waitFor(() => expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('correction-plan-1'))

    await user.click(screen.getByRole('button', { name: '确认执行' }))
    expect(memoryApi.executeMemoryCorrection).not.toHaveBeenCalled()

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('确认执行记忆修正')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '确认执行' }))

    await waitFor(() =>
      expect(memoryApi.executeMemoryCorrection).toHaveBeenCalledWith({
        plan_id: 'correction-plan-1',
        confirmed: true,
        requested_by: 'knowledge_base',
        reason: '',
      }),
    )
  }, 20_000)

  it('renders audit timeline and jumps to an episode target', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '审计时间线' }))
    expect(await screen.findByText('事件列表')).toBeInTheDocument()
    expect(await screen.findByText('Episode 新增：测试 Episode')).toBeInTheDocument()
    await waitFor(() =>
      expect(memoryApi.getMemoryTimeline).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-1' })),
    )

    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(await screen.findByText('Episode 查询')).toBeInTheDocument()
    await waitFor(() =>
      expect(memoryApi.getMemoryEpisodes).toHaveBeenCalledWith(expect.objectContaining({
        source: 'chat_summary:chat-1',
      })),
    )
    await waitFor(() => expect(memoryApi.getMemoryEpisode).toHaveBeenCalledWith('ep-1'))
  }, 20_000)

  it('submits the explicit Episode source attempt budget', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.processMemoryEpisodePending).mockClear()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '情景记忆' }))
    expect(await screen.findByText('已完成')).toBeInTheDocument()

    const limitInput = screen.getByLabelText('本次处理上限')
    const attemptInput = screen.getByLabelText('最大尝试次数（含首次）')
    expect(limitInput).toHaveAttribute('max', '200')
    expect(attemptInput).toHaveAttribute('max', '20')
    await user.clear(limitInput)
    await user.type(limitInput, '7')
    await user.clear(attemptInput)
    await user.type(attemptInput, '4')
    await user.click(screen.getByRole('button', { name: '处理来源重建任务' }))

    await waitFor(() =>
      expect(memoryApi.processMemoryEpisodePending).toHaveBeenCalledWith({ limit: 7, max_retry: 4 }),
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已处理来源重建任务' }))
  }, 20_000)

  it('does not replace an invalid Episode attempt budget with the default', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.processMemoryEpisodePending).mockClear()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '情景记忆' }))
    const attemptInput = await screen.findByLabelText('最大尝试次数（含首次）')
    await user.clear(attemptInput)
    await user.type(attemptInput, '0')
    await user.click(screen.getByRole('button', { name: '处理来源重建任务' }))

    expect(memoryApi.processMemoryEpisodePending).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '处理参数无效' }))

    await user.clear(attemptInput)
    await user.type(attemptInput, '21')
    await user.click(screen.getByRole('button', { name: '处理来源重建任务' }))

    expect(memoryApi.processMemoryEpisodePending).not.toHaveBeenCalled()
  }, 20_000)

  it('shows the reason returned for an unfinished Episode source task', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.processMemoryEpisodePending).mockResolvedValueOnce({
      success: false,
      processed: 1,
      failed: 0,
      unfinished: 1,
      unfinished_items: [{ source: 'chat:group-1', reason: 'not_claimed' }],
    })
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '情景记忆' }))
    await user.click(screen.getByRole('button', { name: '处理来源重建任务' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '处理来源重建任务失败',
          description: 'chat:group-1: 本轮未领取到该来源任务',
        }),
      ),
    )
  }, 20_000)

  it('prefers a real chat stream over WebUI local chat in audit timeline', async () => {
    const user = userEvent.setup()
    vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValueOnce({
      success: true,
      data: [
        {
          chat_id: 'webui-chat',
          chat_name: 'WebUI用户的私聊',
          platform: 'webui',
          group_id: null,
          user_id: 'webui',
          is_group: false,
        },
        {
          chat_id: 'chat-1',
          chat_name: '测试群',
          platform: 'qq',
          group_id: '10001',
          user_id: null,
          is_group: true,
        },
      ],
    })
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '审计时间线' }))

    await waitFor(() =>
      expect(memoryApi.getMemoryTimeline).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-1' })),
    )
    expect(memoryApi.getMemoryTimeline).not.toHaveBeenCalledWith(expect.objectContaining({ chatId: 'webui-chat' }))
  }, 20_000)

  it('jumps from paragraph timeline event to graph paragraph detail', async () => {
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue({
      success: true,
      chat: {
        chat_id: 'chat-1',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: null,
        is_group: true,
      },
      range: {
        time_start: 1_710_000_000,
        time_end: 1_710_003_600,
        min_time: 1_710_000_000,
        max_time: 1_710_003_600,
      },
      summary: {
        total: 1,
        by_type: { paragraph: 1, paragraph_created: 1 },
      },
      items: [
        {
          event_id: 'paragraph_created:paragraph-jump:1710000100',
          event_type: 'paragraph_created',
          category: 'paragraph',
          occurred_at: 1_710_000_100,
          chat_id: 'chat-1',
          chat_name: '测试群',
          title: '段落新增：跳转段落',
          summary: '跳转段落摘要',
          object_count: 1,
          key_id: 'paragraph-jump',
          source: 'chat_summary:chat-1',
          attribution: 'source',
          metadata: { paragraph_hash: 'paragraph-jump' },
          jump_target: {
            tab: 'graph',
            params: { paragraph_hash: 'paragraph-jump' },
          },
        },
      ],
    })

    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '审计时间线' }))
    expect(await screen.findByText('段落新增：跳转段落')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '跳转' }))

    await waitFor(() => {
      expect(memoryApi.getMemoryGraphParagraphDetail).toHaveBeenCalledWith('paragraph-jump')
    })
    expect(window.location.search).toContain('tab=graph')
    expect(window.location.search).toContain('paragraph_hash=paragraph-jump')
  }, 20_000)

  it('jumps from deleted paragraph timeline event to delete search when operation is missing', async () => {
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue({
      success: true,
      chat: {
        chat_id: 'chat-1',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: null,
        is_group: true,
      },
      range: {
        time_start: 1_710_000_000,
        time_end: 1_710_003_600,
        min_time: 1_710_000_000,
        max_time: 1_710_003_600,
      },
      summary: {
        total: 1,
        by_type: { paragraph: 1, paragraph_deleted: 1 },
      },
      items: [
        {
          event_id: 'paragraph_deleted:paragraph-missing-op:1710000200',
          event_type: 'paragraph_deleted',
          category: 'paragraph',
          occurred_at: 1_710_000_200,
          chat_id: 'chat-1',
          chat_name: '测试群',
          title: '段落删除：缺少操作',
          summary: '删除段落摘要',
          object_count: 1,
          key_id: 'paragraph-missing-op',
          source: 'chat_summary:chat-1',
          attribution: 'source',
          metadata: { paragraph_hash: 'paragraph-missing-op' },
          jump_target: {
            tab: 'delete',
            params: {
              paragraph_hash: 'paragraph-missing-op',
              source: 'chat_summary:chat-1',
            },
          },
        },
      ],
    })

    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '审计时间线' }))
    expect(await screen.findByText('段落删除：缺少操作')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '跳转' }))

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '删除' })).toHaveAttribute('data-state', 'active')
    })
    expect(screen.getByPlaceholderText('搜索 operation / reason / requested_by / source')).toHaveValue('paragraph-missing-op')
    expect(screen.getByPlaceholderText('搜索 source 名称')).toHaveValue('paragraph-missing-op')
    expect(window.location.search).toContain('tab=delete')
    expect(window.location.search).toContain('paragraph_hash=paragraph-missing-op')
  }, 20_000)

  it('paginates audit timeline events and supports page size presets', async () => {
    const timelineEvents: memoryApi.MemoryTimelineEventPayload[] = Array.from({ length: 7 }, (_, index) => {
      const itemNumber = index + 1
      return {
        event_id: `paragraph_created:paragraph-${itemNumber}:1710000${itemNumber}`,
        event_type: 'paragraph_created',
        category: 'paragraph',
        occurred_at: 1_710_000_700 - index,
        chat_id: 'chat-1',
        chat_name: '测试群',
        title: `段落新增：第 ${itemNumber} 条`,
        summary: `分页测试摘要 ${itemNumber}`,
        object_count: 1,
        key_id: `paragraph-${itemNumber}`,
        source: 'chat_summary:chat-1',
        attribution: 'source',
        metadata: { paragraph_hash: `paragraph-${itemNumber}` },
        jump_target: {
          tab: 'graph',
          params: {
            paragraph_hash: `paragraph-${itemNumber}`,
          },
        },
      }
    })
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue({
      success: true,
      chat: {
        chat_id: 'chat-1',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: null,
        is_group: true,
      },
      range: {
        time_start: 1_710_000_000,
        time_end: 1_710_003_600,
        min_time: 1_710_000_000,
        max_time: 1_710_003_600,
      },
      summary: {
        total: timelineEvents.length,
        by_type: { paragraph: timelineEvents.length, paragraph_created: timelineEvents.length },
      },
      items: timelineEvents,
    })

    const user = userEvent.setup()
    renderPage()

    await waitForConsoleReady()
    await user.click(screen.getByRole('tab', { name: '审计时间线' }))
    expect(await screen.findByText('段落新增：第 1 条')).toBeInTheDocument()
    expect(screen.getByText('第 1 / 2 页，每页 5 条')).toBeInTheDocument()
    expect(screen.queryByText('段落新增：第 6 条')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('段落新增：第 6 条')).toBeInTheDocument()
    expect(screen.getByText('第 2 / 2 页，每页 5 条')).toBeInTheDocument()
    expect(screen.queryByText('段落新增：第 1 条')).not.toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: '每页显示条数' }))
    await user.click(await screen.findByRole('option', { name: '10 条' }))
    expect(await screen.findByText('段落新增：第 7 条')).toBeInTheDocument()
    expect(screen.getByText('第 1 / 1 页，每页 10 条')).toBeInTheDocument()
  }, 20_000)
})
