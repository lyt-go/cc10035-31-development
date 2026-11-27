<template>
  <section
    id="alarm-recheck-panel"
    class="recheck-panel"
    data-testid="alarm-recheck-panel"
    data-module="alarm_trigger"
  >
    <header class="rc-head">
      <div>
        <h3>告警事发复验 · 复验结论单</h3>
        <p class="rc-tip">
          按<strong>告警事发时刻</strong>查询本测点读数作为参照：事发之后的读数不用，
          <strong>其他测点</strong>的读数不用，<strong>同一设备的相邻探头不能作为替代</strong>。
          没有合格读数时可出具「无证据」结论，不会用 0 补出一条触发记录。
        </p>
      </div>
      <button class="btn ghost" type="button" data-testid="reset-scenario" @click="resetDemo">
        重置演示场景
      </button>
    </header>

    <!-- 告警事件（事发单）选择 -->
    <div class="incident-tabs" data-testid="incident-list">
      <button
        v-for="incident in incidents"
        :key="String(incident['事件编号'])"
        type="button"
        class="incident-card"
        :class="{ active: currentCode === incident['事件编号'] }"
        :data-testid="`incident-${incident['事件编号']}`"
        @click="selectIncident(String(incident['事件编号']))"
      >
        <span class="inc-code">{{ incident['事件编号'] }}</span>
        <span class="inc-meta">
          事发 {{ incident['事发钟点'] }} · {{ incident['关联测点'] }}
        </span>
      </button>
    </div>

    <p v-if="banner" class="rc-banner" data-testid="panel-message">{{ banner }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="panel-error">{{ errorMessage }}</p>

    <div class="rc-grid">
      <!-- ① 参照查询与提交 -->
      <div class="rc-col">
        <div class="rc-card">
          <div class="rc-card-head">
            <h4>① 按事发时刻查询参照读数（预览）</h4>
            <button
              class="btn"
              type="button"
              data-testid="query-references"
              :disabled="loading"
              @click="doPreview"
            >
              {{ loading ? '查询中…' : '查询参照读数' }}
            </button>
          </div>
          <p class="rc-context">
            事件 {{ currentIncident?.['事件编号'] }} ｜ 测点 {{ currentIncident?.['关联测点'] }}
            ｜ 设备 {{ currentIncident?.['设备编号'] }} ｜ 事发
            <strong>{{ currentIncident?.['事发钟点'] }}</strong>
          </p>

          <!-- 迟到证据演练 -->
          <div class="late-box" data-testid="late-reading-box">
            <button
              class="btn primary"
              type="button"
              data-testid="seed-late-85"
              @click="addLate85"
            >
              模拟：本点 08:04 的 85 迟到
            </button>
            <span class="late-custom">
              <label>
                时刻
                <input v-model="lateTime" data-testid="late-time" aria-label="迟到读数时刻" size="6" />
              </label>
              <label>
                读数
                <input v-model="lateValue" data-testid="late-value" aria-label="迟到读数值" size="6" />
              </label>
              <button class="btn" type="button" data-testid="simulate-late-reading" @click="addLateCustom">
                让该读数迟到
              </button>
            </span>
          </div>

          <!-- 合格读数 -->
          <h5>合格参照（仅本测点、且不晚于事发时刻）</h5>
          <div v-if="!qualified.length" class="no-evidence-hint" data-testid="no-qualified-reading">
            查询响应：没有合格读数。可在下方提交后确认「无证据」结论。
          </div>
          <table v-else class="data-table rc-table" data-testid="qualified-readings">
            <thead>
              <tr>
                <th>确认采用</th><th>读数时刻</th><th>读数值</th><th>来源说明</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="reading in qualified"
                :key="String(reading.id)"
                :data-testid="`qualified-reading-${reading.id}`"
              >
                <td>
                  <input
                    type="radio"
                    name="reference-reading"
                    :value="reading.id"
                    v-model="selectedReadingId"
                    :data-testid="`pick-reading-${reading.id}`"
                    :aria-label="`采用 ${reading['钟点']} 读数 ${reading['读数']}`"
                    @change="userTouchedReading = true"
                  />
                </td>
                <td>{{ reading['钟点'] }}</td>
                <td><strong>{{ reading['读数'] }}</strong></td>
                <td>{{ reading['测点说明'] || reading['point_id'] }}</td>
              </tr>
            </tbody>
          </table>

          <!-- 排除读数 -->
          <h5>已排除读数（不得作为本次参照）</h5>
          <table v-if="excluded.length" class="data-table rc-table" data-testid="excluded-readings">
            <thead>
              <tr><th>时刻</th><th>读数</th><th>测点</th><th>排除原因</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="reading in excluded"
                :key="`x-${reading.id}`"
                class="excluded-row"
                :data-testid="`excluded-reading-${reading.id}`"
              >
                <td>{{ reading['钟点'] }}</td>
                <td>{{ reading['读数'] }}</td>
                <td>{{ reading['point_id'] }}</td>
                <td class="exclude-reason">{{ reading['排除原因'] }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="rc-muted">暂无被排除的读数。</p>
        </div>

        <!-- ② 阈值 + 提交 -->
        <div class="rc-card">
          <h4>② 阈值评估联动 · 提交复验</h4>
          <div class="condition-row">
            <label class="cond-text">
              <span>告警条件</span>
              <input
                v-model="conditionText"
                data-testid="recheck-condition"
                aria-label="告警条件"
                placeholder="如 >80 或 大于80"
                @input="onConditionTextInput"
              />
            </label>
            <label>
              <span>运算符</span>
              <select v-model="operator" data-testid="recheck-operator" aria-label="运算符" @change="syncCondition">
                <option value=">">大于</option>
                <option value=">=">大于等于</option>
                <option value="<">小于</option>
                <option value="<=">小于等于</option>
                <option value="==">等于</option>
              </select>
            </label>
            <label>
              <span>阈值</span>
              <input
                v-model="threshold"
                data-testid="recheck-threshold"
                aria-label="阈值"
                inputmode="decimal"
                @input="syncCondition"
              />
            </label>
          </div>
          <div class="submit-row">
            <button
              class="btn"
              type="button"
              data-testid="confirm-reference"
              :disabled="selectedReadingId == null || busy"
              @click="confirmSelectedReference"
            >
              确认所选参照
            </button>
            <button
              class="btn primary"
              type="button"
              data-testid="submit-recheck"
              :disabled="busy"
              @click="submit"
            >
              {{ busy ? '提交中…' : '提交复验' }}
            </button>
          </div>
          <p class="rc-muted">
            提交后由告警服务按<strong>实际确认的参照读数</strong>评估阈值并决定是否触发，
            结论、读数证据与结论时间冻结在同一张结论单。
          </p>
        </div>
      </div>

      <!-- ③ 原详情单 -->
      <div class="rc-col">
        <div class="rc-card detail-card" id="alarm-recheck-detail-anchor" data-testid="recheck-detail">
          <div class="rc-card-head">
            <h4>③ 复验详情（原单办理 / 回看）</h4>
            <span v-if="detail" class="order-code" data-testid="detail-order-no">
              {{ detail['结论单编号'] }}
            </span>
          </div>

          <p v-if="!detail" class="rc-muted">
            提交后在此张原详情单核对最终证据与判断；若尚未受理，按本页所列读数继续办理即可。
          </p>

          <template v-else>
            <div class="badges">
              <span class="status-badge" :data-testid="`order-status-${detail.id}`">
                {{ detail['status'] || detail['状态'] }}
              </span>
              <span
                v-if="detail['结论']"
                class="conclusion-badge"
                :class="conclusionClass"
                data-testid="conclusion-badge"
              >
                结论：{{ detail['结论'] }}
              </span>
            </div>

            <dl class="detail-grid">
              <div><dt>告警事件</dt><dd>{{ detail['事件编号'] || '—' }}</dd></div>
              <div><dt>关联测点</dt><dd>{{ detail['关联测点'] || '—' }}</dd></div>
              <div><dt>事发时刻</dt><dd>{{ detail['事发钟点'] || '—' }}</dd></div>
              <div><dt>告警条件</dt><dd data-testid="detail-condition">{{ detail['告警条件'] }}</dd></div>
              <div>
                <dt>结论时间</dt>
                <dd data-testid="concluded-at">{{ detail['结论时间'] || '尚未办结' }}</dd>
              </div>
              <div v-if="detail.evidence">
                <dt>采用读数（证据）</dt>
                <dd data-testid="adopted-evidence">
                  本点 {{ detail.evidence['钟点'] }} 的
                  <strong>{{ detail.evidence['读数'] }}</strong>
                  （编号 {{ detail.evidence['reading_id'] }}）
                </dd>
              </div>
            </dl>

            <!-- 已完成：冻结结论 -->
            <template v-if="detail['status'] === '已完成'">
              <p class="rationale" data-testid="rationale">{{ detail['判断说明'] }}</p>
              <p class="basis" data-testid="adopted-basis">{{ detail['实际采用依据'] || detail['adopted_basis'] }}</p>
              <p v-if="detail['冲突提示']" class="conflict-notice" data-testid="conflict-notice">
                ⚠ {{ detail['冲突提示'] }}
              </p>
              <p class="immutable-hint">
                结论、读数和时间属于同一次复验；已经确认的结论不随重新查询变化。
              </p>
              <p v-if="detail.consistent != null" class="consistency" data-testid="consistency">
                重开核对：冻结证据按同一条件复算，与当次判断
                <strong :class="detail.consistent ? 'ok-text' : 'bad-text'">
                  {{ detail.consistent ? '一致' : '不一致' }}
                </strong>
                （已重开 {{ detail.reopen_count || 0 }} 次）
              </p>
              <div class="submit-row">
                <button class="btn" type="button" data-testid="requery-detail" @click="requeryDetail">
                  重新查询（结论应不变）
                </button>
                <button class="btn" type="button" data-testid="reopen-completed" @click="reopen(detail.id)">
                  重开已完成的结论核对
                </button>
              </div>
            </template>

            <!-- 未受理：继续确认 -->
            <template v-else>
              <p class="pending-notice" data-testid="conflict-notice">
                {{ detail.detail_notice || detail['冲突提示'] || '复验单尚未受理：请按下列读数确认本次参照。' }}
              </p>

              <div
                v-if="pendingReadings.length"
                class="pending-list"
                data-testid="pending-readings"
              >
                <p class="rc-muted">仍需确认的读数（页面所列）：</p>
                <div
                  v-for="reading in pendingReadings"
                  :key="`p-${reading.id}`"
                  class="pending-item"
                  :data-testid="`pending-reading-${reading.id}`"
                >
                  <label>
                    <input type="radio" name="pending-reading" :value="reading.id" v-model="selectedReadingId" />
                    {{ reading['钟点'] }} ｜ <strong>{{ reading['读数'] }}</strong>
                    ｜ {{ reading['point_id'] }}
                  </label>
                  <button
                    class="link"
                    type="button"
                    :data-testid="`pending-pick-${reading.id}`"
                    @click="finishWithReading(reading.id)"
                  >
                    采用此读数并完成
                  </button>
                </div>
                <button
                  class="btn primary"
                  type="button"
                  data-testid="accept-recheck"
                  :disabled="selectedReadingId == null || busy"
                  @click="finishWithSelected"
                >
                  按所选读数继续办理并判定
                </button>
              </div>

              <div v-else class="no-evidence-confirm">
                <p data-testid="no-evidence-pending">
                  当前没有合格读数。可确认并留存「无证据」结论，之后仍可在详情页回看；
                  系统不会以 0 补出一条触发记录。
                </p>
                <button
                  class="btn primary"
                  type="button"
                  data-testid="confirm-no-evidence"
                  :disabled="busy"
                  @click="confirmNoEvidence"
                >
                  确认无证据
                </button>
              </div>
            </template>
          </template>
        </div>

        <!-- 触发记录：证明无 0 补录 -->
        <div class="rc-card" data-testid="trigger-records">
          <h4>联动触发记录</h4>
          <p v-if="!records.length" class="rc-muted">
            暂无触发记录：未触发或无证据都不会生成记录，更不会补一条触发值为 0 的记录。
          </p>
          <ul v-else class="record-list">
            <li v-for="record in records" :key="String(record.id)" :data-testid="`trigger-record-${record.id}`">
              {{ record['触发记录编号'] }} ｜ {{ record['关联测点'] }} ｜
              触发值 <strong>{{ record['触发值'] }}</strong> ｜ {{ record['阈值条件'] }}
              ｜ 证据#{{ record['证据读数编号'] }} ｜ {{ record['触发时间'] }}
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 结论单列表：原详情入口 + 重开 -->
    <div class="rc-card">
      <h4>复验结论单（一次复验 = 结论 + 读数 + 时间）</h4>
      <table class="data-table" data-testid="recheck-orders">
        <thead>
          <tr>
            <th>单号</th><th>事件</th><th>测点</th><th>条件</th>
            <th>结论</th><th>证据读数/时刻</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in orders" :key="String(order.id)" :data-testid="`recheck-row-${order.id}`">
            <td>{{ order['结论单编号'] }}</td>
            <td>{{ order['事件编号'] }}</td>
            <td>{{ order['关联测点'] }}</td>
            <td>{{ order['告警条件'] }}</td>
            <td :data-testid="`order-conclusion-${order.id}`">{{ order['结论'] || '—' }}</td>
            <td>
              <template v-if="order.evidence">
                {{ order.evidence['读数'] }} @ {{ order.evidence['钟点'] }}
              </template>
              <template v-else>—</template>
            </td>
            <td>{{ order['status'] || order['状态'] }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`open-recheck-${order.id}`" @click="openOrder(order.id)">
                查看原详情
              </button>
              <button class="link" type="button" :data-testid="`reopen-${order.id}`" @click="reopen(order.id)">
                重开
              </button>
            </td>
          </tr>
          <tr v-if="!orders.length">
            <td colspan="8" class="empty-state">暂无复验结论单</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  confirmNoEvidence as apiNoEvidence,
  confirmReference,
  fetchRecheckDetail,
  fetchRecheckList,
  fetchScenario,
  fetchTriggerRecords,
  ingestReading,
  previewRecheck,
  queryReadings,
  reopenRecheck,
  resetScenario as apiReset,
  seedLate85,
  submitRecheck,
  type ReadingSnapshot,
  type RecheckOrder,
} from '@/api/recheck'

const props = defineProps<{ initialRecheckId?: string }>()
const router = useRouter()

function syncDetailUrl(id: number | string) {
  // 不触发重新挂载；仅把当前原详情单固化到可回看的深链上。
  router
    .replace({ name: 'alarm-recheck-detail', params: { recheckId: String(id) } })
    .catch(() => {})
}

const incidents = ref<Array<Record<string, unknown>>>([])
const orders = ref<RecheckOrder[]>([])
const records = ref<Array<Record<string, unknown>>>([])
const currentCode = ref<string>('EVT-0805')
const qualified = ref<ReadingSnapshot[]>([])
const excluded = ref<ReadingSnapshot[]>([])
const selectedReadingId = ref<number | string | undefined>(undefined)
// 值班员是否手动挑过参照；未挑过时，迟到的最新合格读数自动成为首选。
const userTouchedReading = ref(false)
const conditionText = ref('>80')
const operator = ref('>')
const threshold = ref('80')
const detail = ref<RecheckOrder | null>(null)
const loading = ref(false)
const busy = ref(false)
const banner = ref('')
const errorMessage = ref('')
const lateTime = ref('08:04')
const lateValue = ref('85')

const currentIncident = computed(() =>
  incidents.value.find((item) => item['事件编号'] === currentCode.value),
)

const pendingReadings = computed<ReadingSnapshot[]>(() => {
  if (!detail.value) return []
  const list = (detail.value.pending_readings
    || detail.value['待确认读数']
    || []) as ReadingSnapshot[]
  return list
})

const conclusionClass = computed(() => {
  const c = detail.value?.['结论']
  if (c === '触发') return 'bad'
  if (c === '未触发') return 'ok'
  return 'muted'
})

function flash(message: string) {
  banner.value = message
  window.setTimeout(() => {
    if (banner.value === message) banner.value = ''
  }, 6000)
}

function syncCondition() {
  conditionText.value = `${operator.value}${threshold.value}`
}

function onConditionTextInput() {
  const text = conditionText.value.replace(/\s/g, '')
  const match = text.match(/^(>=|<=|==|>|<)?(-?\d+(?:\.\d+)?)?$/)
  if (match) {
    if (match[1]) operator.value = match[1]
    if (match[2] != null) threshold.value = match[2]
  }
}

function applyPreview(order: RecheckOrder) {
  detail.value = order
  qualified.value = (order.qualified_readings || []) as ReadingSnapshot[]
  excluded.value = (order.excluded_readings || []) as ReadingSnapshot[]
  const confirmed = order.confirmed_reading as ReadingSnapshot | null
  if (confirmed?.id != null) {
    // 已显式确认：冻结，迟到证据不改变所选参照。
    selectedReadingId.value = confirmed.id
  } else if (userTouchedReading.value) {
    // 值班员手动挑过：保留其选择，除非该读数已不在合格列表。
    const stillExists = qualified.value.some((r) => r.id === selectedReadingId.value)
    if (!stillExists) selectedReadingId.value = qualified.value[0]?.id
  } else {
    // 尚未确认：默认采用最新（最接近事发时刻）的合格读数；
    // 迟到的 08:04/85 会自然顶替预览时的 08:03/72。
    selectedReadingId.value = qualified.value[0]?.id
  }
}

async function doPreview() {
  loading.value = true
  errorMessage.value = ''
  try {
    const order = await previewRecheck({ incident_id: currentCode.value })
    applyPreview(order)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '参照查询失败'
  } finally {
    loading.value = false
  }
}

async function refreshLists() {
  try {
    const [list, recs] = await Promise.all([fetchRecheckList(), fetchTriggerRecords()])
    orders.value = list.items || []
    records.value = recs.items || []
  } catch {
    // 列表刷新失败不打断主流程
  }
}

async function addLate85() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await seedLate85()
    flash(result.message || '迟到读数 85 已入库')
    await afterDataChange()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迟到读数注入失败'
  } finally {
    busy.value = false
  }
}

