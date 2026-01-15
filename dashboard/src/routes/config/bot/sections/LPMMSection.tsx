import React from 'react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { LPMMKnowledgeConfig } from '../types'

interface LPMMSectionProps {
  config: LPMMKnowledgeConfig
  onChange: (config: LPMMKnowledgeConfig) => void
}

export const LPMMSection = React.memo(function LPMMSection({ config, onChange }: LPMMSectionProps) {
  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <h3 className="text-lg font-semibold">LPMM 知识库设置</h3>
      <div className="grid gap-4">
        <div className="flex items-center space-x-2">
          <Switch
            checked={config.enable}
            onCheckedChange={(checked) => onChange({ ...config, enable: checked })}
          />
          <Label className="cursor-pointer">启用 LPMM 知识库</Label>
        </div>

        {config.enable && (
          <>
            <div className="grid gap-2">
              <Label>LPMM 模式</Label>
              <Select
                value={config.lpmm_mode}
                onValueChange={(value) => onChange({ ...config, lpmm_mode: value })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择 LPMM 模式" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="classic">经典模式</SelectItem>
                  <SelectItem value="agent">Agent 模式</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>同义词搜索 TopK</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.rag_synonym_search_top_k}
                  onChange={(e) =>
                    onChange({ ...config, rag_synonym_search_top_k: parseInt(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>同义词阈值</Label>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={config.rag_synonym_threshold}
                  onChange={(e) =>
                    onChange({ ...config, rag_synonym_threshold: parseFloat(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>实体提取线程数</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.info_extraction_workers}
                  onChange={(e) =>
                    onChange({ ...config, info_extraction_workers: parseInt(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>嵌入向量维度</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.embedding_dimension}
                  onChange={(e) =>
                    onChange({ ...config, embedding_dimension: parseInt(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>嵌入并发线程数</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.max_embedding_workers}
                  onChange={(e) =>
                    onChange({ ...config, max_embedding_workers: parseInt(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>每批嵌入条数</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.embedding_chunk_size}
                  onChange={(e) =>
                    onChange({ ...config, embedding_chunk_size: parseInt(e.target.value) })
                  }
                />
              </div>

              <div className="grid gap-2">
                <Label>同义实体数上限</Label>
                <Input
                  type="number"
                  min="1"
                  value={config.max_synonym_entities}
                  onChange={(e) =>
                    onChange({ ...config, max_synonym_entities: parseInt(e.target.value) })
                  }
                />
              </div>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                checked={config.enable_ppr}
                onCheckedChange={(checked) => onChange({ ...config, enable_ppr: checked })}
              />
              <Label className="cursor-pointer">启用 PPR (低配机器可关闭)</Label>
            </div>
          </>
        )}
      </div>
    </div>
  )
})
