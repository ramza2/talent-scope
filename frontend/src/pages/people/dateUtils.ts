import dayjs, { type Dayjs } from 'dayjs'

/** Format Ant Design DatePicker value to backend ISO date (YYYY-MM-DD). */
export function toIsoDate(value: Dayjs | string | null | undefined): string | null {
  if (value == null || value === '') return null
  if (typeof value === 'string') {
    const parsed = dayjs(value)
    return parsed.isValid() ? parsed.format('YYYY-MM-DD') : null
  }
  return value.isValid() ? value.format('YYYY-MM-DD') : null
}

export function fromIsoDate(value?: string | null): Dayjs | undefined {
  if (!value) return undefined
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed : undefined
}

export function formatPeriod(
  start?: string | null,
  end?: string | null,
  ongoingLabel = '현재',
): string {
  const s = start ? dayjs(start).format('YYYY.MM') : '—'
  const e = end ? dayjs(end).format('YYYY.MM') : ongoingLabel
  return `${s} ~ ${e}`
}
