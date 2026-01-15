import type { Step, Placement } from 'react-joyride'

export const MODEL_ASSIGNMENT_TOUR_ID = 'model-assignment-tour'

// Tour 步骤定义
export const modelAssignmentTourSteps: Step[] = [
  // Step 1: 全屏介绍
  {
    target: 'body',
    content: '本引导旨在帮助你配置模型提供商和对应的模型，并为麦麦的各个组件分配合适的模型。',
    placement: 'center' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 2: 侧边栏 - 模型提供商按钮（点击下一步会自动导航）
  {
    target: '[data-tour="sidebar-model-provider"]',
    content: '第一步，你需要配置模型提供商。模型提供商决定了你要使用谁家的模型，无论是单一厂商（如 DeepSeek），还是模型平台（如 Siliconflow），都可以在这里进行配置。点击"下一步"进入配置页面。',
    placement: 'right' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 3: 添加提供商按钮
  {
    target: '[data-tour="add-provider-button"]',
    content: '点击"添加提供商"按钮，开始配置你的模型提供商。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: true,
    hideFooter: true,
  },
  // Step 4: 添加提供商弹窗
  {
    target: '[data-tour="provider-dialog"]',
    content: '在这里，你可以选择你想要配置的模型提供商，填写相关信息后保存即可。',
    placement: 'left' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 5: 名称输入框
  {
    target: '[data-tour="provider-name-input"]',
    content: '这里的名称是你为这个模型提供商起的一个名字，方便你在后续使用时识别它。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 6: API 密钥输入框
  {
    target: '[data-tour="provider-apikey-input"]',
    content: '这里需要填写你从模型提供商那里获取的 API 密钥，用于验证和调用模型服务。对于不同的提供商，获取 API 密钥的方式可能有所不同，请参考对应提供商的文档。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 7: URL 输入框
  {
    target: '[data-tour="provider-url-input"]',
    content: '这里需要填写模型提供商的 API 访问地址，确保填写正确以便系统能够连接到模型服务。对于不同的提供商，API 地址可能有所不同，请参考对应提供商的文档。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 8: 模板选择下拉框
  {
    target: '[data-tour="provider-template-select"]',
    content: '当然，如果你不知道如何填写这些信息，很多模型提供商在这里都提供了预设的模板供你选择，选择对应的模板后，相关信息会自动填充。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 9: 保存按钮
  {
    target: '[data-tour="provider-save-button"]',
    content: '填写完所有信息后，点击保存按钮，模型提供商就配置完成了。',
    placement: 'top' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 10: 取消按钮
  {
    target: '[data-tour="provider-cancel-button"]',
    content: '因为这次咱们什么都没有填写，所以点击取消按钮退出吧。',
    placement: 'top' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: true,
    hideFooter: true,
  },
  // Step 11: 侧边栏 - 模型管理与分配按钮（点击下一步会自动导航）
  {
    target: '[data-tour="sidebar-model-management"]',
    content: '配置好模型提供商后，接下来我们需要为麦麦添加模型并分配功能。点击"下一步"进入模型管理页面。',
    placement: 'right' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 12: 添加模型按钮
  {
    target: '[data-tour="add-model-button"]',
    content: '在为麦麦的组件分配模型之前，首先需要添加你想要分配的模型，点击"添加模型"按钮开始添加。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: true,
    hideFooter: true,
  },
  // Step 13: 添加模型弹窗
  {
    target: '[data-tour="model-dialog"]',
    content: '在这里，你可以选择你之前配置好的模型提供商，然后选择对应的模型来添加。',
    placement: 'left' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 14: 模型名称输入框
  {
    target: '[data-tour="model-name-input"]',
    content: '这里的模型名称是你为这个模型起的一个名字，方便你在后续使用时识别它。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 15: API 提供商下拉框
  {
    target: '[data-tour="model-provider-select"]',
    content: '在这里选择你之前配置好的模型提供商，这样系统才能知道你要添加哪个提供商的模型。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 16: 模型标识符输入框
  {
    target: '[data-tour="model-identifier-input"]',
    content: '这里需要填写你想要添加的模型的标识符，不同的模型提供商可能有不同的标识符格式，请参考对应提供商的文档。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 17: 保存按钮
  {
    target: '[data-tour="model-save-button"]',
    content: '填写完所有信息后，点击保存按钮，模型就添加完成了。',
    placement: 'top' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
  // Step 18: 取消按钮
  {
    target: '[data-tour="model-cancel-button"]',
    content: '当然，因为这次咱们什么都没有填写，所以直接点击取消按钮退出吧，等你准备好了再来添加模型。',
    placement: 'top' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: true,
    hideFooter: true,
  },
  // Step 19: 为模型分配功能标签页
  {
    target: '[data-tour="tasks-tab-trigger"]',
    content: '最后一步，添加好模型后，切换到"为模型分配功能"标签页，为麦麦的各个组件分配合适的模型。',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: true,
    hideFooter: true,
  },
  // Step 20: 组件模型卡片的模型选择
  {
    target: '[data-tour="task-model-select"]',
    content: '在这里，你可以为每个组件选择一个或多个合适的模型，选择完成后配置会自动保存。恭喜你完成了模型配置的学习！',
    placement: 'bottom' as Placement,
    disableBeacon: true,
    disableOverlayClose: true,
    hideCloseButton: false,
    spotlightClicks: false,
  },
]

// 需要用户点击才能继续的步骤索引（0-based）
// Step 2 (index 2): 点击添加提供商按钮
// Step 9 (index 9): 点击取消按钮关闭提供商弹窗  
// Step 11 (index 11): 点击添加模型按钮
// Step 17 (index 17): 点击取消按钮关闭模型弹窗
// Step 18 (index 18): 点击标签页切换
export const CLICK_TO_CONTINUE_STEPS = new Set([2, 9, 11, 17, 18])

// 步骤与路由的映射
export const STEP_ROUTE_MAP: Record<number, string> = {
  0: '/config/model', // 起始页面
  1: '/config/model', // 侧边栏可见
  2: '/config/modelProvider', // 需要在模型提供商页面
  3: '/config/modelProvider',
  4: '/config/modelProvider',
  5: '/config/modelProvider',
  6: '/config/modelProvider',
  7: '/config/modelProvider',
  8: '/config/modelProvider',
  9: '/config/modelProvider',
  10: '/config/modelProvider',
  11: '/config/model', // 需要在模型管理页面
  12: '/config/model',
  13: '/config/model',
  14: '/config/model',
  15: '/config/model',
  16: '/config/model',
  17: '/config/model',
  18: '/config/model',
  19: '/config/model',
}
