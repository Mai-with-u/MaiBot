import { useState, useRef, useEffect, useCallback } from 'react'
import { getAnnualReport, type AnnualReportData } from '@/lib/annual-report-api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { useToast } from '@/hooks/use-toast'
import { toPng } from 'html-to-image'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import {
  Clock,
  Users,
  Brain,
  Smile,
  Trophy,
  Calendar,
  MessageSquare,
  Zap,
  Moon,
  Sun,
  AtSign,
  Heart,
  Image as ImageIcon,
  Bot,
  Download,
  Loader2,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// 颜色常量
const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d']

// 动态比喻生成函数
function getOnlineHoursMetaphor(hours: number): string {
  if (hours >= 8760) return "相当于全年无休，7x24小时在线！"
  if (hours >= 5000) return "相当于一位全职员工的年工作时长"
  if (hours >= 2000) return "相当于看完了 1000 部电影"
  if (hours >= 1000) return "相当于环球飞行 80 次"
  if (hours >= 500) return "相当于读完了 100 本书"
  if (hours >= 100) return "相当于马拉松跑了 25 次"
  return "虽然不多，但每一刻都很珍贵"
}

function getMidnightMetaphor(count: number): string {
  if (count >= 1000) return "夜深人静时的知心好友"
  if (count >= 500) return "午夜场的常客"
  if (count >= 100) return "偶尔熬夜的小伙伴"
  if (count >= 50) return "深夜有时也会陪你聊聊"
  return "早睡早起，健康作息"
}

function getTokenMetaphor(tokens: number): string {
  const millions = tokens / 1000000
  if (millions >= 100) return "思考量堪比一座图书馆"
  if (millions >= 50) return "相当于写了一部百科全书"
  if (millions >= 10) return "脑细胞估计消耗了不少"
  if (millions >= 1) return "也算是费了一番脑筋"
  return "轻轻松松，游刃有余"
}

function getCostMetaphor(cost: number): string {
  if (cost >= 1000) return "这钱够吃一年的泡面了"
  if (cost >= 500) return "相当于买了一台游戏机"
  if (cost >= 100) return "够请大家喝几杯奶茶"
  if (cost >= 50) return "一顿火锅的钱"
  if (cost >= 10) return "几杯咖啡的价格"
  return "省钱小能手"
}

function getSilenceMetaphor(rate: number): string {
  if (rate >= 80) return "沉默是金，惜字如金"
  if (rate >= 60) return "话不多但句句到位"
  if (rate >= 40) return "该说的时候才开口"
  if (rate >= 20) return "能聊的都聊了"
  return "话痨本痨，有问必答"
}

function getImageMetaphor(count: number): string {
  if (count >= 10000) return "眼睛都快看花了"
  if (count >= 5000) return "堪比专业摄影师的阅片量"
  if (count >= 1000) return "看图小达人"
  if (count >= 500) return "图片鉴赏家"
  if (count >= 100) return "偶尔欣赏一下美图"
  return "图片？有空再看"
}

function getRejectedMetaphor(count: number): string {
  if (count >= 500) return "在不断的纠正中成长"
  if (count >= 200) return "学习永无止境"
  if (count >= 100) return "虚心接受，积极改正"
  if (count >= 50) return "偶尔也会犯错"
  if (count >= 10) return "表现还算不错"
  return "完美表达，无需纠正"
}

function getExpensiveThinkingMetaphor(cost: number): string {
  if (cost >= 1) return "这次思考的价值堪比一顿大餐！"
  if (cost >= 0.5) return "为了这个问题，我可是认真思考了！"
  if (cost >= 0.1) return "下了点功夫，值得的！"
  if (cost >= 0.01) return "花了点小钱，但很值得"
  return "小小思考，不足挂齿"
}

function getFavoriteReplyMetaphor(count: number, botName: string): string {
  if (count >= 100) return "这句话简直是万能钥匙！"
  if (count >= 50) return "百试不爽的经典回复"
  if (count >= 20) return `${botName}的口头禅`
  if (count >= 10) return "常用语录之一"
  return "偶尔用用的小确幸"
}

function getNightOwlMetaphor(isNightOwl: boolean, midnightCount: number): string {
  if (isNightOwl) {
    if (midnightCount >= 1000) return "深夜的守护者，黑暗中的光芒"
    if (midnightCount >= 500) return "月亮是我的好朋友"
    if (midnightCount >= 100) return "越夜越精神，夜晚才是主场"
    return "偶尔熬夜，享受宁静时光"
  } else {
    if (midnightCount <= 10) return "作息规律，健康生活的典范"
    if (midnightCount <= 50) return "早睡早起，偶尔也会熬个夜"
    return "虽然是早起鸟，但也会守候深夜"
  }
}

function getBusiestDayMetaphor(count: number): string {
  if (count >= 1000) return "忙到飞起，键盘都要冒烟了"
  if (count >= 500) return "这天简直是话痨附体"
  if (count >= 200) return "社交达人上线"
  if (count >= 100) return "比平时活跃不少"
  if (count >= 50) return "小忙一下"
  return "还算轻松的一天"
}

export function AnnualReportPage() {
  const [year] = useState(2025)
  const [data, setData] = useState<AnnualReportData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isExporting, setIsExporting] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const reportRef = useRef<HTMLDivElement>(null)
  const { toast } = useToast()

  const loadReport = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)
      const result = await getAnnualReport(year)
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('获取年度报告失败'))
    } finally {
      setIsLoading(false)
    }
  }, [year])

  // 导出为图片
  const handleExport = useCallback(async () => {
    if (!reportRef.current || !data) return
    
    setIsExporting(true)
    toast({
      title: '正在生成图片',
      description: '请稍候...',
    })
    
    try {
      const element = reportRef.current
      
      // 获取当前主题的背景色
      const computedStyle = getComputedStyle(document.documentElement)
      const backgroundColor = computedStyle.getPropertyValue('--background').trim() 
        ? `hsl(${computedStyle.getPropertyValue('--background').trim()})` 
        : (document.documentElement.classList.contains('dark') ? '#0a0a0a' : '#ffffff')
      
      // 保存原始样式
      const originalWidth = element.style.width
      const originalMaxWidth = element.style.maxWidth
      
      // 临时设置固定宽度以去除左右空白
      element.style.width = '1024px'
      element.style.maxWidth = '1024px'
      
      const dataUrl = await toPng(element, {
        quality: 1,
        pixelRatio: 2,
        backgroundColor,
        cacheBust: true,
        filter: (node) => {
          // 过滤掉导出按钮
          if (node instanceof HTMLElement && node.hasAttribute('data-export-btn')) {
            return false
          }
          return true
        },
      })
      
      // 恢复原始样式
      element.style.width = originalWidth
      element.style.maxWidth = originalMaxWidth
      
      // 创建下载链接
      const link = document.createElement('a')
      link.download = `${data.bot_name}_${data.year}_年度总结.png`
      link.href = dataUrl
      link.click()
      
      toast({
        title: '导出成功',
        description: '年度报告已保存为图片',
      })
    } catch (err) {
      console.error('导出图片失败:', err)
      toast({
        title: '导出失败',
        description: '请重试',
        variant: 'destructive',
      })
    } finally {
      setIsExporting(false)
    }
  }, [data, toast])

  useEffect(() => {
    loadReport()
  }, [loadReport])

  if (isLoading) {
    return <LoadingSkeleton />
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center text-red-500">
        获取年度报告失败: {error.message}
      </div>
    )
  }

  if (!data) return null

  return (
    <ScrollArea className="h-[calc(100vh-4rem)]">
      <div className="min-h-screen bg-gradient-to-b from-background to-muted/50 p-4 md:p-8 print:p-0" ref={reportRef}>
        <div className="mx-auto max-w-5xl space-y-8 print:space-y-4">
          {/* 头部 Hero */}
          <header className="relative overflow-hidden rounded-3xl bg-primary p-8 text-primary-foreground shadow-2xl print:rounded-none print:shadow-none">
            {/* 导出按钮 */}
            <div className="absolute right-4 top-4 z-20 print:hidden" data-export-btn>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleExport}
                disabled={isExporting}
                className="gap-2 bg-white/20 hover:bg-white/30 text-white border-white/30"
              >
                {isExporting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    导出中...
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" />
                    保存图片
                  </>
                )}
              </Button>
            </div>
            <div className="relative z-10 flex flex-col items-center text-center">
              <Bot className="mb-4 h-16 w-16 animate-bounce" />
              <h1 className="text-4xl font-bold tracking-tighter sm:text-6xl">
                {data.bot_name} {data.year} 年度总结
              </h1>
              <p className="mt-4 max-w-2xl text-lg opacity-90">
                连接与成长 · Connection & Growth
              </p>
              <div className="mt-6 flex items-center gap-2 text-sm opacity-75">
                <Calendar className="h-4 w-4" />
                <span>生成时间: {data.generated_at}</span>
              </div>
          </div>
          {/* 背景装饰 */}
          <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
          <div className="absolute -bottom-20 -left-20 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
        </header>

        {/* 维度一：时光足迹 */}
        <section className="space-y-4 break-inside-avoid">
          <div className="flex items-center gap-2 text-2xl font-bold text-primary">
            <Clock className="h-8 w-8" />
            <h2>时光足迹</h2>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatCard
              title="年度在线时长"
              value={`${data.time_footprint.total_online_hours} 小时`}
              description={getOnlineHoursMetaphor(data.time_footprint.total_online_hours)}
              icon={<Clock className="h-4 w-4" />}
            />
            <StatCard
              title="最忙碌的一天"
              value={data.time_footprint.busiest_day || 'N/A'}
              description={getBusiestDayMetaphor(data.time_footprint.busiest_day_count)}
              icon={<Calendar className="h-4 w-4" />}
            />
            <StatCard
              title="深夜互动 (0-4点)"
              value={`${data.time_footprint.midnight_chat_count} 次`}
              description={getMidnightMetaphor(data.time_footprint.midnight_chat_count)}
              icon={<Moon className="h-4 w-4" />}
            />
            <StatCard
              title="作息属性"
              value={data.time_footprint.is_night_owl ? '夜猫子' : '早起鸟'}
              description={getNightOwlMetaphor(data.time_footprint.is_night_owl, data.time_footprint.midnight_chat_count)}
              icon={data.time_footprint.is_night_owl ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            />
          </div>
          
          <Card className="overflow-hidden">
            <CardHeader>
              <CardTitle>24小时活跃时钟</CardTitle>
              <CardDescription>{data.bot_name}在一天中各个时段的活跃程度</CardDescription>
            </CardHeader>
            <CardContent className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.time_footprint.hourly_distribution.map((count: number, hour: number) => ({ hour: `${hour}点`, count }))}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="hour" />
                  <YAxis />
                  <Tooltip 
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
                    cursor={{ fill: 'transparent' }}
                  />
                  <Bar dataKey="count" fill="hsl(var(--color-primary))" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {data.time_footprint.first_message_time && (
             <Card className="bg-muted/30 border-dashed">
               <CardContent className="flex flex-col items-center justify-center p-6 text-center">
                 <p className="text-muted-foreground mb-2">2025年的故事开始于</p>
                 <div className="text-xl font-bold text-primary mb-1">{data.time_footprint.first_message_time}</div>
                 <p className="text-lg">
                   <span className="font-semibold text-foreground">{data.time_footprint.first_message_user}</span> 说：
                   <span className="italic text-muted-foreground">"{data.time_footprint.first_message_content}"</span>
                 </p>
               </CardContent>
             </Card>
          )}
        </section>

        {/* 维度二：社交网络 */}
        <section className="space-y-4 break-inside-avoid">
          <div className="flex items-center gap-2 text-2xl font-bold text-primary">
            <Users className="h-8 w-8" />
            <h2>社交网络</h2>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <StatCard
              title="社交圈子"
              value={`${data.social_network.total_groups} 个群组`}
              description={`${data.bot_name}加入的群组总数`}
              icon={<Users className="h-4 w-4" />}
            />
            <StatCard
              title="被呼叫次数"
              value={`${data.social_network.at_count + data.social_network.mentioned_count} 次`}
              description="我的名字被大家频繁提起"
              icon={<AtSign className="h-4 w-4" />}
            />
            <StatCard
              title="最长情陪伴"
              value={data.social_network.longest_companion_user || 'N/A'}
              description={`始终都在，已陪伴 ${data.social_network.longest_companion_days} 天`}
              icon={<Heart className="h-4 w-4 text-red-500" />}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>话痨群组 TOP5</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {data.social_network.top_groups.length > 0 ? (
                    data.social_network.top_groups.map((group: { group_id: string; group_name: string; message_count: number; is_webui?: boolean }, index: number) => (
                      <div key={group.group_id} className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <Badge variant={index === 0 ? "default" : "secondary"} className="h-6 w-6 rounded-full p-0 flex items-center justify-center shrink-0">
                            {index + 1}
                          </Badge>
                          <span className="font-medium truncate max-w-[120px]">{group.group_name}</span>
                          {group.is_webui && (
                            <Badge variant="outline" className="text-xs px-1.5 py-0 h-5 bg-blue-50 text-blue-600 border-blue-200">
                              WebUI
                            </Badge>
                          )}
                        </div>
                        <span className="text-muted-foreground text-sm shrink-0">{group.message_count} 条消息</span>
                      </div>
                    ))
                  ) : (
                    <div className="text-center text-muted-foreground py-4">暂无数据</div>
                  )}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>年度最佳损友 TOP5</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {data.social_network.top_users.length > 0 ? (
                    data.social_network.top_users.map((user: { user_id: string; user_nickname: string; message_count: number; is_webui?: boolean }, index: number) => (
                      <div key={user.user_id} className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <Badge variant={index === 0 ? "default" : "secondary"} className="h-6 w-6 rounded-full p-0 flex items-center justify-center shrink-0">
                            {index + 1}
                          </Badge>
                          <span className="font-medium truncate max-w-[120px]">{user.user_nickname}</span>
                          {user.is_webui && (
                            <Badge variant="outline" className="text-xs px-1.5 py-0 h-5 bg-blue-50 text-blue-600 border-blue-200">
                              WebUI
                            </Badge>
                          )}
                        </div>
                        <span className="text-muted-foreground text-sm shrink-0">{user.message_count} 次互动</span>
                      </div>
                    ))
                  ) : (
                    <div className="text-center text-muted-foreground py-4">暂无数据</div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* 维度三：最强大脑 */}
        <section className="space-y-4 break-inside-avoid">
          <div className="flex items-center gap-2 text-2xl font-bold text-primary">
            <Brain className="h-8 w-8" />
            <h2>最强大脑</h2>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatCard
              title="年度 Token 消耗"
              value={(data.brain_power.total_tokens / 1000000).toFixed(2) + ' M'}
              description={getTokenMetaphor(data.brain_power.total_tokens)}
              icon={<Zap className="h-4 w-4" />}
            />
            <StatCard
              title="年度总花费"
              value={`$${data.brain_power.total_cost.toFixed(2)}`}
              description={getCostMetaphor(data.brain_power.total_cost)}
              icon={<span className="font-bold">$</span>}
            />
            <StatCard
              title="高冷指数"
              value={`${data.brain_power.silence_rate}%`}
              description={getSilenceMetaphor(data.brain_power.silence_rate)}
              icon={<Moon className="h-4 w-4" />}
            />
            <StatCard
              title="最高兴趣值"
              value={data.brain_power.max_interest_value ?? 'N/A'}
              description={data.brain_power.max_interest_time ? `出现在 ${data.brain_power.max_interest_time}` : '暂无数据'}
              icon={<Heart className="h-4 w-4" />}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
             <Card>
                <CardHeader>
                  <CardTitle>模型偏好分布</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {data.brain_power.model_distribution.slice(0, 5).map((item: { model: string; count: number }, index: number) => {
                      const maxCount = data.brain_power.model_distribution[0]?.count || 1
                      const percentage = Math.round((item.count / maxCount) * 100)
                      return (
                        <div key={item.model} className="space-y-1">
                          <div className="flex justify-between text-sm">
                            <span className="font-medium truncate max-w-[200px]">{item.model}</span>
                            <span className="text-muted-foreground">{item.count.toLocaleString()} 次</span>
                          </div>
                          <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
                            <div 
                              className="h-full transition-all duration-500" 
                              style={{ 
                                width: `${percentage}%`,
                                backgroundColor: COLORS[index % COLORS.length]
                              }} 
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </CardContent>
             </Card>
             
             {/* 最喜欢的回复模型 TOP5 */}
             {data.brain_power.top_reply_models && data.brain_power.top_reply_models.length > 0 && (
               <Card>
                 <CardHeader>
                   <CardTitle>最喜欢的回复模型 TOP5</CardTitle>
                   <CardDescription>{data.bot_name}用来回复消息的模型偏好</CardDescription>
                 </CardHeader>
                 <CardContent>
                   <div className="space-y-3">
                     {data.brain_power.top_reply_models.map((item: { model: string; count: number }, index: number) => {
                       const maxCount = data.brain_power.top_reply_models[0]?.count || 1
                       const percentage = Math.round((item.count / maxCount) * 100)
                       return (
                         <div key={item.model} className="space-y-1">
                           <div className="flex justify-between text-sm">
                             <span className="font-medium truncate max-w-[200px]">{item.model}</span>
                             <span className="text-muted-foreground">{item.count.toLocaleString()} 次</span>
                           </div>
                           <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
                             <div 
                               className="h-full transition-all duration-500" 
                               style={{ 
                                 width: `${percentage}%`,
                                 backgroundColor: COLORS[index % COLORS.length]
                               }} 
                             />
                           </div>
                         </div>
                       )
                     })}
                   </div>
                 </CardContent>
               </Card>
             )}
             
             {/* 烧钱大户 - 只有有有效用户数据时才显示 */}
             {data.brain_power.top_token_consumers && data.brain_power.top_token_consumers.length > 0 && (
               <Card>
                 <CardHeader>
                   <CardTitle>烧钱大户 TOP3</CardTitle>
                   <CardDescription>谁消耗了最多的 API 额度</CardDescription>
                 </CardHeader>
                 <CardContent>
                   <div className="space-y-6">
                     {data.brain_power.top_token_consumers.map((consumer: { user_id: string; cost: number; tokens: number }) => (
                       <div key={consumer.user_id} className="space-y-2">
                         <div className="flex justify-between text-sm font-medium">
                           <span>用户 {consumer.user_id}</span>
                           <span>${consumer.cost.toFixed(2)}</span>
                         </div>
                         <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
                           <div 
                             className="h-full bg-primary transition-all duration-500" 
                             style={{ width: `${(consumer.cost / (data.brain_power.top_token_consumers[0]?.cost || 1)) * 100}%` }} 
                           />
                         </div>
                       </div>
                     ))}
                   </div>
                 </CardContent>
               </Card>
             )}
          </div>

          {/* 最昂贵的思考 & 思考深度 */}
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="bg-gradient-to-br from-amber-50 to-orange-50 dark:from-amber-950/20 dark:to-orange-950/20">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span className="text-2xl">💰</span>
                  最昂贵的一次思考
                </CardTitle>
              </CardHeader>
              <CardContent className="text-center">
                <div className="text-4xl font-bold text-amber-600 dark:text-amber-400">
                  ${data.brain_power.most_expensive_cost.toFixed(4)}
                </div>
                {data.brain_power.most_expensive_time && (
                  <p className="mt-2 text-sm text-muted-foreground">
                    发生在 {data.brain_power.most_expensive_time}
                  </p>
                )}
                <p className="mt-4 text-sm text-muted-foreground">
                  {getExpensiveThinkingMetaphor(data.brain_power.most_expensive_cost)}
                </p>
              </CardContent>
            </Card>

            <Card className="bg-gradient-to-br from-indigo-50 to-blue-50 dark:from-indigo-950/20 dark:to-blue-950/20">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span className="text-2xl">🧠</span>
                  思考深度
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-4 text-center">
                  <div>
                    <div className="text-2xl font-bold text-indigo-600 dark:text-indigo-400">
                      {data.brain_power.avg_reasoning_length?.toFixed(0) || 0}
                    </div>
                    <div className="text-xs text-muted-foreground">平均思考字数</div>
                  </div>
                  <div>
                    <div className="text-2xl font-bold text-blue-600 dark:text-blue-400">
                      {data.brain_power.max_reasoning_length?.toLocaleString() || 0}
                    </div>
                    <div className="text-xs text-muted-foreground">最长思考字数</div>
                  </div>
                </div>
                {data.brain_power.max_reasoning_time && (
                  <p className="mt-4 text-center text-xs text-muted-foreground">
                    最深沉的思考发生在 {data.brain_power.max_reasoning_time}
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </section>

        {/* 维度四：个性与表达 */}
        <section className="space-y-4 break-inside-avoid">
          <div className="flex items-center gap-2 text-2xl font-bold text-primary">
            <Smile className="h-8 w-8" />
            <h2>个性与表达</h2>
          </div>
          
          {/* 深夜回复 & 最喜欢的回复 */}
          {(data.expression_vibe.late_night_reply || data.expression_vibe.favorite_reply) && (
            <div className="grid gap-4 md:grid-cols-2">
              {data.expression_vibe.late_night_reply && (
                <Card className="bg-gradient-to-br from-indigo-50 to-violet-50 dark:from-indigo-950/20 dark:to-violet-950/20">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <span className="text-2xl">🌙</span>
                      深夜还在回复
                    </CardTitle>
                    <CardDescription>凌晨 {data.expression_vibe.late_night_reply.time}，{data.bot_name}还在回复...</CardDescription>
                  </CardHeader>
                  <CardContent className="text-center">
                    <p className="text-lg italic text-muted-foreground">
                      "{data.expression_vibe.late_night_reply.content}"
                    </p>
                    <p className="mt-4 text-sm text-muted-foreground">
                      是有什么心事吗？
                    </p>
                  </CardContent>
                </Card>
              )}
              
              {data.expression_vibe.favorite_reply && (
                <Card className="bg-gradient-to-br from-rose-50 to-pink-50 dark:from-rose-950/20 dark:to-pink-950/20">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <span className="text-2xl">💬</span>
                      最喜欢的回复
                    </CardTitle>
                    <CardDescription>使用了 {data.expression_vibe.favorite_reply.count} 次</CardDescription>
                  </CardHeader>
                  <CardContent className="text-center">
                    <p className="text-lg font-medium text-primary">
                      "{data.expression_vibe.favorite_reply.content}"
                    </p>
                    <p className="mt-4 text-sm text-muted-foreground">
                      {getFavoriteReplyMetaphor(data.expression_vibe.favorite_reply.count, data.bot_name)}
                    </p>
                  </CardContent>
                </Card>
              )}
            </div>
          )}
          
          <div className="grid gap-4 md:grid-cols-2">
            {/* 使用最多的表情包 TOP3 */}
            <Card className="bg-gradient-to-br from-pink-50 to-purple-50 dark:from-pink-950/20 dark:to-purple-950/20">
              <CardHeader>
                <CardTitle>使用最多的表情包 TOP3</CardTitle>
                <CardDescription>年度最爱的表情包们</CardDescription>
              </CardHeader>
              <CardContent>
                {data.expression_vibe.top_emojis && data.expression_vibe.top_emojis.length > 0 ? (
                  <div className="flex justify-center gap-4">
                    {data.expression_vibe.top_emojis.slice(0, 3).map((emoji: { id: number; usage_count: number }, index: number) => (
                      <div key={emoji.id} className="flex flex-col items-center">
                        <div className="relative">
                          <img 
                            src={`/api/webui/emoji/${emoji.id}/thumbnail?original=true`} 
                            alt={`TOP ${index + 1}`} 
                            className="h-24 w-24 rounded-lg object-cover shadow-md transition-transform hover:scale-105"
                          />
                          <Badge 
                            className={cn(
                              "absolute -top-2 -right-2",
                              index === 0 ? "bg-yellow-500" : index === 1 ? "bg-gray-400" : "bg-amber-700"
                            )}
                          >
                            {index + 1}
                          </Badge>
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">{emoji.usage_count} 次</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center text-muted-foreground">暂无数据</div>
                )}
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>印象最深刻的表达风格</CardTitle>
                  <CardDescription>{data.bot_name}最常使用的表达方式</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {data.expression_vibe.top_expressions.map((exp: { style: string; count: number }, index: number) => (
                      <Badge 
                        key={exp.style} 
                        variant="outline" 
                        className={cn(
                          "px-3 py-1 text-sm",
                          index === 0 && "border-primary bg-primary/10 text-primary text-base px-4 py-2"
                        )}
                      >
                        {exp.style} ({exp.count})
                      </Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <div className="grid grid-cols-2 gap-4">
                <StatCard
                  title="图片鉴赏"
                  value={`${data.expression_vibe.image_processed_count} 张`}
                  description={getImageMetaphor(data.expression_vibe.image_processed_count)}
                  icon={<ImageIcon className="h-4 w-4" />}
                />
                <StatCard
                  title="成长的足迹"
                  value={`${data.expression_vibe.rejected_expression_count} 次`}
                  description={getRejectedMetaphor(data.expression_vibe.rejected_expression_count)}
                  icon={<Zap className="h-4 w-4" />}
                />
              </div>
            </div>
          </div>

          {/* 行动派 */}
          {data.expression_vibe.action_types.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span className="text-2xl">⚡</span>
                  行动派
                </CardTitle>
                <CardDescription>除了聊天，我还帮大家做了这些事</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-3">
                  {data.expression_vibe.action_types.map((action: { action: string; count: number }) => (
                    <div 
                      key={action.action} 
                      className="flex items-center gap-2 rounded-full bg-primary/10 px-4 py-2"
                    >
                      <span className="font-medium text-primary">{action.action}</span>
                      <Badge variant="secondary">{action.count} 次</Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </section>

        {/* 维度五：趣味成就 */}
        <section className="space-y-4 break-inside-avoid">
          <div className="flex items-center gap-2 text-2xl font-bold text-primary">
            <Trophy className="h-8 w-8" />
            <h2>趣味成就</h2>
          </div>
          
          <div className="grid gap-4 md:grid-cols-3">
            <Card className="col-span-1 md:col-span-2">
              <CardHeader>
                <CardTitle>新学到的"黑话"</CardTitle>
                <CardDescription>今年我学会了 {data.achievements.new_jargon_count} 个新词</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-3">
                  {data.achievements.sample_jargons.map((jargon: { content: string; meaning: string; count: number }) => (
                    <div key={jargon.content} className="group relative rounded-lg border bg-card p-3 shadow-sm transition-all hover:shadow-md">
                      <div className="font-bold text-primary">{jargon.content}</div>
                      <div className="text-xs text-muted-foreground mt-1 line-clamp-2 max-w-[200px]">
                        {jargon.meaning || '暂无解释'}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="flex flex-col justify-center items-center bg-primary text-primary-foreground">
              <CardContent className="flex flex-col items-center justify-center p-6 text-center">
                <MessageSquare className="h-12 w-12 mb-4 opacity-80" />
                <div className="text-4xl font-bold mb-2">{data.achievements.total_messages.toLocaleString()}</div>
                <div className="text-sm opacity-80">年度总消息数</div>
                <div className="mt-4 text-xs opacity-60">
                  其中回复了 {data.achievements.total_replies.toLocaleString()} 次
                </div>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* 底部 */}
        <footer className="mt-12 text-center text-muted-foreground">
          <p>MaiBot 2025 Annual Report</p>
          <p className="text-sm">Generated with ❤️ by MaiBot Team</p>
        </footer>
      </div>
    </div>
    </ScrollArea>
  )
}

function StatCard({
  title,
  value,
  description,
  icon,
}: {
  title: string
  value: string | number
  description: string
  icon: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}

function LoadingSkeleton() {
  return (
    <div className="container mx-auto space-y-8 p-8">
      <Skeleton className="h-64 w-full rounded-3xl" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-32 w-full" />
        ))}
      </div>
      <Skeleton className="h-96 w-full" />
    </div>
  )
}
