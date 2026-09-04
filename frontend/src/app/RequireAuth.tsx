import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Spin } from 'antd'

import { useAuthMe } from '@/app/auth'

export function RequireAuth() {
  const location = useLocation()
  const { data: user, isLoading, isError } = useAuthMe()

  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  if (isError || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}

export function RequireAdmin() {
  const { data: user, isLoading } = useAuthMe()

  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (user.role !== 'ADMIN') {
    return <Navigate to="/forbidden" replace />
  }

  return <Outlet />
}

export function PublicOnly() {
  const { data: user, isLoading } = useAuthMe()
  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }
  if (user) {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}
