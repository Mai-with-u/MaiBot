// 液态玻璃材质：只用在悬浮层上。样式定义在 styles/mobile.css 的 `.m-glass`：
// 高透明度 + 模糊提饱和，边缘有一圈亮边（上沿最亮）与内发光表现玻璃厚度，投影很轻。
export const MOBILE_GLASS_CLASS = 'm-glass'

/** 只在手机端（lg 以下）生效的玻璃，桌面端保持元素原有外观 */
export const MOBILE_ONLY_GLASS_CLASS = 'm-glass-mobile'
