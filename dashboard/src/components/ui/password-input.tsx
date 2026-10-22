import { forwardRef, useState } from 'react'
import type { ComponentPropsWithoutRef } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

type PasswordInputProps = Omit<ComponentPropsWithoutRef<typeof Input>, 'type'> & {
  showLabel: string
  hideLabel: string
}

/** 密码显示按钮与输入框共用布局，保留 Input 的尺寸、主题和原生表单属性。 */
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ showLabel, hideLabel, className, disabled, ...props }, ref) => {
    const [visible, setVisible] = useState(false)
    return (
      <div className="relative">
        <Input
          {...props}
          ref={ref}
          disabled={disabled}
          type={visible ? 'text' : 'password'}
          className={cn('pr-24', className)}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          aria-controls={props.id}
          aria-pressed={visible}
          className="absolute top-1/2 right-2 -translate-y-1/2"
          onClick={() => setVisible((value) => !value)}
        >
          {visible ? hideLabel : showLabel}
        </Button>
      </div>
    )
  }
)
PasswordInput.displayName = 'PasswordInput'