async function addLateCustom() {
  busy.value = true
  try {
    await ingestReading({
      incident_id: currentCode.value,
      point_id: currentIncident.value?.['关联测点'],
      device_id: currentIncident.value?.['设备编号'],
      at: lateTime.value,
      value: lateValue.value,
    })
    flash(`读数 ${lateTime.value}=${lateValue.value} 已作为迟到证据接收`)
    await afterDataChange()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读数上报失败'
  } finally {
    busy.value = false
  }
}

async function afterDataChange() {
  await doPreview()
  await refreshLists()
  if (detail.value?.id != null) {
    try {
      detail.value = await fetchRecheckDetail(detail.value.id)
    } catch {
      /* ignore */
    }
  }
}

async function selectIncident(code: string) {
  currentCode.value = code
  detail.value = null
  selectedReadingId.value = undefined
  userTouchedReading.value = false
  conditionText.value = '>80'
  operator.value = '>'
  threshold.value = '80'
  await doPreview()
}

/** 提交前静默重查一次：若出现迟到合格读数且值班员未手动锁定选择，
 *  让页面所列的最新参照参与本次提交。 */
async function refreshSelectionBeforeSubmit() {
  if (userTouchedReading.value || detail.value?.confirmed_reading) return
  try {
    const q = await queryReadings({ incident_id: currentCode.value })
    qualified.value = q.qualified_readings || []
    excluded.value = q.excluded_readings || []
    selectedReadingId.value = qualified.value[0]?.id
  } catch {
    /* 静默重查失败时沿用页面当前所列读数 */
  }
}

