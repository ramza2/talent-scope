import { Alert, Descriptions, Drawer, Empty, Spin, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'

import { apiErrorMessage } from '@/api/errors'
import { getProject } from '@/api/projects'
import { formatCareerMonths } from '@/api/people'

type Props = {
  projectId: string | null
  open: boolean
  onClose: () => void
}

export function SearchProjectDrawer({ projectId, open, onClose }: Props) {
  const query = useQuery({
    queryKey: ['projects', projectId],
    queryFn: () => getProject(projectId!),
    enabled: open && Boolean(projectId),
  })

  const project = query.data?.data

  return (
    <Drawer
      title="프로젝트 상세"
      width={520}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {query.isLoading ? <Spin /> : null}
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          message={apiErrorMessage(query.error, '프로젝트 정보를 불러오지 못했습니다.')}
        />
      ) : null}
      {!query.isLoading && !query.isError && !project ? (
        <Empty description="프로젝트 정보가 없습니다." />
      ) : null}
      {project ? (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="프로젝트명">{project.project_name}</Descriptions.Item>
          <Descriptions.Item label="고객명">
            {project.customer_name || '—'}
          </Descriptions.Item>
          <Descriptions.Item label="기간">
            {[project.start_date, project.end_date].filter(Boolean).join(' ~ ') || '—'}
          </Descriptions.Item>
          <Descriptions.Item label="수행기간">
            {formatCareerMonths(project.duration_months)}
          </Descriptions.Item>
          <Descriptions.Item label="역할">
            {(project.jobs ?? []).map((j) => (
              <Tag key={j.code}>{j.name || j.code}</Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label="기술">
            {(project.skills ?? []).map((s) => (
              <Tag key={s.code}>{s.name || s.code}</Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label="전문분야">
            {(project.expertise ?? []).map((e) => (
              <Tag key={e.code}>
                {e.name || e.code}
                {e.evidence_type === 'INFERRED' ? ' (추정)' : ''}
              </Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label="사업분야">
            {(project.business_domains ?? []).map((b) => (
              <Tag key={b.code}>{b.name || b.code}</Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label="고객유형">
            {(project.customer_types ?? []).map((c) => (
              <Tag key={c.code}>{c.name || c.code}</Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label="책임/역할 설명">
            <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {project.responsibilities || '—'}
            </Typography.Paragraph>
          </Descriptions.Item>
          <Descriptions.Item label="요약">
            <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {project.project_summary || '—'}
            </Typography.Paragraph>
          </Descriptions.Item>
        </Descriptions>
      ) : null}
    </Drawer>
  )
}
