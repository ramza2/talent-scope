import { Result, Button } from 'antd'
import { useNavigate } from 'react-router-dom'

export function ForbiddenPage() {
  const navigate = useNavigate()
  return (
    <Result
      status="403"
      title="403"
      subTitle="이 화면에 접근할 권한이 없습니다."
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          대시보드로 이동
        </Button>
      }
    />
  )
}
