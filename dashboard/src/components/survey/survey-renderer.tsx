/**
 * 问卷渲染器组件
 * 读取 JSON 配置并展示问卷界面
 */

import { useState, useCallback, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2, CheckCircle2, AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react'
import { SurveyQuestion } from './survey-question'
import { submitSurvey, checkUserSubmission } from '@/lib/survey-api'
import type { SurveyConfig, QuestionAnswer } from '@/types/survey'

export interface SurveyRendererProps {
  /** 问卷配置 */
  config: SurveyConfig
  /** 初始答案（用于预填充，如自动填写版本号） */
  initialAnswers?: QuestionAnswer[]
  /** 提交成功回调 */
  onSubmitSuccess?: (submissionId: string) => void
  /** 提交失败回调 */
  onSubmitError?: (error: string) => void
  /** 是否显示进度条 */
  showProgress?: boolean
  /** 是否分页显示（每页一题） */
  paginateQuestions?: boolean
  /** 自定义类名 */
  className?: string
}

type AnswerMap = Record<string, string | string[] | number | undefined>

export function SurveyRenderer({
  config,
  initialAnswers,
  onSubmitSuccess,
  onSubmitError,
  showProgress = true,
  paginateQuestions = false,
  className
}: SurveyRendererProps) {
  // 将 initialAnswers 转换为 AnswerMap
  const getInitialAnswerMap = useCallback((): AnswerMap => {
    if (!initialAnswers || initialAnswers.length === 0) return {}
    return initialAnswers.reduce((acc, answer) => {
      acc[answer.questionId] = answer.value
      return acc
    }, {} as AnswerMap)
  }, [initialAnswers])

  const [answers, setAnswers] = useState<AnswerMap>(() => getInitialAnswerMap())
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [currentPage, setCurrentPage] = useState(0)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isSubmitted, setIsSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submissionId, setSubmissionId] = useState<string | null>(null)
  const [hasAlreadySubmitted, setHasAlreadySubmitted] = useState(false)
  const [isCheckingSubmission, setIsCheckingSubmission] = useState(true)

  // 当 initialAnswers 变化时更新答案（合并而非替换）
  useEffect(() => {
    if (initialAnswers && initialAnswers.length > 0) {
      setAnswers(prev => ({
        ...prev,
        ...getInitialAnswerMap()
      }))
    }
  }, [initialAnswers, getInitialAnswerMap])

  // 检查是否已提交过
  useEffect(() => {
    const checkSubmission = async () => {
      if (!config.settings?.allowMultiple) {
        const result = await checkUserSubmission(config.id)
        if (result.success && result.hasSubmitted) {
          setHasAlreadySubmitted(true)
        }
      }
      setIsCheckingSubmission(false)
    }
    checkSubmission()
  }, [config.id, config.settings?.allowMultiple])

  // 检查问卷是否在有效期内
  const isWithinTimeRange = useCallback(() => {
    const now = new Date()
    if (config.settings?.startTime && new Date(config.settings.startTime) > now) {
      return false
    }
    if (config.settings?.endTime && new Date(config.settings.endTime) < now) {
      return false
    }
    return true
  }, [config.settings?.startTime, config.settings?.endTime])

  // 计算进度
  const answeredCount = config.questions.filter(q => {
    const answer = answers[q.id]
    if (answer === undefined || answer === null) return false
    if (Array.isArray(answer)) return answer.length > 0
    if (typeof answer === 'string') return answer.trim() !== ''
    return true
  }).length

  const progress = (answeredCount / config.questions.length) * 100

  // 更新答案
  const handleAnswerChange = useCallback((questionId: string, value: string | string[] | number) => {
    setAnswers(prev => ({ ...prev, [questionId]: value }))
    // 清除该问题的错误
    setErrors(prev => {
      const newErrors = { ...prev }
      delete newErrors[questionId]
      return newErrors
    })
  }, [])

  // 验证答案
  const validateAnswers = useCallback(() => {
    const newErrors: Record<string, string> = {}
    
    for (const question of config.questions) {
      if (question.required) {
        const answer = answers[question.id]
        
        if (answer === undefined || answer === null) {
          newErrors[question.id] = '此题为必填项'
          continue
        }
        
        if (Array.isArray(answer) && answer.length === 0) {
          newErrors[question.id] = '请至少选择一项'
          continue
        }
        
        if (typeof answer === 'string' && answer.trim() === '') {
          newErrors[question.id] = '此题为必填项'
          continue
        }
      }
      
      // 文本长度验证
      if (question.minLength && typeof answers[question.id] === 'string') {
        const text = answers[question.id] as string
        if (text.length < question.minLength) {
          newErrors[question.id] = `至少需要 ${question.minLength} 个字符`
        }
      }
    }
    
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }, [config.questions, answers])

  // 提交问卷
  const handleSubmit = useCallback(async () => {
    if (!validateAnswers()) {
      // 如果是分页模式，跳转到第一个有错误的问题
      if (paginateQuestions) {
        const firstErrorIndex = config.questions.findIndex(q => errors[q.id])
        if (firstErrorIndex >= 0) {
          setCurrentPage(firstErrorIndex)
        }
      }
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      // 构建答案列表
      const answerList: QuestionAnswer[] = config.questions
        .filter(q => answers[q.id] !== undefined)
        .map(q => ({
          questionId: q.id,
          value: answers[q.id]!
        }))

      const result = await submitSurvey(
        config.id,
        config.version,
        answerList,
        { allowMultiple: config.settings?.allowMultiple }
      )

      if (result.success && result.submissionId) {
        setIsSubmitted(true)
        setSubmissionId(result.submissionId)
        onSubmitSuccess?.(result.submissionId)
      } else {
        const error = result.error || '提交失败'
        setSubmitError(error)
        onSubmitError?.(error)
      }
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : '提交失败'
      setSubmitError(errorMsg)
      onSubmitError?.(errorMsg)
    } finally {
      setIsSubmitting(false)
    }
  }, [validateAnswers, paginateQuestions, config, answers, errors, onSubmitSuccess, onSubmitError])

  // 分页导航
  const goToPage = useCallback((page: number) => {
    if (page >= 0 && page < config.questions.length) {
      setCurrentPage(page)
    }
  }, [config.questions.length])

  // 检查中
  if (isCheckingSubmission) {
    return (
      <Card className={cn("w-full max-w-2xl mx-auto", className)}>
        <CardContent className="py-12 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  // 已提交过
  if (hasAlreadySubmitted && !config.settings?.allowMultiple) {
    return (
      <Card className={cn("w-full max-w-2xl mx-auto", className)}>
        <CardHeader>
          <CardTitle>{config.title}</CardTitle>
        </CardHeader>
        <CardContent className="py-8">
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              你已经提交过这份问卷了，感谢参与！
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    )
  }

  // 不在有效期内
  if (!isWithinTimeRange()) {
    return (
      <Card className={cn("w-full max-w-2xl mx-auto", className)}>
        <CardHeader>
          <CardTitle>{config.title}</CardTitle>
        </CardHeader>
        <CardContent className="py-8">
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              问卷不在有效期内
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    )
  }

  // 提交成功
  if (isSubmitted) {
    return (
      <Card className={cn("w-full max-w-2xl mx-auto", className)}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-600">
            <CheckCircle2 className="h-6 w-6" />
            提交成功
          </CardTitle>
        </CardHeader>
        <CardContent className="py-8">
          <p className="text-center text-muted-foreground">
            {config.settings?.thankYouMessage || '感谢你的参与！'}
          </p>
          {submissionId && (
            <p className="text-center text-xs text-muted-foreground mt-4">
              提交编号：{submissionId}
            </p>
          )}
        </CardContent>
      </Card>
    )
  }

  // 问卷展示
  const questionsToShow = paginateQuestions
    ? [config.questions[currentPage]]
    : config.questions

  return (
    <div className={cn("h-full flex flex-col", className)}>
      {/* 问卷头部 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6 mb-4 shrink-0">
        <h2 className="text-xl font-semibold">{config.title}</h2>
        {config.description && (
          <p className="text-muted-foreground mt-1 text-sm">{config.description}</p>
        )}
        {showProgress && (
          <div className="space-y-1 pt-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>进度</span>
              <span>{answeredCount} / {config.questions.length}</span>
            </div>
            <Progress value={progress} className="h-2" />
          </div>
        )}
      </div>

      {/* 问卷内容 - 可滚动区域 */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-4 pr-4">
          {questionsToShow.map((question, index) => (
            <div 
              key={question.id}
              className={cn(
                "p-4 rounded-lg border bg-card",
                errors[question.id] ? "border-destructive bg-destructive/5" : "border-border"
              )}
            >
              {paginateQuestions && (
                <div className="text-xs text-muted-foreground mb-2">
                  问题 {currentPage + 1} / {config.questions.length}
                </div>
              )}
              {!paginateQuestions && (
                <div className="text-xs text-muted-foreground mb-2">
                  {index + 1}.
                </div>
              )}
              <SurveyQuestion
                question={question}
                value={answers[question.id]}
                onChange={(value) => handleAnswerChange(question.id, value)}
                error={errors[question.id]}
                disabled={isSubmitting}
              />
            </div>
          ))}

          {submitError && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{submitError}</AlertDescription>
            </Alert>
          )}

          {/* 提交按钮区域 */}
          <div className="flex justify-between items-center py-4">
            {paginateQuestions ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => goToPage(currentPage - 1)}
                  disabled={currentPage === 0 || isSubmitting}
                >
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  上一题
                </Button>
                
                {currentPage === config.questions.length - 1 ? (
                  <Button
                    onClick={handleSubmit}
                    disabled={isSubmitting}
                  >
                    {isSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    提交问卷
                  </Button>
                ) : (
                  <Button
                    onClick={() => goToPage(currentPage + 1)}
                    disabled={isSubmitting}
                  >
                    下一题
                    <ChevronRight className="h-4 w-4 ml-1" />
                  </Button>
                )}
              </>
            ) : (
              <>
                <div className="text-sm text-muted-foreground">
                  {Object.keys(errors).length > 0 && (
                    <span className="text-destructive">
                      还有 {Object.keys(errors).length} 个必填项未完成
                    </span>
                  )}
                </div>
                <Button
                  onClick={handleSubmit}
                  disabled={isSubmitting}
                  size="lg"
                >
                  {isSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  提交问卷
                </Button>
              </>
            )}
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
