import type { TFunction } from 'i18next'
import type { Step } from 'react-joyride'

import { buildSearchNavigationPath } from '@/lib/config-search-navigation'

export const BASIC_FEATURES_TOUR_ID = 'basic-features-tour'

/** 配置字段在动态表单中的锚点；两种属性都匹配，兼容不同的字段渲染方式。 */
function configFieldTarget(fieldPath: string): string {
  return `[data-config-field-path="${fieldPath}"], [data-dynamic-field="${fieldPath}"]`
}

interface BasicFeaturesStepDef {
  key: string
  /** 进入该步骤时需要所在的页面（含查询参数）。 */
  href: string
  target: string
  placement?: Step['placement']
}

const STEP_DEFS: BasicFeaturesStepDef[] = [
  {
    key: 'personality',
    href: buildSearchNavigationPath('/config/bot', 'personality.personality'),
    target: configFieldTarget('personality.personality'),
  },
  {
    key: 'replyStyle',
    href: buildSearchNavigationPath('/config/bot', 'personality.reply_style'),
    target: configFieldTarget('personality.reply_style'),
  },
  {
    key: 'talkValue',
    href: buildSearchNavigationPath('/config/bot', 'chat.reply_timing.talk_value'),
    target: configFieldTarget('chat.reply_timing.talk_value'),
  },
  {
    key: 'plugins',
    href: '/plugins',
    target: '[data-tour="plugin-market-search"]',
  },
  {
    key: 'knowledgeBase',
    href: '/resource/knowledge-base',
    target: '[data-tour="memory-tab-import"]',
  },
  {
    key: 'done',
    href: '/resource/knowledge-base',
    target: 'body',
    placement: 'center',
  },
]

/** 每一步对应的页面，驱动组件据此在切换步骤时跳转。 */
export const BASIC_FEATURES_STEP_HREFS = STEP_DEFS.map((step) => step.href)

export function buildBasicFeaturesTourSteps(t: TFunction): Step[] {
  return STEP_DEFS.map(({ key, target, placement }) => ({
    target,
    title: t(`tour.basicFeatures.${key}.title`),
    content: t(`tour.basicFeatures.${key}.content`),
    placement: placement ?? 'auto',
    disableBeacon: true,
    disableOverlayClose: true,
    // 允许用户在讲解时直接试着修改高亮的配置项。
    spotlightClicks: target !== 'body',
  }))
}
