/** 记忆和表达模块公开的模型向量库描述。 */
export interface VectorSpace {
  space_id: string
  embedding_fingerprint: {
    model?: string
    model_identifier?: string
    provider?: string
    base_url?: string
    dimension?: number
    hash?: string
  }
  state: 'active' | 'syncing' | 'saved'
  can_delete: boolean
  vector_count: number
  counts?: Record<string, number>
  size_bytes: number
  last_used_at?: number | null
}

export interface VectorSpaceList {
  success: boolean
  items: VectorSpace[]
  state?: 'syncing' | 'failed' | 'ready' | 'empty'
  last_error?: string
  target_space_id?: string
}
