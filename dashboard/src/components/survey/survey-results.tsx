/**
 * 问卷结果查看组件
 * 展示问卷统计数据和用户提交记录
 */

import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Loader2, Users, FileText, Clock, Star, BarChart3 } from 'lucide-react'
import { getSurveyStats, getUserSubmissions } from '@/lib/survey-api'
import type { SurveyConfig, SurveyStats, StoredSubmission } from '@/types/survey'

interface SurveyResultsProps {
  /** 问卷配置 */
  config: SurveyConfig
  /** 是否显示用户提交记录 */
  showUserSubmissions?: boolean
  /** 自定义类名 */
  className?: string
}

export function SurveyResults({
  config,
  showUserSubmissions = true,
  className
}: SurveyResultsProps) {
  const [stats, setStats] = useState<SurveyStats | null>(null)
  const [userSubmissions, setUserSubmissions] = useState<StoredSubmission[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true)
      setError(null)

      try {
        // 获取统计数据
        const statsResult = await getSurveyStats(config.id)
        if (statsResult.success && statsResult.stats) {
          setStats(statsResult.stats)
        }

        // 获取用户提交记录
        if (showUserSubmissions) {
          const submissionsResult = await getUserSubmissions(config.id)
          if (submissionsResult.success && submissionsResult.submissions) {
            setUserSubmissions(submissionsResult.submissions)
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载数据失败')
      } finally {
        setIsLoading(false)
      }
    }

    fetchData()
  }, [config.id, showUserSubmissions])

  if (isLoading) {
    return (
      <Card className={cn("w-full max-w-3xl mx-auto", className)}>
        <CardContent className="py-12 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className={cn("w-full max-w-3xl mx-auto", className)}>
        <CardContent className="py-12 text-center text-muted-foreground">
          {error}
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={cn("w-full max-w-3xl mx-auto", className)}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="h-5 w-5" />
          {config.title} - 统计结果
        </CardTitle>
        {config.description && (
          <CardDescription>{config.description}</CardDescription>
        )}
      </CardHeader>

      <CardContent>
        {/* 概览统计 */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="p-4 rounded-lg bg-muted/50 text-center">
            <div className="flex items-center justify-center gap-2 text-muted-foreground mb-1">
              <FileText className="h-4 w-4" />
              <span className="text-sm">总提交数</span>
            </div>
            <div className="text-2xl font-bold">
              {stats?.totalSubmissions || 0}
            </div>
          </div>
          
          <div className="p-4 rounded-lg bg-muted/50 text-center">
            <div className="flex items-center justify-center gap-2 text-muted-foreground mb-1">
              <Users className="h-4 w-4" />
              <span className="text-sm">独立用户</span>
            </div>
            <div className="text-2xl font-bold">
              {stats?.uniqueUsers || 0}
            </div>
          </div>
          
          <div className="p-4 rounded-lg bg-muted/50 text-center">
            <div className="flex items-center justify-center gap-2 text-muted-foreground mb-1">
              <Clock className="h-4 w-4" />
              <span className="text-sm">最后提交</span>
            </div>
            <div className="text-sm font-medium">
              {stats?.lastSubmissionAt
                ? new Date(stats.lastSubmissionAt).toLocaleDateString()
                : '-'
              }
            </div>
          </div>
        </div>

        <Tabs defaultValue="stats" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="stats">问题统计</TabsTrigger>
            {showUserSubmissions && (
              <TabsTrigger value="submissions">我的提交</TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="stats" className="mt-4">
            <ScrollArea className="max-h-[60vh]">
              <div className="space-y-6 pr-4">
                {config.questions.map((question, index) => {
                  const qStats = stats?.questionStats[question.id]
                  
                  return (
                    <div key={question.id} className="p-4 rounded-lg border">
                      <div className="text-xs text-muted-foreground mb-1">
                        问题 {index + 1}
                      </div>
                      <div className="font-medium mb-3">{question.title}</div>
                      
                      {qStats ? (
                        <div className="space-y-2">
                          <div className="text-sm text-muted-foreground">
                            回答人数：{qStats.answered}
                          </div>
                          
                          {/* 选择题统计 */}
                          {qStats.optionCounts && question.options && (
                            <div className="space-y-2">
                              {question.options.map(option => {
                                const count = qStats.optionCounts?.[option.value] || 0
                                const percentage = qStats.answered > 0
                                  ? (count / qStats.answered) * 100
                                  : 0
                                
                                return (
                                  <div key={option.id} className="space-y-1">
                                    <div className="flex justify-between text-sm">
                                      <span>{option.label}</span>
                                      <span className="text-muted-foreground">
                                        {count} ({percentage.toFixed(1)}%)
                                      </span>
                                    </div>
                                    <Progress value={percentage} className="h-2" />
                                  </div>
                                )
                              })}
                            </div>
                          )}
                          
                          {/* 评分/量表统计 */}
                          {qStats.average !== undefined && (
                            <div className="flex items-center gap-2">
                              <Star className="h-4 w-4 text-yellow-400" />
                              <span className="text-sm">
                                平均分：{qStats.average.toFixed(2)}
                              </span>
                            </div>
                          )}
                          
                          {/* 文本答案样本 */}
                          {qStats.sampleAnswers && qStats.sampleAnswers.length > 0 && (
                            <div className="space-y-2">
                              <div className="text-sm text-muted-foreground">
                                部分回答：
                              </div>
                              <div className="space-y-1">
                                {qStats.sampleAnswers.map((answer, i) => (
                                  <div
                                    key={i}
                                    className="text-sm p-2 bg-muted/50 rounded text-muted-foreground"
                                  >
                                    "{answer}"
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="text-sm text-muted-foreground">
                          暂无数据
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </ScrollArea>
          </TabsContent>

          {showUserSubmissions && (
            <TabsContent value="submissions" className="mt-4">
              <ScrollArea className="max-h-[60vh]">
                {userSubmissions.length === 0 ? (
                  <div className="py-8 text-center text-muted-foreground">
                    你还没有提交过这份问卷
                  </div>
                ) : (
                  <div className="space-y-4 pr-4">
                    {userSubmissions.map((submission) => (
                      <div key={submission.id} className="p-4 rounded-lg border">
                        <div className="flex items-center justify-between mb-3">
                          <Badge variant="outline">
                            {new Date(submission.submittedAt).toLocaleString()}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            ID: {submission.id}
                          </span>
                        </div>
                        
                        <div className="space-y-2">
                          {submission.answers.map((answer) => {
                            const question = config.questions.find(
                              q => q.id === answer.questionId
                            )
                            
                            if (!question) return null
                            
                            // 格式化答案显示
                            let displayValue: string
                            if (Array.isArray(answer.value)) {
                              const labels = answer.value.map(v => {
                                const opt = question.options?.find(o => o.value === v)
                                return opt?.label || v
                              })
                              displayValue = labels.join('、')
                            } else if (typeof answer.value === 'number') {
                              displayValue = answer.value.toString()
                            } else {
                              const opt = question.options?.find(
                                o => o.value === answer.value
                              )
                              displayValue = opt?.label || answer.value
                            }
                            
                            return (
                              <div key={answer.questionId} className="text-sm">
                                <span className="text-muted-foreground">
                                  {question.title}：
                                </span>
                                <span>{displayValue}</span>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </TabsContent>
          )}
        </Tabs>
      </CardContent>
    </Card>
  )
}
