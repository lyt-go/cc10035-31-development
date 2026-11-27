/** 告警事发复验 / 复验结论单接口封装。
 *  路径同时兼容 alarm_trigger 与 alarm_recheck 两组后端别名。
 */
import { request } from './client'

export interface ReadingSnapshot {
  id?: number | string
  reading_id?: number | string
  测点编号?: string
  point_id?: string
  设备编号?: string
  device_id?: string
  钟点?: string
  observed_time?: string
  读数?: number
  value?: number
  测点说明?: string
  eligible?: boolean
  exclude_reason?: string | null
  排除原因?: string
  [key: string]: unknown
}

export interface RecheckOrder {
  id: number
  结论单编号?: string
  status?: string
  状态?: string
  accepted?: boolean
  关联测点?: string
  事发钟点?: string
  告警条件?: string
  operator?: string
  阈值?: number
  conclusion?: string | null
  结论?: string | null
  evidence?: ReadingSnapshot | null
  adopted_basis?: string
  实际采用依据?: string
  conflict_notice?: string
  冲突提示?: string
  detail_notice?: string
  pending_readings?: ReadingSnapshot[]
  待确认读数?: ReadingSnapshot[]
  qualified_readings?: ReadingSnapshot[]
  excluded_readings?: ReadingSnapshot[]
  consistent?: boolean | null
  immutable?: boolean
  rationale?: string
  判断说明?: string
  结论时间?: string | null
  concluded_at?: string | null
  message?: string
  [key: string]: unknown
}

async function postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function fetchScenario(): Promise<{
  incidents: Array<Record<string, unknown>>
  orders: RecheckOrder[]
  trigger_records: Array<Record<string, unknown>>
}> {
  return getJson('/api/alarm_trigger/scenario')
}

export function resetScenario(): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/alarm_trigger/recheck/reset')
}

export function queryReadings(body: Record<string, unknown>): Promise<{
  ok: boolean
  qualified_readings: ReadingSnapshot[]
  excluded_readings: ReadingSnapshot[]
  message?: string
}> {
  return postJson('/api/alarm_trigger/readings/query', body)
}

export function seedLate85(): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/alarm_trigger/seed-late-85')
}

export function ingestReading(body: Record<string, unknown>): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/alarm_trigger/readings', body)
}

export function previewRecheck(body: Record<string, unknown>): Promise<RecheckOrder & { query?: unknown }> {
  return postJson('/api/alarm_trigger/recheck/preview', body)
}

export function submitRecheck(body: Record<string, unknown>): Promise<RecheckOrder> {
  return postJson('/api/alarm_trigger/recheck', body)
}

export function confirmReference(id: number | string, readingId: number | string): Promise<RecheckOrder> {
  return postJson(`/api/alarm_trigger/recheck/${id}/confirm`, { reading_id: readingId })
}

export function confirmNoEvidence(id: number | string): Promise<RecheckOrder> {
  return postJson(`/api/alarm_trigger/recheck/${id}/no-evidence`, {})
}

export function reopenRecheck(id: number | string): Promise<RecheckOrder> {
  return postJson(`/api/alarm_trigger/recheck/${id}/reopen`, {})
}

export function fetchRecheckDetail(id: number | string): Promise<RecheckOrder> {
  return getJson(`/api/alarm_trigger/recheck/${id}`)
}

export function fetchRecheckList(): Promise<{ items: RecheckOrder[]; total: number }> {
  return getJson('/api/alarm_trigger/rechecks')
}

export function fetchTriggerRecords(): Promise<{
  items: Array<Record<string, unknown>>
  total: number
}> {
  return getJson('/api/alarm_trigger/trigger-records')
}
