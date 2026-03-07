import axios from 'axios'

import { getApiBaseUrl } from './api-base'

const apiClient = axios.create({
  baseURL: '', // 统一为空，通过拦截器动态设置
  timeout: 10000,
})

// Electron 端：动态注入后端 URL；浏览器端 getApiBaseUrl() 返回空字符串，行为不变
apiClient.interceptors.request.use(async (config) => {
  const baseUrl = await getApiBaseUrl()
  if (baseUrl && !config.baseURL) {
    config.baseURL = baseUrl
  }
  return config
})

export default apiClient
