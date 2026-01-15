/**
 * WebUI 使用反馈问卷页面
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Loader2, AlertCircle, FileQuestion } from 'lucide-react'
import { SurveyRenderer } from '@/components/survey'
import { webuiFeedbackSurvey } from '@/config/surveys'
import { APP_VERSION } from '@/lib/version'
import type { SurveyConfig, QuestionAnswer } from '@/types/survey'

export function WebUIFeedbackSurveyPage() {
  const [isLoading, setIsLoading] = useState(true)

  // 使用 useMemo 派生配置而不是 useState + useEffect
  const surveyConfig = useMemo(() => {
    // 深拷贝配置以避免修改原始对象
    return JSON.parse(JSON.stringify(webuiFeedbackSurvey)) as SurveyConfig
  }, [])

  // 初始化完成后设置加载状态
  useEffect(() => {
    setIsLoading(false)
  }, [])

  // 预填充的答案（版本号自动填写）
  const initialAnswers: QuestionAnswer[] = useMemo(() => [
    {
      questionId: 'webui_version',
      value: `v${APP_VERSION}`,
    },
  ], [])

  // 提交成功回调
  const handleSubmitSuccess = useCallback((submissionId: string) => {
    console.log('WebUI Survey submitted:', submissionId)
  }, [])

  // 提交错误回调
  const handleSubmitError = useCallback((error: string) => {
    console.error('WebUI Survey submission error:', error)
  }, [])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!surveyConfig) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
        <Alert variant="destructive" className="max-w-md">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            无法加载问卷配置
          </AlertDescription>
        </Alert>
        <Button variant="outline" onClick={() => window.location.reload()}>
          重试
        </Button>
      </div>
    )
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6 shrink-0">
        <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
          <FileQuestion className="h-8 w-8" strokeWidth={2} />
          WebUI 使用反馈问卷
        </h1>
        <p className="text-muted-foreground mt-1 text-sm sm:text-base">
          感谢您的反馈，帮助我们持续改进产品体验
        </p>
      </div>

      {/* 问卷内容 */}
      <div className="flex-1 min-h-0">
        <SurveyRenderer
          config={surveyConfig}
          initialAnswers={initialAnswers}
          showProgress={true}
          paginateQuestions={false}
          onSubmitSuccess={handleSubmitSuccess}
          onSubmitError={handleSubmitError}
        />
      </div>
    </div>
  )
}

export default WebUIFeedbackSurveyPage
