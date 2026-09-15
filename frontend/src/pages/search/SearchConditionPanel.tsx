import {
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd'
import { ClearOutlined, RobotOutlined, SearchOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'

import { listCodes, type CodeType } from '@/api/codes'
import { GRADE_LABELS, type TechnicalGrade } from '@/api/people'
import type { SkillMatchMode } from '@/api/search'
import {
  codeSelectOptions,
  filterCodeOption,
  hasActiveSearchConditions,
  monthsHelperText,
  type SearchDraftQuery,
} from '@/pages/search/searchState'

const GRADE_OPTIONS = (Object.entries(GRADE_LABELS) as Array<[TechnicalGrade, string]>).map(
  ([value, label]) => ({ value, label: `${label} (${value})` }),
)

type Props = {
  draft: SearchDraftQuery
  onChange: (next: SearchDraftQuery) => void
  naturalText: string
  onNaturalTextChange: (value: string) => void
  followUpEnabled: boolean
  onFollowUpChange: (value: boolean) => void
  assumptions: string[]
  interpretPending: boolean
  searchPending: boolean
  onInterpret: () => void
  onSearch: () => void
  onReset: () => void
}

function useCodeOptions(type: CodeType, orphanCodes: string[]) {
  const query = useQuery({
    queryKey: ['codes', type, 'search'],
    queryFn: () => listCodes({ type, active: true }),
  })
  const codes = query.data?.data ?? []
  return {
    options: codeSelectOptions(codes, orphanCodes),
    loading: query.isLoading,
    refetch: query.refetch,
  }
}

function CodeMultiSelect({
  type,
  value,
  onChange,
  placeholder,
}: {
  type: CodeType
  value: string[]
  onChange: (next: string[]) => void
  placeholder: string
}) {
  const { options, loading } = useCodeOptions(type, value)
  return (
    <Select
      mode="multiple"
      allowClear
      showSearch
      loading={loading}
      placeholder={placeholder}
      value={value}
      onChange={onChange}
      options={options}
      filterOption={filterCodeOption}
      style={{ width: '100%' }}
      maxTagCount="responsive"
    />
  )
}

export function SearchConditionPanel({
  draft,
  onChange,
  naturalText,
  onNaturalTextChange,
  followUpEnabled,
  onFollowUpChange,
  assumptions,
  interpretPending,
  searchPending,
  onInterpret,
  onSearch,
  onReset,
}: Props) {
  const required = draft.required
  const preferred = draft.preferred
  const followUpAvailable = hasActiveSearchConditions(draft)

  const patchRequired = (patch: Partial<typeof required>) => {
    onChange({
      ...draft,
      required: { ...required, ...patch },
    })
  }

  const patchPreferred = (patch: Partial<typeof preferred>) => {
    onChange({
      ...draft,
      preferred: { ...preferred, ...patch },
    })
  }

  const careerMin = required.career?.min_months ?? null
  const careerMax = required.career?.max_months ?? null

  const setCareer = (min: number | null, max: number | null) => {
    if (min == null && max == null) {
      patchRequired({ career: null })
      return
    }
    patchRequired({ career: { min_months: min, max_months: max } })
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title="자연어 검색">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Input.TextArea
            value={naturalText}
            onChange={(e) => onNaturalTextChange(e.target.value)}
            placeholder="예: 특급 DBA 중 Oracle 경험이 있고 금융 프로젝트 경험이 있으면 좋겠어"
            maxLength={2000}
            showCount
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={interpretPending}
          />
          <Space wrap>
            <Switch
              checked={followUpEnabled && followUpAvailable}
              disabled={!followUpAvailable || interpretPending}
              onChange={onFollowUpChange}
            />
            <Typography.Text type={followUpAvailable ? undefined : 'secondary'}>
              현재 조건에 이어서 해석
            </Typography.Text>
          </Space>
          <Space wrap>
            <Button
              type="default"
              icon={<RobotOutlined />}
              loading={interpretPending}
              disabled={!naturalText.trim() || searchPending}
              onClick={onInterpret}
            >
              AI 조건 해석
            </Button>
            <Typography.Text type="secondary">
              해석만 수행합니다. 실제 검색은 아래에서 실행하세요.
            </Typography.Text>
          </Space>
          {assumptions.length > 0 ? (
            <Card size="small" type="inner" title="AI 해석 기준">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {assumptions.map((item) => (
                  <li key={item}>
                    <Typography.Text>{item}</Typography.Text>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </Space>
      </Card>

      <Card size="small" title="적용 조건">
        <Form layout="vertical">
          <Typography.Text strong>기본 조건 (필수)</Typography.Text>
          <div style={{ marginTop: 12 }}>
            <Form.Item label="직무">
              <CodeMultiSelect
                type="JOB"
                value={required.jobs}
                onChange={(jobs) => patchRequired({ jobs })}
                placeholder="직무 선택"
              />
            </Form.Item>
            <Form.Item label="기술등급">
              <Select
                mode="multiple"
                allowClear
                placeholder="등급 선택"
                value={required.grade?.values ?? []}
                onChange={(values: TechnicalGrade[]) =>
                  patchRequired({
                    grade: values.length ? { values } : null,
                  })
                }
                options={GRADE_OPTIONS}
                style={{ width: '100%' }}
              />
            </Form.Item>
            <Form.Item
              label="경력(개월)"
              extra={
                <Space split="·">
                  {monthsHelperText(careerMin) ? (
                    <span>최소 {monthsHelperText(careerMin)}</span>
                  ) : null}
                  {monthsHelperText(careerMax) ? (
                    <span>최대 {monthsHelperText(careerMax)}</span>
                  ) : null}
                  <span>예: 36개월 = 3년</span>
                </Space>
              }
            >
              <Space wrap>
                <InputNumber
                  min={0}
                  step={1}
                  precision={0}
                  controls
                  inputMode="numeric"
                  placeholder="최소"
                  value={careerMin ?? undefined}
                  onKeyDown={(e) => {
                    if (['.', ',', 'e', 'E', '+', '-'].includes(e.key)) {
                      e.preventDefault()
                    }
                  }}
                  onChange={(v) =>
                    setCareer(
                      typeof v === 'number' && Number.isInteger(v) ? v : null,
                      careerMax,
                    )
                  }
                />
                <Typography.Text>~</Typography.Text>
                <InputNumber
                  min={0}
                  step={1}
                  precision={0}
                  controls
                  inputMode="numeric"
                  placeholder="최대"
                  value={careerMax ?? undefined}
                  onKeyDown={(e) => {
                    if (['.', ',', 'e', 'E', '+', '-'].includes(e.key)) {
                      e.preventDefault()
                    }
                  }}
                  onChange={(v) =>
                    setCareer(
                      careerMin,
                      typeof v === 'number' && Number.isInteger(v) ? v : null,
                    )
                  }
                />
              </Space>
            </Form.Item>
            <Form.Item label="기술">
              <CodeMultiSelect
                type="TECH"
                value={required.skills}
                onChange={(skills) => patchRequired({ skills })}
                placeholder="기술 선택"
              />
            </Form.Item>
            {required.skills.length >= 2 ? (
              <Form.Item label="기술 조건">
                <Radio.Group
                  value={draft.skill_match_mode}
                  onChange={(e) =>
                    onChange({
                      ...draft,
                      skill_match_mode: e.target.value as SkillMatchMode,
                    })
                  }
                  optionType="button"
                  buttonStyle="solid"
                  options={[
                    { value: 'ANY', label: '하나 이상(ANY)' },
                    { value: 'ALL', label: '모두 포함(ALL)' },
                  ]}
                />
              </Form.Item>
            ) : null}
            <Form.Item label="전문분야">
              <CodeMultiSelect
                type="EXP"
                value={required.expertise}
                onChange={(expertise) => patchRequired({ expertise })}
                placeholder="전문분야 선택 (예: RAG)"
              />
            </Form.Item>
          </div>

          <Collapse
            style={{ marginTop: 8 }}
            items={[
              {
                key: 'detail',
                label: '상세 조건',
                children: (
                  <>
                    <Form.Item label="사업분야">
                      <CodeMultiSelect
                        type="BIZ"
                        value={required.business_domains}
                        onChange={(business_domains) =>
                          patchRequired({ business_domains })
                        }
                        placeholder="사업분야 선택"
                      />
                    </Form.Item>
                    <Form.Item label="고객유형">
                      <CodeMultiSelect
                        type="CUSTOMER_TYPE"
                        value={required.customer_types}
                        onChange={(customer_types) => patchRequired({ customer_types })}
                        placeholder="고객유형 선택"
                      />
                    </Form.Item>
                    <Form.Item
                      label="현재 소속"
                      extra="현재 소속회사 기준입니다. 과거 근무 경험이 아닙니다."
                    >
                      <Select
                        mode="tags"
                        allowClear
                        tokenSeparators={[',']}
                        placeholder="현재 소속회사"
                        value={required.affiliations}
                        onChange={(affiliations) => patchRequired({ affiliations })}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                    <Form.Item label="자격증">
                      <Select
                        mode="tags"
                        allowClear
                        tokenSeparators={[',']}
                        placeholder="예: 정보처리기사, AWS"
                        value={required.certifications}
                        onChange={(certifications) => patchRequired({ certifications })}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                    <Form.Item
                      label="프로젝트 키워드"
                      extra="프로젝트명·고객명·책임·요약을 대상으로 하는 필수 키워드입니다."
                    >
                      <Select
                        mode="tags"
                        allowClear
                        tokenSeparators={[',']}
                        placeholder="예: DEMIS, SNUH.AI"
                        value={required.project_keywords}
                        onChange={(project_keywords) =>
                          patchRequired({ project_keywords })
                        }
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                    <Form.Item
                      label="정확 키워드 검색"
                      extra="고유 프로젝트명·제품명·기술어 등 (최대 500자)"
                    >
                      <Input
                        allowClear
                        maxLength={500}
                        showCount
                        value={draft.keyword_query ?? ''}
                        onChange={(e) =>
                          onChange({
                            ...draft,
                            keyword_query: e.target.value,
                          })
                        }
                        placeholder="정확 키워드"
                      />
                    </Form.Item>
                    <Form.Item
                      label="의미 검색"
                      extra="의미 기반 검색어입니다. Frontend는 Embedding API를 직접 호출하지 않습니다."
                    >
                      <Input.TextArea
                        allowClear
                        autoSize={{ minRows: 2, maxRows: 4 }}
                        value={draft.semantic_query ?? ''}
                        onChange={(e) =>
                          onChange({
                            ...draft,
                            semantic_query: e.target.value,
                          })
                        }
                        placeholder="예: 병원 의료데이터 기반 진단보조 AI 경험"
                      />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'preferred',
                label: '우대 조건',
                children: (
                  <>
                    <Form.Item label="우대 직무">
                      <CodeMultiSelect
                        type="JOB"
                        value={preferred.jobs}
                        onChange={(jobs) => patchPreferred({ jobs })}
                        placeholder="우대 직무"
                      />
                    </Form.Item>
                    <Form.Item label="우대 기술">
                      <CodeMultiSelect
                        type="TECH"
                        value={preferred.skills}
                        onChange={(skills) => patchPreferred({ skills })}
                        placeholder="우대 기술"
                      />
                    </Form.Item>
                    <Form.Item label="우대 전문분야">
                      <CodeMultiSelect
                        type="EXP"
                        value={preferred.expertise}
                        onChange={(expertise) => patchPreferred({ expertise })}
                        placeholder="우대 전문분야"
                      />
                    </Form.Item>
                    <Form.Item label="우대 사업분야">
                      <CodeMultiSelect
                        type="BIZ"
                        value={preferred.business_domains}
                        onChange={(business_domains) =>
                          patchPreferred({ business_domains })
                        }
                        placeholder="우대 사업분야"
                      />
                    </Form.Item>
                    <Form.Item label="우대 고객유형">
                      <CodeMultiSelect
                        type="CUSTOMER_TYPE"
                        value={preferred.customer_types}
                        onChange={(customer_types) =>
                          patchPreferred({ customer_types })
                        }
                        placeholder="우대 고객유형"
                      />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />

          <Space wrap style={{ marginTop: 16 }}>
            <Button
              type="primary"
              icon={<SearchOutlined />}
              loading={searchPending}
              disabled={interpretPending}
              onClick={onSearch}
            >
              검색
            </Button>
            <Button icon={<ClearOutlined />} disabled={interpretPending || searchPending} onClick={onReset}>
              전체 초기화
            </Button>
          </Space>
        </Form>
      </Card>
    </Space>
  )
}
