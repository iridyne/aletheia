export function parseCsvList(raw: string): string[] {
  return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
}

export function listToCsv(value: unknown): string {
  return Array.isArray(value) ? value.join(', ') : ''
}
