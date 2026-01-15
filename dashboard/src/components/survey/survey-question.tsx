/**
 * 单个问题渲染组件
 */

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Checkbox } from '@/components/ui/checkbox'
import { Slider } from '@/components/ui/slider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Star } from 'lucide-react'
import type { SurveyQuestion as SurveyQuestionType } from '@/types/survey'

interface SurveyQuestionProps {
  question: SurveyQuestionType
  value: string | string[] | number | undefined
  onChange: (value: string | string[] | number) => void
  error?: string
  disabled?: boolean
}

export function SurveyQuestion({
  question,
  value,
  onChange,
  error,
  disabled = false
}: SurveyQuestionProps) {
  const [hoverRating, setHoverRating] = useState<number | null>(null)
  
  // 如果问题设置了只读，则禁用输入
  const isDisabled = disabled || question.readOnly

  const renderQuestion = () => {
    switch (question.type) {
      case 'single':
        return (
          <RadioGroup
            value={value as string || ''}
            onValueChange={onChange}
            disabled={isDisabled}
            className="space-y-2"
          >
            {question.options?.map((option) => (
              <div key={option.id} className="flex items-center space-x-2">
                <RadioGroupItem value={option.value} id={`${question.id}-${option.id}`} />
                <Label 
                  htmlFor={`${question.id}-${option.id}`}
                  className="cursor-pointer font-normal"
                >
                  {option.label}
                </Label>
              </div>
            ))}
          </RadioGroup>
        )

      case 'multiple': {
        const selectedValues = (value as string[]) || []
        return (
          <div className="space-y-2">
            {question.options?.map((option) => (
              <div key={option.id} className="flex items-center space-x-2">
                <Checkbox
                  id={`${question.id}-${option.id}`}
                  checked={selectedValues.includes(option.value)}
                  disabled={isDisabled || (
                    question.maxSelections !== undefined &&
                    selectedValues.length >= question.maxSelections &&
                    !selectedValues.includes(option.value)
                  )}
                  onCheckedChange={(checked) => {
                    if (checked) {
                      onChange([...selectedValues, option.value])
                    } else {
                      onChange(selectedValues.filter(v => v !== option.value))
                    }
                  }}
                />
                <Label 
                  htmlFor={`${question.id}-${option.id}`}
                  className="cursor-pointer font-normal"
                >
                  {option.label}
                </Label>
              </div>
            ))}
            {question.maxSelections && (
              <p className="text-xs text-muted-foreground">
                最多选择 {question.maxSelections} 项
              </p>
            )}
          </div>
        )
      }

      case 'text':
        return (
          <Input
            value={value as string || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={question.placeholder || '请输入...'}
            disabled={isDisabled}
            readOnly={question.readOnly}
            maxLength={question.maxLength}
            className={cn(question.readOnly && "bg-muted cursor-not-allowed")}
          />
        )

      case 'textarea':
        return (
          <div className="space-y-1">
            <Textarea
              value={value as string || ''}
              onChange={(e) => onChange(e.target.value)}
              placeholder={question.placeholder || '请输入...'}
              disabled={isDisabled}
              readOnly={question.readOnly}
              maxLength={question.maxLength}
              rows={4}
              className={cn(question.readOnly && "bg-muted cursor-not-allowed")}
            />
            {question.maxLength && (
              <p className="text-xs text-muted-foreground text-right">
                {(value as string || '').length} / {question.maxLength}
              </p>
            )}
          </div>
        )

      case 'rating': {
        const ratingValue = (value as number) || 0
        const displayRating = hoverRating !== null ? hoverRating : ratingValue
        return (
          <div className="flex items-center gap-1">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                disabled={isDisabled}
                className={cn(
                  "p-1 transition-colors focus:outline-none focus:ring-2 focus:ring-ring rounded",
                  isDisabled && "cursor-not-allowed opacity-50"
                )}
                onMouseEnter={() => !isDisabled && setHoverRating(star)}
                onMouseLeave={() => setHoverRating(null)}
                onClick={() => !isDisabled && onChange(star)}
              >
                <Star
                  className={cn(
                    "h-6 w-6 transition-colors",
                    star <= displayRating
                      ? "fill-yellow-400 text-yellow-400"
                      : "text-muted-foreground"
                  )}
                />
              </button>
            ))}
            {ratingValue > 0 && (
              <span className="ml-2 text-sm text-muted-foreground">
                {ratingValue} / 5
              </span>
            )}
          </div>
        )
      }

      case 'scale': {
        const min = question.min ?? 1
        const max = question.max ?? 10
        const step = question.step ?? 1
        const scaleValue = (value as number) ?? min
        return (
          <div className="space-y-4">
            <Slider
              value={[scaleValue]}
              onValueChange={([val]) => onChange(val)}
              min={min}
              max={max}
              step={step}
              disabled={isDisabled}
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{question.minLabel || min}</span>
              <span className="font-medium text-foreground">{scaleValue}</span>
              <span>{question.maxLabel || max}</span>
            </div>
          </div>
        )
      }

      case 'dropdown':
        return (
          <Select
            value={value as string || ''}
            onValueChange={onChange}
            disabled={isDisabled}
          >
            <SelectTrigger>
              <SelectValue placeholder={question.placeholder || '请选择...'} />
            </SelectTrigger>
            <SelectContent>
              {question.options?.map((option) => (
                <SelectItem key={option.id} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )

      default:
        return <div className="text-muted-foreground">不支持的问题类型</div>
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="text-base font-medium">
          {question.title}
          {question.required && (
            <span className="text-destructive ml-1">*</span>
          )}
        </Label>
        {question.description && (
          <p className="text-sm text-muted-foreground">{question.description}</p>
        )}
      </div>
      
      {renderQuestion()}
      
      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}
    </div>
  )
}
