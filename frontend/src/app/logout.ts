import { message } from 'antd'
import type { QueryClient } from '@tanstack/react-query'

import { ApiError, logout } from '@/api/client'
import { authMeQueryKey } from '@/app/auth'

export type LogoutClientAction = 'clear_and_redirect' | 'keep_session'

/**
 * Decide whether Client auth state should be cleared after a logout attempt.
 * Server Session invalidation must be confirmed (or already gone) before clearing.
 */
export function resolveLogoutClientAction(error: unknown): LogoutClientAction {
  if (!(error instanceof ApiError)) {
    return 'keep_session'
  }
  if (error.status === 401) {
    return 'clear_and_redirect'
  }
  return 'keep_session'
}

export async function performLogout(queryClient: QueryClient): Promise<LogoutClientAction> {
  try {
    await logout()
    await queryClient.invalidateQueries({ queryKey: authMeQueryKey })
    queryClient.setQueryData(authMeQueryKey, null)
    return 'clear_and_redirect'
  } catch (error) {
    const action = resolveLogoutClientAction(error)

    if (action === 'clear_and_redirect') {
      await queryClient.invalidateQueries({ queryKey: authMeQueryKey })
      queryClient.setQueryData(authMeQueryKey, null)
      return action
    }

    if (error instanceof ApiError && error.status === 403) {
      message.error('로그아웃에 실패했습니다. CSRF 검증에 실패했습니다.')
      await queryClient.invalidateQueries({ queryKey: authMeQueryKey })
      return 'keep_session'
    }

    message.error('로그아웃에 실패했습니다. 잠시 후 다시 시도해주세요.')
    return 'keep_session'
  }
}
