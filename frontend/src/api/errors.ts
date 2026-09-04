/** Extract Problem+JSON detail for UI messages. */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (
    error &&
    typeof error === 'object' &&
    'body' in error &&
    error.body &&
    typeof error.body === 'object' &&
    'detail' in error.body &&
    typeof (error.body as { detail: unknown }).detail === 'string'
  ) {
    return (error.body as { detail: string }).detail
  }
  return fallback
}
