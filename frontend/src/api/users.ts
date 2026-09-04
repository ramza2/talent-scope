import { apiFetch } from '@/api/client'

export type UserRole = 'USER' | 'ADMIN'
export type UserStatus = 'ACTIVE' | 'INACTIVE'

export type UserItem = {
  id: string
  login_id: string
  name: string
  email?: string | null
  department?: string | null
  role: UserRole
  status: UserStatus
  last_login_at?: string | null
  created_at: string
  updated_at: string
}

export type PageMeta = {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export type UserListResponse = { data: UserItem[]; meta: PageMeta }
export type UserDetailResponse = { data: UserItem }

export type UserFilters = {
  q?: string
  role?: UserRole | ''
  status?: UserStatus | ''
  page?: number
  page_size?: number
}

export function listUsers(filters: UserFilters = {}) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.role) params.set('role', filters.role)
  if (filters.status) params.set('status', filters.status)
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.page_size ?? 20))
  return apiFetch<UserListResponse>(`/users?${params.toString()}`)
}

export function getUser(userId: string) {
  return apiFetch<UserDetailResponse>(`/users/${userId}`)
}

export function createUser(body: {
  login_id: string
  name: string
  email?: string | null
  department?: string | null
  role: UserRole
  password: string
}) {
  return apiFetch<UserDetailResponse>('/users', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateUser(
  userId: string,
  body: {
    name?: string
    email?: string | null
    department?: string | null
    role?: UserRole
    status?: UserStatus
  },
) {
  return apiFetch<UserDetailResponse>(`/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function resetUserPassword(userId: string, newPassword: string) {
  return apiFetch<void>(`/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ new_password: newPassword }),
  })
}
