import { apiFetch } from '@/api/client'

export type EmploymentItem = {
  id: string
  person_id: string
  company_name: string
  department?: string | null
  title?: string | null
  start_date?: string | null
  end_date?: string | null
  responsibilities?: string | null
  source_type: string
  created_at: string
  updated_at: string
}

export type EducationItem = {
  id: string
  person_id: string
  school_name: string
  major?: string | null
  degree?: string | null
  start_date?: string | null
  end_date?: string | null
  status?: string | null
  source_type: string
  created_at: string
  updated_at: string
}

export type CertificationItem = {
  id: string
  person_id: string
  certification_name: string
  issuer?: string | null
  acquired_date?: string | null
  expiry_date?: string | null
  certificate_no?: string | null
  source_type: string
  created_at: string
  updated_at: string
}

export function listEmploymentHistory(personId: string) {
  return apiFetch<{ data: EmploymentItem[] }>(`/people/${personId}/employment-history`)
}

export function createEmploymentHistory(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: EmploymentItem }>(`/people/${personId}/employment-history`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateEmploymentHistory(id: string, body: Record<string, unknown>) {
  return apiFetch<{ data: EmploymentItem }>(`/employment-history/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteEmploymentHistory(id: string) {
  return apiFetch<void>(`/employment-history/${id}`, { method: 'DELETE' })
}

export function listEducation(personId: string) {
  return apiFetch<{ data: EducationItem[] }>(`/people/${personId}/education`)
}

export function createEducation(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: EducationItem }>(`/people/${personId}/education`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateEducation(id: string, body: Record<string, unknown>) {
  return apiFetch<{ data: EducationItem }>(`/education/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteEducation(id: string) {
  return apiFetch<void>(`/education/${id}`, { method: 'DELETE' })
}

export function listCertifications(personId: string) {
  return apiFetch<{ data: CertificationItem[] }>(`/people/${personId}/certifications`)
}

export function createCertification(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: CertificationItem }>(`/people/${personId}/certifications`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateCertification(id: string, body: Record<string, unknown>) {
  return apiFetch<{ data: CertificationItem }>(`/certifications/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteCertification(id: string) {
  return apiFetch<void>(`/certifications/${id}`, { method: 'DELETE' })
}
