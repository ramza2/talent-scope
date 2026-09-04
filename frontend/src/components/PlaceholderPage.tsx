import { Typography } from 'antd'

type PlaceholderPageProps = {
  title: string
  description: string
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        {title}
      </Typography.Title>
      <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
      <Typography.Paragraph>
        이 화면은 IA/Wireframe 기준 Placeholder입니다. 실제 업무 API 연동은 다음
        단계에서 구현합니다.
      </Typography.Paragraph>
    </div>
  )
}
