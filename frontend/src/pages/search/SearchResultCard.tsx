import {
  Button,
  Card,
  Collapse,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  ProfileOutlined,
} from '@ant-design/icons'
import { documentPreviewUrl } from '@/api/documents'
import { GRADE_LABELS, formatCareerMonths, type TechnicalGrade } from '@/api/people'
import type { EvidenceItem, MatchItem, SearchPersonResult } from '@/api/search'

function gradeLabel(grade?: string | null): string {
  if (!grade) return '등급 미정'
  if (grade in GRADE_LABELS) return GRADE_LABELS[grade as TechnicalGrade]
  return grade
}

function sourceLevelLabel(level: string): string {
  switch (level) {
    case 'CONFIRMED_PROFILE':
      return '확정 프로필 근거'
    case 'CONFIRMED_PROJECT':
      return '확정 프로젝트 근거'
    case 'DOCUMENT_CHUNK':
      return '원본문서 검색 근거'
    default:
      return level
  }
}

function MatchTags({ matches }: { matches: MatchItem[] }) {
  if (!matches.length) return null
  return (
    <Space wrap size={[8, 8]}>
      {matches.map((m, idx) => {
        const kind = m.type === 'REQUIRED' ? '필수' : '우대'
        const matched = m.status === 'MATCH'
        return (
          <Tag
            key={`${m.type}-${m.condition}-${idx}`}
            color={matched ? (m.type === 'REQUIRED' ? 'success' : 'processing') : 'default'}
            icon={matched ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
          >
            {kind}: {m.condition}
            {matched
              ? m.evidence_count > 0
                ? ` · 근거 ${m.evidence_count}건`
                : ' · 근거 없음'
              : ' · 미일치'}
          </Tag>
        )
      })}
    </Space>
  )
}

type Props = {
  result: SearchPersonResult
  onOpenProfile: (personId: string) => void
  onOpenProject: (projectId: string) => void
  onOpenEvidence: (evidenceId: string) => void
}

export function SearchResultCard({
  result,
  onOpenProfile,
  onOpenProject,
  onOpenEvidence,
}: Props) {
  const person = result.person
  const jobs = person.primary_jobs?.length ? person.primary_jobs.join(' · ') : '직무 미정'

  return (
    <Card size="small">
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }} align="start">
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              <Tag color="blue">적합도 {result.score}점</Tag>
              {person.name || '이름 없음'}
            </Typography.Title>
            <Typography.Text type="secondary">
              {gradeLabel(person.technical_grade)} · {jobs} ·{' '}
              {formatCareerMonths(person.career_months)}
            </Typography.Text>
          </div>
          <Button
            type="primary"
            ghost
            icon={<ProfileOutlined />}
            onClick={() => onOpenProfile(result.person_id)}
          >
            상세 프로필
          </Button>
        </Space>

        <Space wrap size={[4, 4]}>
          {(person.skills ?? []).map((s) => (
            <Tag key={`s-${s}`}>{s}</Tag>
          ))}
          {(person.expertise ?? []).map((e) => (
            <Tag key={`e-${e}`} color="purple">
              {e}
            </Tag>
          ))}
        </Space>

        <MatchTags matches={result.matches ?? []} />

        {(result.top_projects ?? []).length > 0 ? (
          <div>
            <Typography.Text strong>관련 프로젝트</Typography.Text>
            <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 6 }}>
              {result.top_projects.map((p) => (
                <Card key={p.project_id} size="small" type="inner">
                  <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                    <div>
                      <Button
                        type="link"
                        style={{ padding: 0, height: 'auto' }}
                        onClick={() => onOpenProject(p.project_id)}
                      >
                        {p.project_name}
                      </Button>
                      <div>
                        <Typography.Text type="secondary">
                          {p.period || '기간 미상'}
                          {(p.roles ?? []).length ? ` · ${p.roles.join(' · ')}` : ''}
                        </Typography.Text>
                      </div>
                      {(p.evidence_ids ?? []).length > 0 ? (
                        <Typography.Text type="secondary">
                          근거 {p.evidence_ids.length}건
                        </Typography.Text>
                      ) : null}
                    </div>
                  </Space>
                </Card>
              ))}
            </Space>
          </div>
        ) : null}

        {(result.evidence ?? []).length > 0 ? (
          <Collapse
            ghost
            items={[
              {
                key: 'evidence',
                label: `검색 근거 (${result.evidence.length})`,
                children: (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {result.evidence.map((item, idx) => (
                      <EvidenceRow
                        key={`${item.evidence_id ?? item.document_id ?? 'e'}-${idx}`}
                        item={item}
                        onOpenEvidence={onOpenEvidence}
                      />
                    ))}
                  </Space>
                ),
              },
            ]}
          />
        ) : null}
      </Space>
    </Card>
  )
}

function EvidenceRow({
  item,
  onOpenEvidence,
}: {
  item: EvidenceItem
  onOpenEvidence: (evidenceId: string) => void
}) {
  const title =
    item.document_title || item.original_filename || '문서'
  const metaParts = [
    sourceLevelLabel(item.source_level),
    title,
    item.version_no != null ? `v${item.version_no}` : null,
    item.page_no != null ? `${item.page_no}페이지` : null,
  ].filter(Boolean)

  const previewHref =
    item.document_id != null
      ? item.page_no != null
        ? `${documentPreviewUrl(item.document_id)}#page=${item.page_no}`
        : documentPreviewUrl(item.document_id)
      : null

  return (
    <Card size="small" type="inner">
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Typography.Text type="secondary">{metaParts.join(' · ')}</Typography.Text>
        {item.snippet ? (
          <Typography.Paragraph
            style={{ marginBottom: 0 }}
            ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
          >
            {item.snippet}
          </Typography.Paragraph>
        ) : null}
        <Space wrap>
          {item.evidence_id ? (
            <Button size="small" onClick={() => onOpenEvidence(item.evidence_id!)}>
              근거 상세
            </Button>
          ) : null}
          {previewHref ? (
            <Button
              size="small"
              icon={<FileTextOutlined />}
              href={previewHref}
              target="_blank"
              rel="noopener noreferrer"
            >
              원문 보기
            </Button>
          ) : null}
        </Space>
      </Space>
    </Card>
  )
}