async function confirmSelectedReference() {
  if (detail.value?.id == null || selectedReadingId.value == null) return
  busy.value = true
  userTouchedReading.value = true
  try {
    detail.value = await confirmReference(detail.value.id, selectedReadingId.value)
    flash('参照已确认并冻结；此后迟到证据不会改变本次采用依据。')
    await refreshLists()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认参照失败'
  } finally {
    busy.value = false
  }
}

async function submit() {
  busy.value = true
  errorMessage.value = ''
  try {
    if (detail.value?.status !== '已完成') {
      await refreshSelectionBeforeSubmit()
    }
    const payload: Record<string, unknown> = {
      incident_id: currentCode.value,
      condition: conditionText.value,
      operator: operator.value,
      threshold: threshold.value,
    }
    if (detail.value?.id != null) payload.recheck_id = detail.value.id
    if (selectedReadingId.value != null) payload.reading_id = selectedReadingId.value
    const result = await submitRecheck(payload)
    detail.value = result
    qualified.value = (result.qualified_readings || qualified.value) as ReadingSnapshot[]
    if (result.accepted === false) {
      const pending = (result.pending_readings || []) as ReadingSnapshot[]
      if (pending.length) selectedReadingId.value = pending[0].id
      flash(result.message || '复验单尚未受理：请按页面所列读数继续确认。')
    } else {
      flash(result.message || `复验完成：${result['结论']}`)
    }
    if (result.id != null) syncDetailUrl(result.id)
    await refreshLists()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交复验失败'
  } finally {
    busy.value = false
  }
}

