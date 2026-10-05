<template>
  <section class="page" data-module="certificate">
    <header class="page-head">
      <div>
        <h2>持证管理管理</h2>
        <p class="page-desc">维护持证人员，按到期日期自动判定持证状态，支持证书类别加到期月份组合查询。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记持证人员</button>
        <button class="btn" type="button" @click="exportRows">导出持证管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>人员编号</span>
        <input v-model="filters.keyword" placeholder="按人员编号检索" />
      </label>
      <label class="filter-item">
        <span>证书类别</span>
        <input v-model="filters.category" placeholder="按证书类别检索" />
      </label>
      <label class="filter-item">
        <span>到期月份</span>
        <input v-model="filters.expiry_month" type="month" />
      </label>
      <label class="filter-item">
        <span>证书状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无持证管理数据，可先登记持证人员</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条持证管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/certificate'
const columns = ["人员编号", "姓名", "证书类别", "证书编号", "发证日期", "到期日期", "到期天数", "复训记录", "证书状态"]
const actions = ["安排复训", "补录复训记录", "注销证书"]
const statuses = ["持证有效", "即将到期", "已过期", "已注销"]

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([{ label: '持证人员', value: 0 }, { label: '即将到期', value: 0 }, { label: '已过期', value: 0 }])
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', category: '', expiry_month: '', status: '' })

function activeFilters() {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params.set(key, value)
  }
  return params.toString()
}

function resetFilters() {
  filters.value = { keyword: '', category: '', expiry_month: '', status: '' }
  void reload()
}

function exportRows() {
  const query = activeFilters()
  window.open(query ? `${ENDPOINT}/export?${query}` : `${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '持证人员登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '补录复训记录') {
    const retrainedOn = window.prompt(`为 ${row['姓名'] ?? row['人员编号']} 补录复训记录，请输入复训日期（YYYY-MM-DD）`)
    if (!retrainedOn) return
    values['复训日期'] = retrainedOn
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '持证管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '持证管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const [listResponse, statsResponse] = await Promise.all([
      request(`${ENDPOINT}?${activeFilters()}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listResponse.ok) {
      throw new Error('持证人员列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (statsResponse.ok) {
      const counts = await statsResponse.json()
      const sum = statuses.reduce((acc, status) => acc + (counts[status] ?? 0), 0)
      stats.value = [
        { label: '持证人员', value: sum },
        { label: '即将到期', value: counts['即将到期'] ?? 0 },
        { label: '已过期', value: counts['已过期'] ?? 0 },
      ]
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '持证管理列表读取失败'
  }
}

onMounted(reload)
</script>
