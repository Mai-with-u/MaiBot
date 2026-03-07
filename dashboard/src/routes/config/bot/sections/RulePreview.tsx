
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import { Eye } from 'lucide-react'

interface RulePreviewProps {
  rule: {
    target: string
    time: string
    value: number
  }
}

// 预览窗口组件
export function RulePreview({ rule }: RulePreviewProps) {
  const previewText = `{ target = "${rule.target}", time = "${rule.time}", value = ${rule.value.toFixed(1)} }`
  
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <Eye className="h-4 w-4 mr-1" />
          预览
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 sm:w-96">
        <div className="space-y-2">
          <h4 className="font-medium text-sm">配置预览</h4>
          <div className="rounded-md bg-muted p-3 font-mono text-xs break-all">
            {previewText}
          </div>
          <p className="text-xs text-muted-foreground">
            这是保存到 bot_config.toml 文件中的格式
          </p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
