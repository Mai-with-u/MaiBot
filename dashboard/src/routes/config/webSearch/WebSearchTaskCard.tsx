import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { PageFrame } from '@/components/ui/surface'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { WebSearchConfig } from './types'

interface Props {
  config: WebSearchConfig
  selected: string
  onChange: (names: string[]) => void
  onAdd: () => void
}

export function WebSearchTaskCard({ config, selected, onChange, onAdd }: Props) {
  const services = Object.keys(config.services)
  return (
    <div className="space-y-3">
      <h4 className="text-base font-semibold sm:text-lg">联网搜索 (web_search)</h4>
      <p className="text-muted-foreground text-sm">
        选择一个已定义的联网服务，供模型搜索互联网和读取网页。未分配时不开放统一联网工具。
      </p>
      <PageFrame className="space-y-3 p-3">
        <Label htmlFor="web-search-task-service">搜索服务</Label>
        <div className="flex gap-2">
          <Select
            value={selected}
            onValueChange={(name) => onChange([name])}
            disabled={services.length === 0}
          >
            <SelectTrigger id="web-search-task-service" className="min-w-0 flex-1">
              <SelectValue placeholder="选择已添加的联网服务" />
            </SelectTrigger>
            <SelectContent>
              {services.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selected && (
            <Button variant="outline" onClick={() => onChange([])}>
              取消分配
            </Button>
          )}
        </div>
        {services.length === 0 && (
          <p className="text-muted-foreground text-sm">还没有联网服务，请先添加连接配置。</p>
        )}
        <Button variant="outline" onClick={onAdd}>
          添加联网搜索
        </Button>
      </PageFrame>
    </div>
  )
}
