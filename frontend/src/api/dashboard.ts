import { apiFetch } from '@/api/client'

export type DashboardPeopleSummary = {
  total: number
  active: number
  inactive: number
  archived: number
}

export type DashboardRecentPerson = {
  person_id: string
  name?: string | null
  status: string
  affiliation_company?: string | null
  technical_grade?: string | null
  created_at: string
  profile_updated_at?: string | null
}

export type DashboardPermissions = {
  can_manage_people: boolean
  can_manage_analyses: boolean
}

export type DashboardAnalysisSummary = {
  queued: number
  processing: number
  reviewing: number
  failed: number
  review_pending_runs: number
}

export type DashboardRecentAnalysis = {
  analysis_id: string
  person_id: string
  person_name?: string | null
  status: string
  pending_count: number
  created_at: string
  completed_at?: string | null
}

export type DashboardDocumentFailure = {
  document_id: string
  person_id: string
  person_name?: string | null
  original_filename: string
  processing_status: string
  processing_error?: string | null
  uploaded_at: string
}

export type DashboardData = {
  people: DashboardPeopleSummary
  recent_people: DashboardRecentPerson[]
  permissions: DashboardPermissions
  analysis: DashboardAnalysisSummary | null
  recent_analyses: DashboardRecentAnalysis[] | null
  document_failures: DashboardDocumentFailure[] | null
}

export type DashboardResponse = {
  data: DashboardData
}

export function getDashboard() {
  return apiFetch<DashboardResponse>('/dashboard')
}