async function finishWithReading(readingId: number | string | undefined) {
  if (readingId == null) return
  selectedReadingId.value = readingId
  await finishWithSelected()
}

async function finishWithSelected() {
  if (detail.value?.id == null || selectedReadingId.value == null) return
  busy.value = true
  try {
    const result = await submitRecheck({
      recheck_id: detail.value.id,
      reading_id: selectedReadingId.value,
      condition: conditionText.value,
    })
    detail.value = result
    flash(result.message || `复验完成：${result['结论']}`)
    if (result.id != null) syncDetailUrl(result.id)
    await refreshLists()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '办理失败'
  } finally {
    busy.value = false
  }
}

async function confirmNoEvidence() {
  if (detail.value?.id == null) return
  busy.value = true
  try {
    detail.value = await apiNoEvidence(detail.value.id)
    flash('已留存无证据结论，可随时重开回看；未生成触发记录。')
    if (detail.value?.id != null) syncDetailUrl(detail.value.id)
    await refreshLists()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无证据确认失败'
  } finally {
    busy.value = false
  }
}

async function openOrder(id: number | string) {
  try {
    const data = await fetchRecheckDetail(id)
    if (data && data.ok === false) {
      // 深链指向的单据尚不存在：回到当前事件的预览办理态。
      detail.value = null
      await doPreview()
      flash('该结论单尚不存在，已为你打开当前事件的复验预览。')
      return
    }
    detail.value = data
    if (detail.value['事件编号']) currentCode.value = String(detail.value['事件编号'])
    const ev = detail.value.evidence as ReadingSnapshot | null
    if (ev?.id != null) selectedReadingId.value = ev.id
    syncDetailUrl(id)
    document.getElementById('alarm-recheck-detail-anchor')?.scrollIntoView({ behavior: 'smooth' })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打开详情失败'
  }
}

