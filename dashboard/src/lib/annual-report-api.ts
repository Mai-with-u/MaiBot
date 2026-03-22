/**
 * 年度报告 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface AnnualReportData {
  year: number
  totalMessages: number
  totalUsers: number
  topWords: { word: string; count: number }[]
  activityByMonth: { month: number; count: number }[]
  topUsers: { id: string; name: string; messageCount: number }[]
  memorableMoments: string[]
}

export async function getAnnualReport(year?: number): Promise<{ success: boolean; data: AnnualReportData }> {
  const yearParam = year ? `?year=${year}` : ''
  const response = await fetchWithAuth(`/api/webui/annual-report${yearParam}`)
  return response.json()
}
