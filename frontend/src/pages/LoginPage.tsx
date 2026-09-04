import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError, login } from '@/api/client'
import { authMeQueryKey } from '@/app/auth'

type LoginForm = {
  login_id: string
  password: string
}

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onFinish = async (values: LoginForm) => {
    setSubmitting(true)
    setError(null)
    try {
      await login(values.login_id, values.password)
      await queryClient.invalidateQueries({ queryKey: authMeQueryKey })
      const redirectTo =
        (location.state as { from?: string } | null)?.from &&
        (location.state as { from?: string }).from !== '/login'
          ? (location.state as { from: string }).from
          : '/'
      navigate(redirectTo, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('아이디 또는 비밀번호를 확인해주세요.')
      } else {
        setError('로그인에 실패했습니다. 잠시 후 다시 시도해주세요.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(160deg, #eef5f2 0%, #f7f7f5 55%, #e8eef2 100%)',
        padding: 24,
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 8px 28px rgba(15, 40, 35, 0.08)' }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          TalentScope
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          사내 인력 프로필 관리 · 검색
        </Typography.Paragraph>
        {error ? (
          <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} />
        ) : null}
        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            label="Login ID"
            name="login_id"
            rules={[{ required: true, message: 'Login ID를 입력하세요.' }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="Password"
            name="password"
            rules={[{ required: true, message: 'Password를 입력하세요.' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            로그인
          </Button>
        </Form>
      </Card>
    </div>
  )
}
