<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

const status = ref<string>('idle')
const taskId = ref<number | null>(null)
const isLoading = ref(false)

const formatDate = (val: any) => {
  if (!val) return '-'
  // 强制转换为数字，如果是时间戳字符串也能处理
  const num = Number(val)
  const d = new Date(num > 1e12 ? num : num * 1000)
  
  if (isNaN(d.getTime())) return val
  
  return d.toLocaleDateString('zh-CN', { 
    year: 'numeric', 
    month: '2-digit', 
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).replace(/\//g, '-')
}

let pollInterval: ReturnType<typeof setInterval> | null = null

const stats = ref<any>({
  remote_total: '-',
  local_total: '-',
  embedded_total: '-',
  md_converted: '-',
  mp_count: '-',
  api_status: '-'
})

// --- 研报列表相关 ---
interface Article {
  article_id: string
  mp_name: string
  title: string
  source_url: string
  published_at: string
  embedded: number
}

const articles = ref<Article[]>([])
const totalArticles = ref(0)
const articlesLoading = ref(true)
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = ref(15)
const embeddedFilter = ref<string>('all') // 'all', '1', '0'
const selectedArticle = ref<any>(null)
const showDetail = ref(false)

const fetchArticles = async () => {
  articlesLoading.value = true
  try {
    const skip = (currentPage.value - 1) * pageSize.value
    let url = `${API_BASE}/articles?limit=${pageSize.value}&skip=${skip}`
    if (searchQuery.value) url += `&search=${encodeURIComponent(searchQuery.value)}`
    if (embeddedFilter.value !== 'all') url += `&embedded=${embeddedFilter.value}`
    
    const res = await axios.get(url)
    articles.value = res.data.items
    totalArticles.value = res.data.total
  } catch (e) {
    console.error(e)
  } finally {
    articlesLoading.value = false
  }
}

watch(embeddedFilter, () => {
  currentPage.value = 1
  fetchArticles()
})

const handleSearch = () => {
  currentPage.value = 1
  fetchArticles()
}

const changePage = (page: number) => {
  currentPage.value = page
  fetchArticles()
}

const viewArticle = async (id: string) => {
  try {
    const res = await axios.get(`${API_BASE}/articles/${id}`)
    selectedArticle.value = res.data
    showDetail.value = true
  } catch (e) {
    console.error('Failed to load article details', e)
    alert('获取文章详情失败')
  }
}

const fetchStats = async () => {
  try {
    const res = await axios.get(`${API_BASE}/articles/stats`)
    stats.value = res.data
  } catch(e) {
    console.error(e)
  }
}

const fetchStatus = async () => {
  try {
    const res = await axios.get(`${API_BASE}/ingest/status`)
    if (res.data) {
      status.value = res.data.status
      taskId.value = res.data.task_id
      
      if (status.value === 'running' || status.value === 'pending') {
        if (!pollInterval) {
          pollInterval = setInterval(fetchStatus, 3000)
        }
      } else {
        if (pollInterval) {
          clearInterval(pollInterval)
          pollInterval = null
        }
        fetchStats()
      }
    }
  } catch (e) {
    console.error('Error fetching status', e)
  }
}

const startIngest = async () => {
  if (status.value === 'running' || status.value === 'pending') return
  
  isLoading.value = true
  try {
    await axios.post(`${API_BASE}/ingest/start`, { limit: 0, force: false, skip_ddb: true })
    await fetchStatus()
  } catch (e: any) {
    alert('Failed to start ingest: ' + (e.response?.data?.detail || e.message))
  } finally {
    isLoading.value = false
  }
}

const startVectorize = async () => {
  if (status.value === 'running' || status.value === 'pending') return
  
  isLoading.value = true
  try {
    await axios.post(`${API_BASE}/ingest/start_vectorize`)
    await fetchStatus()
  } catch (e: any) {
    alert('Failed to start vectorize: ' + (e.response?.data?.detail || e.message))
  } finally {
    isLoading.value = false
  }
}

import { useRouter } from 'vue-router'

const router = useRouter()
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

const triggerUpload = () => {
  fileInput.value?.click()
}

const handleFileUpload = async (event: Event) => {
  const target = event.target as HTMLInputElement
  if (!target.files?.length) return
  
  const file = target.files[0]
  const formData = new FormData()
  formData.append('file', file)
  
  uploading.value = true
  try {
    const res = await axios.post(`${API_BASE}/ingest/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    alert(`文件 "${file.name}" 上传并向量化成功！\nID: ${res.data.article_id}`)
    fetchStats()
    fetchArticles()
  } catch (e: any) {
    alert('上传失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

const chatWithArticle = (article: Article) => {
  if (!article.embedded) return
  router.push({
    path: '/knowledge',
    query: { 
      article_id: article.article_id,
      title: article.title
    }
  })
}

onMounted(() => {
  fetchStatus()
  fetchStats()
  fetchArticles()
})


onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})
</script>

<template>
  <div class="animate-in fade-in duration-500 h-[calc(100vh-100px)] overflow-y-auto">
    <div class="mb-4">
      <p class="text-slate-500 font-medium text-sm">统一管理所有微信研报的数据拉取、入库与向量化任务。</p>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">源头文章数</div>
        <div class="text-2xl font-bold text-slate-900">{{ stats.remote_total }}</div>
      </div>
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">本地文章库</div>
        <div class="text-2xl font-bold text-slate-900">{{ stats.local_total }}</div>
      </div>
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">已向量文章</div>
        <div class="text-2xl font-bold text-slate-900">{{ stats.embedded_total }}</div>
      </div>
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">Markdown 转换</div>
        <div class="text-2xl font-bold text-slate-900">{{ stats.md_converted }}</div>
      </div>
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">覆盖公众号</div>
        <div class="text-2xl font-bold text-slate-900">{{ stats.mp_count }}</div>
      </div>
      <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 text-center">
        <div class="text-xs text-slate-500 mb-1">源服务状态</div>
        <div class="text-xl font-bold mt-1" :class="stats.api_status === '正常' ? 'text-green-600' : 'text-red-600'">
          {{ stats.api_status }}
        </div>
      </div>
    </div>

    <!-- Ingest Pipeline -->
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
      <div class="border-b border-slate-100 bg-slate-50 px-6 py-4 flex items-center justify-between">
        <h2 class="text-base font-bold text-slate-800">全量同步任务 (Ingest Pipeline)</h2>
        
        <div class="flex items-center gap-3">
          <div class="flex items-center gap-2 text-sm font-semibold mr-4">
            状态:
            <span v-if="status === 'idle'" class="px-2 py-1 bg-slate-100 text-slate-600 rounded">空闲中</span>
            <span v-else-if="status === 'pending'" class="px-2 py-1 bg-amber-100 text-amber-700 rounded flex items-center gap-2">
              <div class="w-2 h-2 bg-amber-500 rounded-full animate-pulse"></div>
              排队中
            </span>
            <span v-else-if="status === 'running'" class="px-2 py-1 bg-blue-100 text-blue-700 rounded flex items-center gap-2">
              <div class="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
              执行中
            </span>
            <span v-else-if="status === 'completed'" class="px-2 py-1 bg-green-100 text-green-700 rounded">已完成</span>
            <span v-else class="px-2 py-1 bg-red-100 text-red-700 rounded">{{ status }}</span>
          </div>

          <div class="flex gap-2">
            <!-- Hidden File Input -->
            <input 
              type="file" 
              ref="fileInput" 
              class="hidden" 
              accept=".pdf,.md,.txt" 
              @change="handleFileUpload"
            >
            <button 
              @click="triggerUpload"
              :disabled="uploading"
              class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-bold rounded-lg transition-all shadow-sm active:scale-95 text-sm flex items-center gap-2"
            >
              <span v-if="uploading" class="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></span>
              {{ uploading ? '上传中...' : '📂 上传本地研报' }}
            </button>
            <button 
              @click="startIngest"
              :disabled="status === 'running' || status === 'pending' || isLoading"
              class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-bold rounded-lg transition-colors shadow-sm active:scale-95 text-sm"
            >
              {{ isLoading ? '提交中...' : '▶ 同步至本地库' }}
            </button>
            <button 
              @click="startVectorize"
              :disabled="status === 'running' || status === 'pending' || isLoading"
              class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-bold rounded-lg transition-colors shadow-sm active:scale-95 text-sm"
            >
              {{ isLoading ? '提交中...' : '▶ 向量化处理' }}
            </button>
          </div>

        </div>
      </div>
      
      <div class="px-6 py-4">
        <div class="flex items-center gap-2 text-sm font-medium text-slate-600">
          <div class="px-3 py-1.5 bg-slate-100 rounded-lg border border-slate-200 text-xs">1. we-mp-rss API</div>
          <div class="text-slate-400">➔</div>
          <div class="px-3 py-1.5 bg-slate-100 rounded-lg border border-slate-200 text-xs">2. 提取清洗 Markdown</div>
          <div class="text-slate-400">➔</div>
          <div class="px-3 py-1.5 bg-slate-100 rounded-lg border border-slate-200 text-xs">3. 图片→腾讯云COS</div>
          <div class="text-slate-400">➔</div>
          <div class="px-3 py-1.5 bg-slate-100 rounded-lg border border-slate-200 text-xs">4. 存入 SQLite</div>
          <div class="text-slate-400">➔</div>
          <div class="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg border border-blue-200 font-bold text-xs">5. DolphinDB 向量化</div>
        </div>
        
        <div class="mt-4 bg-slate-900 rounded-xl p-4 font-mono text-xs text-green-400 h-40 overflow-y-auto" v-if="status === 'running' || status === 'pending'">
          <div>[System] 后台守护进程已就绪，实时监控任务状态...</div>
          <div v-if="status === 'pending'" class="text-yellow-400 mt-2">[Queue] 任务已加入数据库队列，等待 Worker 接管 (Task ID: {{ taskId }})...</div>
          <div v-if="status === 'running'" class="mt-2">[Worker] 正在执行抓取与清洗任务，由于后台服务不直接推送日志到前端，请在后端终端查看详细进度...</div>
        </div>
      </div>
    </div>


    <!-- 研报列表 -->
    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
      <div class="px-6 py-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
        <h2 class="text-base font-bold text-slate-800">📄 研报列表</h2>
        <div class="flex gap-3 items-center">
          <input 
            v-model="searchQuery" 
            @keyup.enter="handleSearch"
            placeholder="搜索研报标题或公众号..." 
            class="px-4 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-medium text-sm w-72"
          >
          <button @click="handleSearch" class="px-5 py-2 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-700 transition-colors text-sm">搜索</button>
        </div>
      </div>
      
      <div class="overflow-auto max-h-[500px]">
        <div v-if="articlesLoading" class="p-10 text-center text-slate-400 font-medium">加载中...</div>
        <div v-else-if="articles.length === 0" class="p-10 text-center text-slate-400 font-medium">暂无数据</div>
        <table v-else class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-slate-50 text-slate-500 text-sm border-b border-slate-200 sticky top-0">
              <th class="py-3 px-6 font-semibold">公众号</th>
              <th class="py-3 px-6 font-semibold w-1/2">标题</th>
              <th class="py-3 px-6 font-semibold">发布时间</th>
              <th class="py-3 px-6 font-semibold text-center">
                <div class="flex items-center justify-center gap-1">
                  <span>向量化</span>
                  <select v-model="embeddedFilter" class="bg-transparent border-none text-[10px] font-bold text-slate-400 focus:ring-0 cursor-pointer p-0 outline-none">
                    <option value="all">全部</option>
                    <option value="1">已完成</option>
                    <option value="0">待处理</option>
                  </select>
                </div>
              </th>
              <th class="py-3 px-6 font-semibold text-center">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in articles" :key="item.article_id" class="border-b border-slate-100 hover:bg-slate-50/80 transition-colors">
              <td class="py-2.5 px-6 font-medium text-slate-700 text-sm">{{ item.mp_name }}</td>
              <td class="py-2.5 px-6 font-bold text-slate-900 text-sm cursor-pointer hover:text-blue-600 transition-colors" @click="viewArticle(item.article_id)" :title="item.title">
                {{ item.title.length > 50 ? item.title.slice(0, 50) + '...' : item.title }}
              </td>
              <td class="py-2.5 px-6 text-slate-500 font-mono text-xs">{{ formatDate(item.published_at) }}</td>
              <td class="py-2.5 px-6 text-center">
                <span 
                  class="px-2 py-0.5 rounded-full text-[10px] font-black uppercase"
                  :class="item.embedded ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-400'"
                >
                  {{ item.embedded ? '已完成' : '待处理' }}
                </span>
              </td>
              <td class="py-2.5 px-6 text-center space-x-3">
                <button 
                  @click="chatWithArticle(item)"
                  :disabled="!item.embedded"
                  class="font-bold text-xs inline-flex items-center gap-1 transition-colors"
                  :class="item.embedded ? 'text-emerald-600 hover:text-emerald-800' : 'text-slate-300 cursor-not-allowed'"
                  :title="item.embedded ? '开启针对性问答' : '请先完成向量化处理再进行问答'"
                >
                  💡 问答
                </button>
                <a :href="item.source_url" target="_blank" class="text-blue-600 hover:text-blue-800 font-bold text-xs inline-flex items-center gap-1">
                  原文 ↗
                </a>
              </td>

            </tr>
          </tbody>
        </table>
      </div>
      
      <!-- Pagination -->
      <div class="px-6 py-3 border-t border-slate-100 bg-slate-50 flex items-center justify-between" v-if="totalArticles > 0">
        <div class="text-sm text-slate-500 font-medium">
          共 <span class="font-bold text-slate-900">{{ totalArticles }}</span> 篇研报
        </div>
        <div class="flex items-center gap-2">
          <button 
            @click="changePage(currentPage - 1)"
            :disabled="currentPage <= 1"
            class="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm font-bold text-slate-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-50 transition-colors"
          >
            上一页
          </button>
          <div class="text-sm text-slate-600 font-medium px-2">
            第 <span class="font-bold text-blue-600">{{ currentPage }}</span> / {{ Math.ceil(totalArticles / pageSize) }} 页
          </div>
          <button 
            @click="changePage(currentPage + 1)"
            :disabled="currentPage >= Math.ceil(totalArticles / pageSize)"
            class="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm font-bold text-slate-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-50 transition-colors"
          >
            下一页
          </button>
        </div>
      </div>
    </div>

    <!-- Article Detail Modal -->
    <div v-if="showDetail && selectedArticle" class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4 lg:p-8" @click.self="showDetail = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-[96%] max-h-[92vh] flex flex-col overflow-hidden">
        <div class="p-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
          <div class="flex-1 mr-4">
            <h2 class="text-lg font-bold text-slate-900 truncate">{{ selectedArticle.title }}</h2>
            <div class="text-xs text-slate-500 mt-1">
              📢 {{ selectedArticle.mp_name }} | 📅 {{ selectedArticle.published_at || '-' }} | 
              <a :href="selectedArticle.source_url" target="_blank" class="text-blue-600 hover:underline">查看原文 ↗</a>
            </div>
          </div>
          <button @click="showDetail = false" class="px-4 py-2 bg-slate-200 text-slate-700 font-bold rounded-lg hover:bg-slate-300 transition-colors text-sm">✕ 关闭</button>
        </div>
        
        <div class="flex-1 flex min-h-0 overflow-hidden bg-slate-200 gap-[1px]">
          <!-- Column 1: Markdown -->
          <div class="flex-1 flex flex-col min-w-0 bg-white">
            <div class="bg-slate-50 px-4 py-2 border-b border-slate-200 font-bold text-slate-700 text-[10px] uppercase tracking-wider shrink-0">
              📝 Markdown
            </div>
            <div class="flex-1 overflow-y-auto p-5 bg-white markdown-body custom-scrollbar">
              <div v-html="marked(selectedArticle.content_md || '无内容')"></div>
            </div>
          </div>
          
          <!-- Column 2: HTML -->
          <div class="flex-1 flex flex-col min-w-0 bg-white">
            <div class="bg-slate-50 px-4 py-2 border-b border-slate-200 font-bold text-slate-700 text-[10px] uppercase tracking-wider shrink-0">
              🌐 HTML 原文
            </div>
            <div class="flex-1 overflow-y-auto bg-slate-50 custom-scrollbar">
              <div class="bg-white p-8 shadow-sm" v-html="selectedArticle.content_html || '无内容'"></div>
            </div>
          </div>
          
          <!-- Column 3: Text -->
          <div class="flex-1 flex flex-col min-w-0 bg-white">
            <div class="bg-slate-50 px-4 py-2 border-b border-slate-200 font-bold text-slate-700 text-[10px] uppercase tracking-wider shrink-0">
              📄 纯文本
            </div>
            <div class="flex-1 overflow-y-auto p-5 whitespace-pre-wrap text-[13px] text-slate-600 leading-relaxed custom-scrollbar">
              {{ selectedArticle.content_clean || '无内容' }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 自定义悬浮滚动条 - 增强版 */
.custom-scrollbar {
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}

/* Chrome/Edge/Safari */
.custom-scrollbar::-webkit-scrollbar {
  width: 8px;
  background-color: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: transparent;
  border-radius: 20px;
  /* 增加边框实现内凹效果，让滚动条看起来更精致 */
  border: 2px solid transparent;
  background-clip: content-box;
}

.custom-scrollbar:hover::-webkit-scrollbar-thumb {
  background-color: rgba(100, 116, 139, 0.4); /* 使用 Slate-500 色调 */
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(100, 116, 139, 0.7) !important;
}

/* 隐藏横向滚动条（除非内容真的需要） */
.custom-scrollbar::-webkit-scrollbar-horizontal {
  height: 0px;
  display: none;
}
</style>
