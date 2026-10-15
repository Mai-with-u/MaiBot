import { TabsList, TabsTrigger } from '@/components/ui/tabs'

export function MemorySearchModeTabs() {
  return (
    <TabsList aria-label="记忆查询类型" className="h-9 shrink-0">
      <TabsTrigger value="records" className="h-7 px-2 text-xs">文字记录</TabsTrigger>
      <TabsTrigger value="profiles" className="h-7 px-2 text-xs">人物画像</TabsTrigger>
    </TabsList>
  )
}