async function reopen(id: number | string) {
  busy.value = true
  try {
    detail.value = await reopenRecheck(id)
    if (detail.value['事件编号']) currentCode.value = String(detail.value['事件编号'])
    syncDetailUrl(id)
    flash(detail.value.message || '已重开核对当次结论。')
    await refreshLists()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重开失败'
  } finally {
    busy.value = false
  }
}

async function requeryDetail() {
  if (detail.value?.id == null) return
  try {
    const [fresh] = await Promise.all([fetchRecheckDetail(detail.value.id), doPreview()])
    detail.value = fresh
    flash('已按最新数据重新查询：当次已确认结论保持不变。')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新查询失败'
  }
}

async function resetDemo() {
  await apiReset()
  detail.value = null
  selectedReadingId.value = undefined
  userTouchedReading.value = false
  await init()
  flash('演示场景已重置为：08:05 事发，预览 72 / 100 / 99。')
}

/** 供告警规则行的「复验」按钮调用：定位到对应事件。 */
async function startForRule(row: Record<string, unknown>) {
  const ruleNo = row['规则编号'] ? String(row['规则编号']) : ''
  const point = row['关联测点'] ? String(row['关联测点']) : ''
  const match = incidents.value.find(
    (item) => item['告警规则编号'] === ruleNo || item['关联测点'] === point,
  )
  document.getElementById('alarm-recheck-panel')?.scrollIntoView({ behavior: 'smooth' })
  if (match) {
    await selectIncident(String(match['事件编号']))
    flash(`已为规则 ${ruleNo} 打开发事发复验，请按事发时刻查询并确认参照。`)
  } else {
    flash(`规则 ${ruleNo} 的测点 ${point} 暂无预置事件，可直接用下方查询/提交接口按该测点办理。`)
  }
}

