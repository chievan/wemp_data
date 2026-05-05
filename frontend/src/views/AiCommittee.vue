<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

// Split message content into body and references
const splitContent = (content: string) => {
  const refPatterns = ['\n\n参考资料：', '\n\n参考资料:', '\n参考资料：', '\n参考资料:']
  for (const pat of refPatterns) {
    const idx = content.indexOf(pat)
    if (idx !== -1) {
      return {
        body: content.substring(0, idx).trim(),
        refs: content.substring(idx).trim()
      }
    }
  }
  return { body: content, refs: '' }
}

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

// --- 委员会预设 & 成员管理 ---
const committees = ref<Record<string, string[]>>({})
const selectedCommittee = ref('')
const currentMembers = computed(() => committees.value[selectedCommittee.value] || [])
const allMpNames = ref<string[]>([])
const showAddMember = ref(false)
const newMemberName = ref('')
const saving = ref(false)

// --- 历史会议 ---
const historySessions = ref<any[]>([])

// --- 讨论状态 ---
const currentInput = ref('')
const isDiscussing = ref(false)
const discussionMessages = ref<any[]>([])
const activeSessionId = ref<string>('')

// --- localStorage 持久化 ---
const STORAGE_KEY = 'wemp_committee_current'

const saveToLocal = () => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      messages: discussionMessages.value,
      sessionId: activeSessionId.value,
      committee: selectedCommittee.value
    }))
  } catch (e) {}
}

const restoreFromLocal = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const data = JSON.parse(raw)
      if (data.messages && data.messages.length > 0) {
        discussionMessages.value = data.messages
        activeSessionId.value = data.sessionId || ''
        if (data.committee) selectedCommittee.value = data.committee
      }
    }
  } catch (e) {}
}

const fetchPresets = async () => {
  try {
    const res = await axios.get(`${API_BASE}/committee/presets`)
    committees.value = res.data
    if (!selectedCommittee.value && Object.keys(res.data).length > 0) {
      selectedCommittee.value = Object.keys(res.data)[0]
    }
  } catch (e) {
    console.error('Failed to load presets', e)
  }
}

const fetchMpNames = async () => {
  try {
    const res = await axios.get(`${API_BASE}/committee/mp_names`)
    allMpNames.value = res.data
  } catch (e) {
    console.error('Failed to load mp_names', e)
  }
}

const fetchSessions = async () => {
  try {
    const res = await axios.get(`${API_BASE}/committee/sessions?limit=20`)
    historySessions.value = res.data
  } catch (e) {
    console.error('Failed to load sessions', e)
  }
}

