/** 按钮、输入框和下拉框共用尺寸档位；主题只定义各档的实际高度。 */
export type ControlSize = 'default' | 'xs' | 'sm' | 'lg'

export const controlSizeClassNames: Record<ControlSize, string> = {
  xs: 'h-[var(--control-height-xs)]',
  default: 'h-[var(--control-height)]',
  sm: 'h-[var(--control-height-sm)]',
  lg: 'h-[var(--control-height-lg)]',
}
