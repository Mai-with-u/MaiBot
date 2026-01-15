/**
 * 回复器监控组件
 */
import { Clock, TrendingUp, FileText, Zap, Brain, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, ArrowLeft, MessageSquare, CheckCircle, XCircle, Cpu, Search } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useState, useEffect, useCallback } from 'react'
import { 
  getReplierOverview, 
  getReplyChatLogs, 
  getReplyLogDetail, 
  type ReplierOverview, 
  type ReplyLogDetail, 
  type PaginatedReplyLogs,
  type ReplierChatSummary 
} from '@/lib/planner-api'
import { Skeleton } from '@/components/ui/skeleton'
import { useChatNameMap, formatTimestamp, formatRelativeTime, useAutoRefresh } from './use-monitor'

interface ReplierMonitorProps {
  autoRefresh: boolean
  refreshKey: number
}

export function ReplierMonitor({ autoRefresh, refreshKey }: ReplierMonitorProps) {
  // 视图状态: 'overview' | 'chat-logs'
  const [view, setView] = useState<'overview' | 'chat-logs'>('overview')
  const [selectedChat, setSelectedChat] = useState<ReplierChatSummary | null>(null)
  
  // 聊天名称映射
  const { getChatName } = useChatNameMap()
  
  // 总览数据
  const [overview, setOverview] = useState<ReplierOverview | null>(null)
  const [overviewLoading, setOverviewLoading] = useState(true)
  
  // 聊天日志数据
  const [chatLogs, setChatLogs] = useState<PaginatedReplyLogs | null>(null)
  const [chatLogsLoading, setChatLogsLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchInput, setSearchInput] = useState('')
  
  // 详情弹窗
  const [selectedLog, setSelectedLog] = useState<ReplyLogDetail | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  // 加载总览数据
  const loadOverview = useCallback(async () => {
    try {
      setOverviewLoading(true)
      const data = await getReplierOverview()
      setOverview(data)
    } catch (error) {
      console.error('加载回复器总览失败:', error)
    } finally {
      setOverviewLoading(false)
    }
  }, [])

  // 加载聊天日志
  const loadChatLogs = useCallback(async () => {
    if (!selectedChat) return
    try {
      setChatLogsLoading(true)
      const data = await getReplyChatLogs(selectedChat.chat_id, page, pageSize, searchQuery || undefined)
      setChatLogs(data)
    } catch (error) {
      console.error('加载聊天日志失败:', error)
    } finally {
      setChatLogsLoading(false)
    }
  }, [selectedChat, page, pageSize, searchQuery])

  // 初始加载
  useEffect(() => {
    loadOverview()
  }, [loadOverview])

  // 响应外部刷新
  useEffect(() => {
    if (refreshKey > 0) {
      if (view === 'overview') {
        loadOverview()
      } else {
        loadChatLogs()
      }
    }
  }, [refreshKey, view, loadOverview, loadChatLogs])

  // 加载聊天日志
  useEffect(() => {
    if (view === 'chat-logs' && selectedChat) {
      loadChatLogs()
    }
  }, [view, selectedChat, loadChatLogs])

  // 自动刷新
  useAutoRefresh(
    autoRefresh,
    useCallback(() => {
      if (view === 'overview') {
        loadOverview()
      } else {
        loadChatLogs()
      }
    }, [view, loadOverview, loadChatLogs])
  )

  const handleChatClick = (chat: ReplierChatSummary) => {
    setSelectedChat(chat)
    setPage(1)
    setSearchQuery('')
    setSearchInput('')
    setView('chat-logs')
  }

  const handleBackToOverview = () => {
    setView('overview')
    setSelectedChat(null)
    setChatLogs(null)
    setSearchQuery('')
    setSearchInput('')
  }

  const handleSearch = () => {
    setSearchQuery(searchInput)
    setPage(1)
  }

  const handleClearSearch = () => {
    setSearchInput('')
    setSearchQuery('')
    setPage(1)
  }

  const handleLogClick = async (chatId: string, filename: string) => {
    try {
      setDetailLoading(true)
      setDialogOpen(true)
      const detail = await getReplyLogDetail(chatId, filename)
      setSelectedLog(detail)
    } catch (error) {
      console.error('加载回复详情失败:', error)
    } finally {
      setDetailLoading(false)
    }
  }

  const handlePageSizeChange = (value: string) => {
    setPageSize(Number(value))
    setPage(1)
  }

  const handleJumpToPage = () => {
    const targetPage = parseInt(jumpToPage)
    const totalPages = chatLogs ? Math.ceil(chatLogs.total / chatLogs.page_size) : 0
    if (!isNaN(targetPage) && targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    }
  }

  const totalPages = chatLogs ? Math.ceil(chatLogs.total / chatLogs.page_size) : 0

  return (
    <>
      <div className="space-y-4">
        {view === 'overview' ? (
          // ========== 第一级：总览视图 ==========
          <>
            {/* 统计卡片 */}
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">聊天数量</CardTitle>
                  <MessageSquare className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {overviewLoading ? (
                    <Skeleton className="h-8 w-16" />
                  ) : (
                    <div className="text-2xl font-bold">{overview?.total_chats || 0}</div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">回复总数</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {overviewLoading ? (
                    <Skeleton className="h-8 w-16" />
                  ) : (
                    <div className="text-2xl font-bold">{overview?.total_replies || 0}</div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* 聊天卡片列表 */}
            <Card>
              <CardHeader>
                <CardTitle>聊天列表</CardTitle>
                <CardDescription>点击查看该聊天的所有回复记录</CardDescription>
              </CardHeader>
              <CardContent>
                {overviewLoading ? (
                  <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                    {[...Array(6)].map((_, i) => (
                      <Skeleton key={i} className="h-24 w-full" />
                    ))}
                  </div>
                ) : overview?.chats && overview.chats.length > 0 ? (
                  <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                    {overview.chats.map((chat) => (
                      <div
                        key={chat.chat_id}
                        className="border rounded-lg p-4 hover:bg-accent/50 transition-colors cursor-pointer"
                        onClick={() => handleChatClick(chat)}
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <MessageSquare className="h-4 w-4 text-muted-foreground" />
                            <span className="text-sm truncate max-w-[180px]" title={getChatName(chat.chat_id)}>
                              {getChatName(chat.chat_id)}
                            </span>
                          </div>
                          <Badge variant="secondary">{chat.reply_count}</Badge>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          最后活动: {formatRelativeTime(chat.latest_timestamp)}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">暂无聊天记录</div>
                )}
              </CardContent>
            </Card>
          </>
        ) : (
          // ========== 第二级：聊天日志列表 ==========
          <>
            {/* 返回按钮和聊天信息 */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 mb-4">
              <Button variant="outline" size="sm" onClick={handleBackToOverview}>
                <ArrowLeft className="h-4 w-4 mr-2" />
                返回聊天列表
              </Button>
              <div className="text-sm text-muted-foreground">
                当前聊天: <span className="font-medium">{selectedChat ? getChatName(selectedChat.chat_id) : ''}</span>
                <span className="mx-2">•</span>
                共 {chatLogs?.total || 0} 条回复记录
              </div>
            </div>

            <Card>
              <CardHeader>
                <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                  <div>
                    <CardTitle>回复生成记录</CardTitle>
                    <CardDescription>
                      {selectedChat ? getChatName(selectedChat.chat_id) : ''}
                    </CardDescription>
                  </div>
                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                    <div className="flex items-center gap-1">
                      <Input
                        placeholder="搜索提示词内容..."
                        value={searchInput}
                        onChange={(e) => setSearchInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                        className="w-full sm:w-48"
                      />
                      <Button variant="outline" size="icon" onClick={handleSearch}>
                        <Search className="h-4 w-4" />
                      </Button>
                      {searchQuery && (
                        <Button variant="ghost" size="sm" onClick={handleClearSearch}>
                          清除
                        </Button>
                      )}
                    </div>
                    <Select value={pageSize.toString()} onValueChange={handlePageSizeChange}>
                      <SelectTrigger className="w-full sm:w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="10">10条/页</SelectItem>
                        <SelectItem value="20">20条/页</SelectItem>
                        <SelectItem value="50">50条/页</SelectItem>
                        <SelectItem value="100">100条/页</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                {searchQuery && (
                  <div className="text-sm text-muted-foreground mt-2">
                    搜索关键词: <span className="font-medium">"{searchQuery}"</span>
                  </div>
                )}
              </CardHeader>
              <CardContent>
                {chatLogsLoading ? (
                  <div className="space-y-2">
                    {[...Array(5)].map((_, i) => (
                      <Skeleton key={i} className="h-20 w-full" />
                    ))}
                  </div>
                ) : chatLogs?.data && chatLogs.data.length > 0 ? (
                  <>
                    <div className="space-y-2">
                      {chatLogs.data.map((reply) => (
                        <div
                          key={reply.filename}
                          className="border rounded-lg p-3 hover:bg-accent/50 transition-colors cursor-pointer"
                          onClick={() => handleLogClick(reply.chat_id, reply.filename)}
                        >
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-sm font-medium text-muted-foreground">
                              {formatTimestamp(reply.timestamp)}
                            </span>
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                              {reply.success ? (
                                <Badge variant="default" className="text-xs bg-green-600">
                                  <CheckCircle className="h-3 w-3 mr-1" />
                                  成功
                                </Badge>
                              ) : (
                                <Badge variant="destructive" className="text-xs">
                                  <XCircle className="h-3 w-3 mr-1" />
                                  失败
                                </Badge>
                              )}
                              <Badge variant="outline" className="text-xs">
                                {reply.model}
                              </Badge>
                              <Badge variant="secondary" className="text-xs">
                                {reply.overall_ms.toFixed(0)}ms
                              </Badge>
                            </div>
                          </div>
                          <p className="text-sm line-clamp-2">{reply.output_preview || '无输出内容'}</p>
                        </div>
                      ))}
                    </div>

                    {/* 分页控件 */}
                    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 mt-4 pt-4 border-t">
                      <div className="text-sm text-muted-foreground">
                        共 {chatLogs.total} 条记录，第 {page} / {totalPages} 页
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage(1)}
                          disabled={page === 1}
                          className="hidden sm:flex"
                        >
                          <ChevronsLeft className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage(p => Math.max(1, p - 1))}
                          disabled={page === 1}
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <div className="hidden sm:flex items-center gap-2">
                          <Input
                            type="number"
                            min={1}
                            max={totalPages}
                            value={jumpToPage}
                            onChange={(e) => setJumpToPage(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                            placeholder="跳转"
                            className="w-20 h-8"
                          />
                          <Button size="sm" variant="outline" onClick={handleJumpToPage}>
                            跳转
                          </Button>
                        </div>
                        <span className="sm:hidden text-sm text-muted-foreground">
                          {page}/{totalPages}
                        </span>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                          disabled={page === totalPages}
                        >
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage(totalPages)}
                          disabled={page === totalPages}
                          className="hidden sm:flex"
                        >
                          <ChevronsRight className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">暂无回复记录</div>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* ========== 第三级：回复详情弹窗 ========== */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              回复生成详情
            </DialogTitle>
            <DialogDescription>
              查看麦麦的详细回复生成过程
            </DialogDescription>
          </DialogHeader>

          <ScrollArea className="h-full pr-4">
            <div className="space-y-6 pb-4">
              {detailLoading ? (
                <div className="space-y-4">
                  {[...Array(5)].map((_, i) => (
                    <Skeleton key={i} className="h-24 w-full" />
                  ))}
                </div>
              ) : selectedLog ? (
                <>
                  {/* 基本信息 */}
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                      <Clock className="h-4 w-4" />
                      基本信息
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-4 bg-muted/50 rounded-lg">
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">聊天</div>
                        <div className="text-sm" title={selectedLog.chat_id}>{getChatName(selectedLog.chat_id)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">时间戳</div>
                        <div className="text-sm">{formatTimestamp(selectedLog.timestamp)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">状态</div>
                        {selectedLog.success ? (
                          <Badge variant="default" className="bg-green-600">
                            <CheckCircle className="h-3 w-3 mr-1" />
                            成功
                          </Badge>
                        ) : (
                          <Badge variant="destructive">
                            <XCircle className="h-3 w-3 mr-1" />
                            失败
                          </Badge>
                        )}
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">思考深度</div>
                        <Badge variant="outline">Level {selectedLog.think_level}</Badge>
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* 模型信息 */}
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                      <Cpu className="h-4 w-4" />
                      模型信息
                    </h3>
                    <div className="p-4 bg-muted/50 rounded-lg">
                      <Badge variant="secondary" className="text-sm">{selectedLog.model}</Badge>
                    </div>
                  </div>

                  <Separator />

                  {/* 性能统计 */}
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                      <Zap className="h-4 w-4" />
                      性能统计
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <Card>
                        <CardHeader className="p-4 pb-2">
                          <CardTitle className="text-xs text-muted-foreground">提示词构建</CardTitle>
                        </CardHeader>
                        <CardContent className="p-4 pt-0">
                          <div className="text-xl font-bold">{selectedLog.timing.prompt_ms?.toFixed(2) || 0}ms</div>
                        </CardContent>
                      </Card>
                      <Card>
                        <CardHeader className="p-4 pb-2">
                          <CardTitle className="text-xs text-muted-foreground">LLM 推理</CardTitle>
                        </CardHeader>
                        <CardContent className="p-4 pt-0">
                          <div className="text-xl font-bold">{selectedLog.timing.llm_ms?.toFixed(2) || 0}ms</div>
                        </CardContent>
                      </Card>
                      <Card>
                        <CardHeader className="p-4 pb-2">
                          <CardTitle className="text-xs text-muted-foreground">总耗时</CardTitle>
                        </CardHeader>
                        <CardContent className="p-4 pt-0">
                          <div className="text-xl font-bold">{selectedLog.timing.overall_ms?.toFixed(2) || 0}ms</div>
                        </CardContent>
                      </Card>
                    </div>
                    
                    {/* 耗时日志 */}
                    {selectedLog.timing.timing_logs && selectedLog.timing.timing_logs.length > 0 && (
                      <div className="mt-3 p-3 bg-muted/30 rounded-lg">
                        <div className="text-xs font-medium text-muted-foreground mb-2">耗时详情</div>
                        <div className="space-y-1">
                          {selectedLog.timing.timing_logs.map((log, idx) => (
                            <div key={idx} className="text-xs text-muted-foreground">{log}</div>
                          ))}
                        </div>
                      </div>
                    )}
                    
                    {/* 几乎为零的耗时 */}
                    {selectedLog.timing.almost_zero && (
                      <div className="mt-2 text-xs text-muted-foreground">
                        <span className="font-medium">近乎零耗时: </span>
                        {selectedLog.timing.almost_zero}
                      </div>
                    )}
                  </div>

                  <Separator />

                  {/* 回复输出 */}
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                      <Brain className="h-4 w-4" />
                      回复输出
                    </h3>
                    <div className="p-4 bg-muted/50 rounded-lg">
                      <p className="text-sm whitespace-pre-wrap leading-relaxed">{selectedLog.output || '无输出内容'}</p>
                    </div>
                  </div>

                  {/* 处理后的输出 */}
                  {selectedLog.processed_output && selectedLog.processed_output.length > 0 && (
                    <>
                      <Separator />
                      <div className="space-y-2">
                        <h3 className="text-sm font-semibold">处理后的输出</h3>
                        <div className="space-y-2">
                          {selectedLog.processed_output.map((output, idx) => (
                            <div key={idx} className="p-3 bg-muted/30 rounded-lg">
                              <p className="text-sm whitespace-pre-wrap">{output}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  )}

                  {/* 推理内容 */}
                  {selectedLog.reasoning && (
                    <>
                      <Separator />
                      <div className="space-y-2">
                        <h3 className="text-sm font-semibold">推理过程</h3>
                        <div className="p-4 bg-muted/50 rounded-lg">
                          <p className="text-sm whitespace-pre-wrap leading-relaxed">{selectedLog.reasoning}</p>
                        </div>
                      </div>
                    </>
                  )}

                  {/* 错误信息 */}
                  {selectedLog.error && (
                    <>
                      <Separator />
                      <div className="space-y-2">
                        <h3 className="text-sm font-semibold text-destructive">错误信息</h3>
                        <div className="p-4 bg-destructive/10 rounded-lg border border-destructive/20">
                          <p className="text-sm text-destructive whitespace-pre-wrap">{selectedLog.error}</p>
                        </div>
                      </div>
                    </>
                  )}

                  {/* 提示词 */}
                  {selectedLog.prompt && (
                    <>
                      <Separator />
                      <div className="space-y-2">
                        <h3 className="text-sm font-semibold">完整提示词</h3>
                        <details className="group">
                          <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                            点击展开查看完整提示词
                          </summary>
                          <div className="mt-2 p-4 bg-muted/50 rounded-lg">
                            <pre className="text-xs whitespace-pre-wrap break-words">{selectedLog.prompt}</pre>
                          </div>
                        </details>
                      </div>
                    </>
                  )}
                </>
              ) : (
                <div className="flex items-center justify-center py-12">
                  <p className="text-muted-foreground">无数据</p>
                </div>
              )}
            </div>
          </ScrollArea>

          <DialogFooter className="flex-shrink-0">
            <Button onClick={() => setDialogOpen(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