defineExpose({ startForRule })

async function init() {
  try {
    const scenario = await fetchScenario()
    incidents.value = scenario.incidents || []
    orders.value = scenario.orders || []
    records.value = scenario.trigger_records || []
    if (props.initialRecheckId) {
      await openOrder(props.initialRecheckId)
    } else {
      await doPreview()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复验场景初始化失败'
  }
}

onMounted(init)
</script>

<style scoped>
.recheck-panel {
  background: #fbfdff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin: 14px 0 18px;
}
.rc-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.rc-head h3 { margin: 0 0 6px; font-size: 17px; }
.rc-tip { font-size: 12.5px; color: #475569; line-height: 1.7; margin: 0; max-width: 900px; }
.incident-tabs { display: flex; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
.incident-card {
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 8px 12px; cursor: pointer;
}
.incident-card.active { border-color: var(--brand); box-shadow: 0 0 0 2px rgb(31 111 235 / 15%); }
.inc-code { font-weight: 700; font-size: 13px; }
.inc-meta { font-size: 12px; color: var(--muted); }
.rc-banner { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 12px; border-radius: 8px; font-size: 13px; }
.rc-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr); gap: 14px; }
@media (max-width: 1100px) { .rc-grid { grid-template-columns: 1fr; } }
.rc-col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.rc-card { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.rc-card h4 { margin: 0 0 10px; font-size: 14.5px; }
.rc-card h5 { margin: 12px 0 6px; font-size: 13px; color: #334155; }
.rc-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.rc-context { font-size: 12.5px; color: #475569; margin: 4px 0 10px; }
.rc-table { font-size: 12.5px; }
.excluded-row { color: #94a3b8; background: #fafafa; }
.exclude-reason { color: #b45309; }
.no-evidence-hint {
  border: 1px dashed #f0a; border-color: #f0c36d; background: #fffaeb; color: #b54708;
  padding: 10px 12px; border-radius: 8px; font-size: 13px;
}
.late-box {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  background: #f8fafc; border: 1px dashed var(--border); border-radius: 8px; padding: 10px; margin-bottom: 6px;
}
.late-custom { display: inline-flex; gap: 8px; align-items: flex-end; font-size: 12px; }
.late-custom label { display: flex; flex-direction: column; gap: 2px; color: var(--muted); }
.late-custom input, .condition-row input, .condition-row select {
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px;
}
.condition-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.condition-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.cond-text { flex: 1; min-width: 200px; }
.cond-text input { width: 100%; }
.submit-row { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.rc-muted { font-size: 12px; color: var(--muted); line-height: 1.7; }
.detail-card { border-color: #bcd3ff; box-shadow: 0 6px 18px rgb(31 111 235 / 8%); }
.order-code { font-family: monospace; font-size: 12px; color: var(--muted); }
.badges { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.status-badge { background: #eef2ff; color: #3538cd; border-radius: 999px; padding: 3px 10px; font-size: 12px; }
.conclusion-badge { border-radius: 999px; padding: 3px 12px; font-size: 13px; font-weight: 700; }
.conclusion-badge.bad { background: #fee4e2; color: #b42318; }
.conclusion-badge.ok { background: #dcfae6; color: #067647; }
.conclusion-badge.muted { background: #f1f5f9; color: #475569; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; margin: 0 0 8px; }
.detail-grid dt { font-size: 11.5px; color: var(--muted); }
.detail-grid dd { margin: 2px 0 0; font-size: 13.5px; }
.rationale { background: #f8fafc; border-left: 3px solid var(--brand); padding: 8px 10px; font-size: 13px; border-radius: 4px; }
.basis { background: #ecfdf3; border-left: 3px solid #12b76a; padding: 8px 10px; font-size: 13px; border-radius: 4px; }
.conflict-notice, .pending-notice {
  background: #fffaeb; border-left: 3px solid #f79009; padding: 8px 10px;
  font-size: 12.5px; color: #b54708; border-radius: 4px; line-height: 1.7;
}
.immutable-hint { font-size: 12px; color: var(--muted); }
.consistency { font-size: 12.5px; }
.ok-text { color: #067647; }
.bad-text { color: #b42318; }
.pending-list { display: flex; flex-direction: column; gap: 6px; margin: 8px 0; }
.pending-item { display: flex; justify-content: space-between; gap: 8px; align-items: center; background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
.no-evidence-confirm { background: #f8fafc; border-radius: 8px; padding: 10px; }
.record-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; }
</style>
