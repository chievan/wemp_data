<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

const activeTab = ref('installed')
const installedSkills = ref<any[]>([])
const searchResults = ref<any[]>([])
const searchQuery = ref('')
const loading = ref(false)
const installing = ref<string | null>(null)

// Modal State
const showDetail = ref(false)
const detailLoading = ref(false)
const skillDetail = ref({ name: '', content: '' })

const fetchInstalled = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_BASE}/skills`)
    installedSkills.value = res.data
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const showSkillDetail = async (id: string, name: string) => {
  skillDetail.value = { name, content: '' }
  showDetail.value = true
  detailLoading.value = true
  try {
    const res = await axios.get(`${API_BASE}/skills/${id}/content`)
    skillDetail.value.content = await marked.parse(res.data.content || '暂无介绍')
  } catch (e) {
    skillDetail.value.content = '加载详情失败'
  } finally {
    detailLoading.value = false
  }
}

const uninstallSkill = async (id: string) => {
  if (!confirm(`确定要卸载技能 "${id}" 吗？`)) return
  try {
    await axios.delete(`${API_BASE}/skills/${id}`)
    await fetchInstalled()
  } catch (e) {
    alert('卸载失败')
  }
}

const searchMarket = async () => {
  if (!searchQuery.value.trim()) return
  loading.value = true
  try {
    const res = await axios.get(`${API_BASE}/skills/search`, { params: { q: searchQuery.value } })
    searchResults.value = res.data
  } catch (e) {
    alert('搜索失败，请检查 ClawHub 连接')
  } finally {
    loading.value = false
  }
}

const installSkill = async (slug: string) => {
  installing.value = slug
  try {
    await axios.post(`${API_BASE}/skills/install/${slug}`)
    alert(`${slug} 安装成功！`)
    await fetchInstalled()
  } catch (e) {
    alert('安装失败')
  } finally {
    installing.value = null
  }
}

onMounted(fetchInstalled)
</script>

<template>
  <div class="max-w-6xl mx-auto space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <p class="text-slate-500 font-bold text-sm">通过 ClawHub 生态扩展投研代理的专业领域能力</p>
      </div>
      <div class="flex bg-white p-1 rounded-xl shadow-sm border border-slate-200">
        <button 
          @click="activeTab = 'installed'"
          class="px-4 py-1.5 rounded-lg text-sm font-bold transition-all"
          :class="activeTab === 'installed' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'"
        >已安装</button>
        <button 
          @click="activeTab = 'market'"
          class="px-4 py-1.5 rounded-lg text-sm font-bold transition-all"
          :class="activeTab === 'market' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'"
        >大市场</button>
      </div>
    </div>

    <!-- Content Area -->
    <div v-if="activeTab === 'installed'" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <div v-if="loading && installedSkills.length === 0" class="col-span-full py-12 text-center text-slate-400">
        加载中...
      </div>
      
      <div 
        v-for="skill in installedSkills" 
        :key="skill.id"
        @click="showSkillDetail(skill.id, skill.name)"
        class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm hover:shadow-md transition-all group cursor-pointer hover:-translate-y-1 active:scale-95"
      >
        <div class="flex justify-between items-start mb-3">
          <div class="w-10 h-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center text-xl font-black shadow-inner group-hover:bg-blue-600 group-hover:text-white transition-colors">
            {{ skill.name.slice(0, 1) }}
          </div>
          <span class="text-[10px] font-black px-2 py-0.5 bg-slate-100 text-slate-500 rounded-full uppercase tracking-tighter">
            v{{ skill.version }}
          </span>
        </div>
        <h3 class="font-bold text-slate-900 mb-1">{{ skill.name }}</h3>
        <p class="text-xs text-slate-500 leading-relaxed mb-4 line-clamp-2 min-h-[2.5rem]">
          {{ skill.description }}
        </p>
        <div class="flex items-center justify-between pt-4 border-t border-slate-50">
          <span class="text-[10px] text-slate-400 font-mono">ID: {{ skill.id }}</span>
          <button 
            @click.stop="uninstallSkill(skill.id)"
            class="text-xs font-bold text-slate-400 hover:text-red-600 transition-colors"
          >🗑️ 卸载</button>
        </div>
      </div>

      <div v-if="!loading && installedSkills.length === 0" class="col-span-full py-20 text-center bg-white rounded-3xl border border-dashed border-slate-300">
        <div class="text-4xl mb-4">📦</div>
        <div class="text-slate-400 font-medium">尚未安装任何技能</div>
        <button @click="activeTab = 'market'" class="mt-4 text-blue-600 font-bold text-sm">前往大市场下载 ↗</button>
      </div>
    </div>

    <!-- Market Area -->
    <div v-if="activeTab === 'market'" class="space-y-6">
      <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex gap-3">
        <input 
          v-model="searchQuery"
          @keypress.enter="searchMarket"
          placeholder="输入关键词搜索技能（例如：finance, stock, bonds）..."
          class="flex-1 bg-slate-50 border-none rounded-xl px-4 py-2.5 focus:ring-2 focus:ring-blue-500/20 text-sm"
        />
        <button 
          @click="searchMarket"
          :disabled="loading"
          class="bg-blue-600 text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-blue-600/20 hover:bg-blue-700 disabled:opacity-50 transition-all"
        >
          {{ loading ? '搜索中...' : '🔍 搜索技能' }}
        </button>
      </div>

      <div class="grid grid-cols-1 gap-3">
        <div 
          v-for="item in searchResults" 
          :key="item.slug"
          class="bg-white p-4 rounded-xl border border-slate-100 flex items-center justify-between hover:border-blue-200 transition-colors"
        >
          <div class="min-w-0 flex-1 mr-4">
            <div class="font-bold text-slate-900">{{ item.slug }}</div>
            <div class="text-xs text-slate-500 truncate mt-0.5">{{ item.description }}</div>
          </div>
          <button 
            @click="installSkill(item.slug)"
            :disabled="installing === item.slug"
            class="px-4 py-1.5 rounded-lg bg-blue-50 text-blue-600 text-xs font-bold hover:bg-blue-600 hover:text-white transition-all disabled:opacity-50"
          >
            {{ installing === item.slug ? '🚀 安装中...' : '⬇️ 安装' }}
          </button>
        </div>
        
        <div v-if="!loading && searchResults.length === 0 && searchQuery" class="text-center py-10 text-slate-400 text-sm italic">
          未找到匹配的技能，请换个词试试
        </div>
      </div>
    </div>

    <!-- Detail Modal -->
    <div v-if="showDetail" class="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6">
      <div class="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" @click="showDetail = false"></div>
      <div class="relative bg-white w-full max-w-3xl max-h-[85vh] rounded-3xl shadow-2xl overflow-hidden flex flex-col animate-in fade-in zoom-in duration-300">
        <!-- Modal Header -->
        <div class="px-8 py-6 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 rounded-2xl bg-blue-600 text-white flex items-center justify-center text-2xl font-black shadow-lg shadow-blue-600/20">
              {{ skillDetail.name.slice(0, 1) }}
            </div>
            <div>
              <h2 class="text-xl font-bold text-slate-900">{{ skillDetail.name }}</h2>
              <p class="text-xs text-slate-500 font-medium">专家分身详细研究框架与风格设定</p>
            </div>
          </div>
          <button 
            @click="showDetail = false"
            class="w-10 h-10 rounded-full hover:bg-slate-200 flex items-center justify-center text-slate-400 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Modal Content -->
        <div class="flex-1 overflow-y-auto p-8 prose prose-slate prose-sm max-w-none custom-markdown">
          <div v-if="detailLoading" class="flex flex-col items-center justify-center py-20 gap-4">
            <div class="w-8 h-8 border-4 border-blue-600/20 border-t-blue-600 rounded-full animate-spin"></div>
            <p class="text-slate-400 font-medium animate-pulse">正在深度分析专家逻辑...</p>
          </div>
          <div v-else v-html="skillDetail.content"></div>
        </div>

        <!-- Modal Footer -->
        <div class="px-8 py-5 bg-slate-50 border-t border-slate-100 flex justify-end">
          <button 
            @click="showDetail = false"
            class="px-6 py-2 rounded-xl bg-slate-900 text-white font-bold text-sm hover:bg-slate-800 transition-all shadow-lg shadow-slate-900/10"
          >
            关闭详情
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-markdown :deep(h1), 
.custom-markdown :deep(h2), 
.custom-markdown :deep(h3) {
  @apply text-slate-900 font-bold border-b border-slate-100 pb-2 mb-4 mt-6;
}

.custom-markdown :deep(p) {
  @apply text-slate-600 leading-relaxed mb-4;
}

.custom-markdown :deep(ul) {
  @apply list-disc list-inside space-y-2 mb-4 text-slate-600;
}

.custom-markdown :deep(strong) {
  @apply text-slate-900 font-bold;
}

.custom-markdown :deep(table) {
  @apply w-full border-collapse mb-6 text-xs;
}

.custom-markdown :deep(th) {
  @apply bg-slate-50 text-slate-900 font-bold p-3 text-left border border-slate-200;
}

.custom-markdown :deep(td) {
  @apply p-3 border border-slate-100 text-slate-600;
}

.custom-markdown :deep(blockquote) {
  @apply border-l-4 border-blue-500 bg-blue-50/50 p-4 rounded-r-xl italic text-slate-700 mb-4;
}

/* 隐藏滚动条但保留功能 */
.overflow-y-auto {
  scrollbar-width: thin;
  scrollbar-color: #e2e8f0 transparent;
}
</style>

