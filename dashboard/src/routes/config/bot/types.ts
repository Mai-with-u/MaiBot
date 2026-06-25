/**
 * Bot 配置页面相关类型定义
 */

export interface BotConfig {
  platform: string
  qq_account: string | number
  nickname: string
  platforms: string[]
  alias_names: string[]
}

export interface PersonalityConfig {
  personality: string
  reply_style: string
  interest: string
  plan_style: string
  visual_style: string
  states: string[]
  state_probability: number
}

export interface ChatConfig {
  talk_value: number
  mentioned_bot_reply: boolean
  inevitable_at_reply: boolean
  enable_direct_followup_reply: boolean
  max_context_size: number
  enable_context_optimization: boolean
  planner_smooth: number
  think_mode: 'classic' | 'deep' | 'dynamic'
  plan_reply_log_max_per_chat: number
  llm_quote: boolean
  enable_talk_value_rules: boolean
  talk_value_rules: Array<{
    target: string
    time: string
    value: number
  }>
}

export interface ExperimentalConfig {
  enable_behavior_learning: boolean
  behavior_learning_list: LearningItem[]
  behavior_groups: ChatStreamGroup[]
  enable_replyer_format_output: boolean
  focus_mode: boolean
  focus_cool_time: number
}

export interface TargetItem {
  platform: string
  item_id: string
  rule_type?: 'group' | 'private'
  type?: 'group' | 'private'
}

export interface LearningItem {
  platform: string
  item_id: string
  type?: 'group' | 'private'
  use?: boolean
  learn?: boolean
  rule_type?: 'group' | 'private'
  use_expression?: boolean
  enable_learning?: boolean
  enable_jargon_learning?: boolean
}

export interface ChatStreamGroup {
  targets?: TargetItem[]
  expression_groups?: TargetItem[]
  jargon_groups?: TargetItem[]
  behavior_groups?: TargetItem[]
}

export type ExpressionGroup = ChatStreamGroup

export interface ExpressionConfig {
  learning_list: LearningItem[]
  expression_groups: ChatStreamGroup[]
  expression_manual_reflect: boolean
  manual_reflect_operator_id: TargetItem | null
  allow_reflect: TargetItem[]
  expression_checked_only: boolean
  expression_self_reflect: boolean
  max_expression_learner: number
  all_global_jargon?: boolean
  enable_jargon_explanation?: boolean
  jargon_mode?: string
}

export interface JargonConfig {
  learning_list: LearningItem[]
  jargon_groups: ChatStreamGroup[]
}

export interface EmojiConfig {
  emoji_chance: number
  max_reg_num: number
  do_replace: boolean
  check_interval: number
  steal_emoji: boolean
  content_filtration: boolean
}

// MoodConfig 已在后端移除

export interface VoiceConfig {
  enable_asr: boolean
}

export interface MessageReceiveConfig {
  ban_words: string[]
  ban_msgs_regex: string[]
}

export interface DreamConfig {
  interval_minutes: number
  max_iterations: number
  first_delay_seconds: number
  dream_send: string
  dream_time_ranges: string[]
  dream_visible: boolean
}

export interface KeywordRule {
  keywords?: string[]
  regex?: string[]
  reaction: string
}

export interface KeywordReactionConfig {
  keyword_rules: KeywordRule[]
  regex_rules: KeywordRule[]
}

export interface ResponsePostProcessConfig {
  enable_response_post_process: boolean
}

export interface ChineseTypoConfig {
  enable: boolean
  error_rate: number
  min_freq: number
  tone_error_rate: number
  word_replace_rate: number
}

export interface ResponseSplitterConfig {
  enable: boolean
  max_length: number
  max_sentence_num: number
  enable_kaomoji_protection: boolean
  enable_overflow_return_all: boolean
}

export interface LogConfig {
  date_style: string
  log_level_style: string
  color_text: string
  log_level: string
  console_log_level: string
  file_log_level: string
  suppress_libraries: string[]
  library_log_levels: Record<string, string>
}

export interface DebugConfig {
  show_prompt: boolean
  show_replyer_prompt: boolean
  show_replyer_reasoning: boolean
  show_jargon_prompt: boolean
  show_memory_prompt: boolean
  show_planner_prompt: boolean
  keep_prompt_preview_json_base64: boolean
}

export interface MaimMessageConfig {
  auth_token: string[]
  enable_api_server: boolean
  api_server_host: string
  api_server_port: number
  api_server_use_wss: boolean
  api_server_cert_file: string
  api_server_key_file: string
  api_server_allowed_api_keys: string[]
}

export interface TelemetryConfig {
  enable: boolean
}

/**
 * WebUI 配置
 * 注意: host 和 port 配置已移至环境变量 WEBUI_HOST 和 WEBUI_PORT
 */
export interface WebUIConfig {
  enabled: boolean
  mode: string
  webui_style: number
  anti_crawler_mode: string
  allowed_ips: string
  trusted_proxies: string
  trust_xff: boolean
  secure_cookie: boolean
  enable_paragraph_content: boolean
}

/**
 * 所有配置的聚合类型
 */
export interface AllBotConfigs {
  botConfig: BotConfig | null
  personalityConfig: PersonalityConfig | null
  chatConfig: ChatConfig | null
  experimentalConfig: ExperimentalConfig | null
  expressionConfig: ExpressionConfig | null
  jargonConfig: JargonConfig | null
  emojiConfig: EmojiConfig | null
  voiceConfig: VoiceConfig | null
  messageReceiveConfig: MessageReceiveConfig | null
  dreamConfig: DreamConfig | null
  keywordReactionConfig: KeywordReactionConfig | null
  responsePostProcessConfig: ResponsePostProcessConfig | null
  chineseTypoConfig: ChineseTypoConfig | null
  responseSplitterConfig: ResponseSplitterConfig | null
  logConfig: LogConfig | null
  debugConfig: DebugConfig | null
  maimMessageConfig: MaimMessageConfig | null
  telemetryConfig: TelemetryConfig | null
}

/**
 * 配置节名称到类型的映射
 */
export type ConfigSectionName = 
  | 'bot'
  | 'personality'
  | 'chat'
  | 'experimental'
  | 'expression'
  | 'jargon'
  | 'emoji'
  | 'memory'
  | 'visual'
  | 'tool'
  | 'voice'
  | 'message_receive'
  | 'dream'
  | 'keyword_reaction'
  | 'response_post_process'
  | 'chinese_typo'
  | 'response_splitter'
  | 'log'
  | 'debug'
  | 'maim_message'
  | 'telemetry'
  | 'webui'
  | 'database'
  | 'mcp'
  | 'plugin'
  | 'plugin_runtime'
  | 'a_memorix'