const removeSession = async (sessionId: string, event: Event) => {
  event.stopPropagation()
  if (!confirm('确定要永久删除这次会议记录吗？')) return
  
  try {
    await axios.delete(`${API_BASE}/committee/sessions/${sessionId}`)
    await fetchSessions() // 重新加载列表
    if (activeSessionId.value === sessionId) {
      discussionMessages.value = []
      activeSessionId.value = ''
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch (e) {
    alert('删除失败')
  }
}

const availableMpNames = computed(() => {
  const members = currentMembers.value
  return allMpNames.value.filter(name => !members.includes(name))
})

const addMember = (name: string) => {
  if (!name.trim() || !selectedCommittee.value) return
  const members = committees.value[selectedCommittee.value] || []
  if (!members.includes(name.trim())) {
    members.push(name.trim())
    committees.value[selectedCommittee.value] = [...members]
  }
  newMemberName.value = ''
  showAddMember.value = false
}

const removeMember = (idx: number) => {
  if (!selectedCommittee.value) return
  const members = [...(committees.value[selectedCommittee.value] || [])]
  members.splice(idx, 1)
  committees.value[selectedCommittee.value] = members
}

const saveMembers = async () => {
  if (!selectedCommittee.value) return
  saving.value = true
  try {
    await axios.post(`${API_BASE}/committee/presets`, {
      committee_name: selectedCommittee.value,
      members: committees.value[selectedCommittee.value]
    })
  } catch (e: any) {
    alert('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

const startDiscussion = async () => {
  if (!currentInput.value.trim() || isDiscussing.value) return
  
  const topic = currentInput.value.trim()
  currentInput.value = ''
  activeSessionId.value = ''
  
  discussionMessages.value = [
    {
      role: 'user',
      name: 'CIO',
      content: `【发起议题】 ${topic}`
    }
  ]
  
  isDiscussing.value = true
  
  try {
    const response = await fetch(`${API_BASE}/committee/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: topic,
        committee_type: selectedCommittee.value,
        members: currentMembers.value
      })
    })
    
    if (!response.body) throw new Error('No response body')
    
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      
      // Process complete lines (NDJSON)
      const lines = buffer.split('\n')
      buffer = lines.pop() || '' // Keep incomplete line in buffer
      
      for (const line of lines) {
        if (!line.trim()) continue
        try {
          const msg = JSON.parse(line)
          discussionMessages.value.push(msg)
        } catch (e) {
          // skip malformed lines
        }
      }
    }
    
    // Process remaining buffer
    if (buffer.trim()) {
      try {
        const msg = JSON.parse(buffer)
        discussionMessages.value.push(msg)
      } catch (e) {}
    }
    
    // Refresh history list after session completes
    fetchSessions()
    
  } catch (e) {
    discussionMessages.value.push({
      role: 'assistant',
      name: '系统',
      content: '由于网络或后端问题，圆桌讨论未能完成。'
    })
  } finally {
    isDiscussing.value = false
  }
}

const startNewDiscussion = () => {
  activeSessionId.value = ''
  discussionMessages.value = []
  saveToLocal()
}

const loadSessionDetail = async (sessionId: string) => {
  activeSessionId.value = sessionId
  try {
    const res = await axios.get(`${API_BASE}/committee/sessions/${sessionId}`)
    const history = res.data.history || []
    // Convert DolphinDB history format to display messages
    // Filter out internal logs
    discussionMessages.value = history.filter((msg: any) => {
      const name = msg.name || ''
      if (name.endsWith('_Thought') || name === 'CIO_Reasoning') return false
      return true
    }).map((msg: any) => ({
      role: msg.role,
      name: msg.name || (msg.role === 'user' ? 'CIO' : '专家'),
      content: msg.content
    }))
  } catch (e) {
    console.error('Failed to load session detail', e)
    discussionMessages.value = [{ role: 'assistant', name: '系统', content: '加载会议记录失败' }]
  }
}

onMounted(() => {
  fetchPresets()
  fetchMpNames()
  fetchSessions()
  restoreFromLocal()
})

// 每次对话变更时自动持久化
watch(discussionMessages, () => {
  saveToLocal()
}, { deep: true })
</script>

<template>
  <div class="animate-in fade-in duration-500 h-[calc(100vh-100px)] flex flex-col md:flex-row gap-6">
    
    <!-- Left Sidebar: Committee Selection + Members -->
    <div class="w-full md:w-72 flex flex-col gap-2 shrink-0 overflow-hidden">
      <!-- 编排预设 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 px-4 py-3 shrink-0">
        <h2 class="text-sm font-black text-slate-900 mb-2 flex items-center gap-1.5">
          🏛️ 编排预设
        </h2>
        <div class="space-y-1">
          <button 
            v-for="(_, name) in committees" 
            :key="name"
            @click="selectedCommittee = name"
            class="w-full text-left px-3 py-2 rounded-lg font-bold text-xs transition-all border"
            :class="selectedCommittee === name ? 'bg-blue-600 text-white border-blue-600 shadow-sm' : 'bg-slate-50 text-slate-600 border-slate-200 hover:border-blue-300 hover:bg-blue-50'"
          >
            {{ name }}
          </button>
        </div>
      </div>
      
      <!-- 委员会成员列表 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 px-4 py-3 shrink-0 flex flex-col" style="max-height: 280px;">
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-bold text-slate-800 text-sm flex items-center gap-1.5">
            👥 委员会成员
          </h3>
          <div class="flex items-center gap-1.5">
            <button 
              @click="showAddMember = !showAddMember"
              class="w-6 h-6 flex items-center justify-center rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100 transition-colors font-bold text-sm border border-blue-100"
              title="添加成员"
            >+</button>
            <button 
              @click="saveMembers"
              :disabled="saving"
              class="w-6 h-6 flex items-center justify-center rounded-md bg-green-50 text-green-700 hover:bg-green-100 transition-colors font-bold text-xs border border-green-100 disabled:opacity-50"
              title="保存当前成员"
            >
              {{ saving ? '·' : '💾' }}
            </button>
          </div>
        </div>
        
        <!-- 添加成员面板 -->
        <div v-if="showAddMember" class="mb-2 p-2 bg-blue-50 rounded-lg border border-blue-100">
          <div class="text-[10px] font-bold text-blue-700 mb-1.5">从已入库公众号中选择：</div>
          <div class="flex flex-wrap gap-1 max-h-24 overflow-y-auto mb-1.5">
            <button 
              v-for="name in availableMpNames" 
              :key="name"
              @click="addMember(name)"
              class="px-1.5 py-0.5 bg-white border border-blue-200 text-slate-700 rounded text-[10px] font-medium hover:bg-blue-600 hover:text-white hover:border-blue-600 transition-colors"
            >
              {{ name }}
            </button>
          </div>
          <div class="flex gap-1.5">
            <input 
              v-model="newMemberName" 
              @keypress.enter="addMember(newMemberName)"
              placeholder="或手动输入名称..." 
              class="flex-1 px-2 py-1 text-[10px] border border-blue-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
            >
            <button 
              @click="addMember(newMemberName)"
              :disabled="!newMemberName.trim()"
              class="px-2 py-1 text-[10px] bg-blue-600 text-white font-bold rounded hover:bg-blue-700 disabled:bg-slate-300 transition-colors"
            >添加</button>
          </div>
        </div>
        
        <!-- 成员列表 -->
        <div class="flex-1 overflow-y-auto space-y-1 pr-0.5">
          <div 
            v-for="(member, idx) in currentMembers" 
            :key="idx"
            class="flex items-center gap-2 px-2 py-1.5 bg-slate-50 rounded-md border border-slate-100 group"
          >
            <div class="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center text-white font-black text-[10px] shadow-inner shrink-0">
              {{ member.slice(0, 1) }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="font-bold text-slate-800 text-xs truncate leading-tight">{{ member }}</div>
            </div>
            <button 
              @click="removeMember(idx)"
              class="w-5 h-5 rounded flex items-center justify-center text-slate-400 hover:text-red-500 hover:bg-red-50 opacity-0 group-hover:opacity-100 transition-all text-[10px] font-bold"
              title="移除成员"
            >✕</button>
          </div>
          <div v-if="currentMembers.length === 0" class="text-center text-slate-400 text-xs py-3">
            暂无成员，点击 + 添加
          </div>
        </div>
      </div>

      <!-- 历史对话 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 px-4 py-3 flex-1 flex flex-col overflow-hidden min-h-0">
        <div class="flex items-center justify-between mb-2 shrink-0">
          <h3 class="font-bold text-slate-800 text-sm flex items-center gap-1.5">
            📋 历史对话
          </h3>
          <button 
            @click="startNewDiscussion"
            class="w-6 h-6 flex items-center justify-center rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100 transition-colors font-bold text-sm border border-blue-100"
            title="新建对话"
          >+</button>
        </div>
        <div class="flex-1 overflow-y-auto">
            <div 
              v-for="session in historySessions" 
              :key="session.session_id"
              @click="loadSessionDetail(session.session_id)"
              :title="session.title"
              class="group relative flex items-center justify-between py-1.5 px-2.5 rounded-lg cursor-pointer border border-transparent transition-all hover:bg-white hover:shadow-sm"
              :class="activeSessionId === session.session_id ? 'bg-white shadow-sm border-blue-200' : 'bg-slate-50/50'"
            >
              <div class="flex items-center min-w-0">
                <span class="text-xs font-bold text-slate-700 truncate">{{ session.title }}</span>
              </div>
              
              <button 
                @click="removeSession(session.session_id, $event)"
                class="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-50 text-slate-300 hover:text-red-500 rounded transition-all"
                title="删除记录"
              >
                <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          <div v-if="historySessions.length === 0" class="text-center text-slate-400 text-xs py-4">
            暂无历史会议记录
          </div>
        </div>
      </div>
    </div>

    <!-- Right Main Area: Debate Stream -->
    <div class="flex-1 bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col overflow-hidden relative">
      <!-- Header -->
      <div class="p-5 border-b border-slate-100 bg-slate-50 shrink-0 flex justify-between items-center">
        <div>
          <h2 class="text-lg font-black text-slate-900">
            {{ activeSessionId ? historySessions.find(s => s.session_id === activeSessionId)?.title || '圆桌辩论大厅' : '圆桌辩论大厅' }}
          </h2>
          <p class="text-sm text-slate-500 font-medium mt-1">CIO (首席投资官) 正在主持会议，大模型多智能体实时交互。</p>
        </div>
        <div class="px-3 py-1 bg-green-100 text-green-700 rounded-full text-xs font-bold border border-green-200 flex items-center gap-2">
          <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
          系统就绪
        </div>
      </div>

      <!-- Messages Area -->
      <div class="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-50/50">
        <div v-if="discussionMessages.length === 0" class="h-full flex flex-col items-center justify-center text-slate-400">
          <div class="text-6xl mb-4 opacity-50">🎙️</div>
          <p class="font-medium text-lg text-slate-500">请在下方输入议题，召集 {{ selectedCommittee }} 召开会议</p>
        </div>
        
        <div v-for="(msg, i) in discussionMessages" :key="i" class="flex flex-col gap-1" :class="msg.role === 'user' ? 'items-end' : 'items-start'">
          <div class="flex items-center gap-2 px-2" :class="msg.role === 'user' ? 'flex-row-reverse' : ''">
            <div class="text-xs font-bold" :class="msg.role === 'user' ? 'text-blue-600' : 'text-indigo-600'">
              {{ msg.name }}
            </div>
            <div class="text-[10px] text-slate-400 font-medium uppercase">{{ msg.role === 'user' ? 'CIO' : 'EXPERT' }}</div>
          </div>
          <div 
            class="px-5 py-3.5 rounded-2xl shadow-sm text-sm leading-relaxed max-w-[85%]"
            :class="msg.role === 'user' ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-white border border-slate-200 text-slate-800 rounded-tl-sm markdown-body'"
          >
            <div v-if="msg.role === 'assistant'" v-html="marked(splitContent(msg.content).body)"></div>
            <div v-else>{{ msg.content }}</div>
            <details v-if="msg.role === 'assistant' && splitContent(msg.content).refs" class="mt-2 border-t border-slate-100 pt-2">
              <summary class="text-xs text-slate-400 font-bold cursor-pointer hover:text-blue-500 transition-colors select-none">📚 参考资料 ({{ splitContent(msg.content).refs.split('\n').filter((l: string) => l.trim().startsWith('*')).length }}篇)</summary>
              <div class="mt-1.5 text-xs text-slate-500 leading-relaxed" v-html="marked(splitContent(msg.content).refs)"></div>
            </details>
          </div>
        </div>
        
        <div v-if="isDiscussing" class="flex flex-col gap-1 items-start">
          <div class="flex items-center gap-2 px-2">
            <div class="text-xs font-bold text-slate-500">专家思考中</div>
          </div>
          <div class="px-5 py-4 bg-white border border-slate-200 rounded-2xl rounded-tl-sm shadow-sm flex items-center gap-2">
            <span class="w-2 h-2 bg-blue-500 rounded-full animate-bounce"></span>
            <span class="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style="animation-delay: 0.2s"></span>
            <span class="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style="animation-delay: 0.4s"></span>
          </div>
        </div>
      </div>

      <!-- Input Area -->
      <div class="p-4 bg-white border-t border-slate-100 shrink-0">
        <div class="relative bg-slate-50 border border-slate-300 rounded-xl shadow-inner focus-within:ring-2 focus-within:ring-blue-100 focus-within:border-blue-400 transition-all flex items-center p-1">
          <input 
            v-model="currentInput" 
            @keypress.enter="startDiscussion"
            :disabled="isDiscussing"
            type="text"
            placeholder="输入投研议题，例如：基于近期政策，评估房地产基本面..." 
            class="flex-1 bg-transparent border-none focus:ring-0 py-3 pl-4 text-slate-800 font-medium disabled:opacity-50"
          >
          <button 
            @click="startDiscussion"
            :disabled="isDiscussing || !currentInput.trim()"
            class="px-6 py-2.5 mr-1 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-700 disabled:bg-slate-300 disabled:text-slate-500 transition-all flex items-center gap-2 shrink-0"
          >
            🚀 发起圆桌
          </button>
        </div>
      </div>
    </div>
    
  </div>
</template>
