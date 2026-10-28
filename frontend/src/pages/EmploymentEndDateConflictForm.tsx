import { Alert, Input, Radio, Space, Typography } from 'antd'

import {
  EMPLOYMENT_END_CONFLICT_SEP,
  EMPLOYMENT_END_DATE_CUSTOM_VALUE,
  employmentEndDateSelectionError,
} from '@/api/employmentEndDateConflict'

export type EmploymentEndDateConflictFormProps = {
  candidates: string[]
  startDate?: string | null
  companyName?: string | null
  selected: string | null
  customDate: string
  onSelect: (value: string) => void
  onCustomDateChange: (value: string) => void
}

export function EmploymentEndDateConflictForm({
  candidates,
  startDate,
  companyName,
  selected,
  customDate,
  onSelect,
  onCustomDateChange,
}: EmploymentEndDateConflictFormProps) {
  const previewError =
    selected != null
      ? employmentEndDateSelectionError({
          selected,
          customDate,
          startDate,
        })
      : null

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="warning"
        showIcon
        message="종료일 충돌"
        description={
          <span>
            원문에 서로 다른 종료일이 있어 자동으로 확정할 수 없습니다.
            {companyName ? (
              <>
                {' '}
                회사: <strong>{companyName}</strong>
              </>
            ) : null}
            {startDate ? (
              <>
                {' '}
                · 시작일: <strong>{startDate}</strong>
              </>
            ) : null}
            . 아래 후보 중 하나를 선택하거나 직접 입력하세요.
          </span>
        }
      />
      <Typography.Text type="secondary">
        충돌 원문: {candidates.join(EMPLOYMENT_END_CONFLICT_SEP)}
      </Typography.Text>
      <Radio.Group
        value={selected ?? undefined}
        onChange={(e) => onSelect(e.target.value as string)}
        style={{ width: '100%' }}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          {candidates.map((candidate) => (
            <Radio key={candidate} value={candidate}>
              {candidate}
            </Radio>
          ))}
          <Radio value={EMPLOYMENT_END_DATE_CUSTOM_VALUE}>직접 입력</Radio>
        </Space>
      </Radio.Group>
      {selected === EMPLOYMENT_END_DATE_CUSTOM_VALUE ? (
        <Input
          value={customDate}
          placeholder="예: 2014.09.30"
          onChange={(e) => onCustomDateChange(e.target.value)}
          status={previewError ? 'error' : undefined}
        />
      ) : null}
      {selected && previewError ? (
        <Typography.Text type="danger">{previewError}</Typography.Text>
      ) : null}
    </Space>
  )
}
