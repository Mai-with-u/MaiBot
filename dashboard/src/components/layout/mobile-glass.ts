// FlClash (prism_ui) 风格液态玻璃材质分级；光学规则定义在 styles/mobile.css 中（仅 modern 主题启用完整光学反射层）。
// - MOBILE_GLASS_CLASS：悬浮控制层（底部标签栏、实时条、返回键、搜索键、页头按钮、悬浮栏）
// - MOBILE_GLASS_MENU_CLASS：悬浮菜单与弹层（下拉菜单、选择器、气泡面板，提供更稳妥的正文可读性）
// - MOBILE_ONLY_GLASS_CLASS：仅在手机模式（lg 以下）启用液态玻璃，桌面端保持原有外观
export const MOBILE_GLASS_CLASS = 'm-glass'

export const MOBILE_GLASS_MENU_CLASS = 'm-glass-menu'

/** 只在手机端（lg 以下）生效的玻璃，桌面端保持元素原有外观 */
export const MOBILE_ONLY_GLASS_CLASS = 'm-glass-mobile'
