<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

import { marked } from 'marked'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

// Split message content into body and references
const splitContent = (content: string) => {
  const marker = '[WEMP_REFS_START]'
  const idx = content.indexOf(marker)
  if (idx !== -1) {
    return {
      body: content.substring(0, idx).trim(),
      refs: content.substring(idx + marker.length).trim()
    }
  }
  
  // Fallback for older sessions or if marker is missing
  const refPatterns = ['\n\n参考资料：', '\n\n参考资料:', '\n参考资料：', '\n参考资料:',
    '\n\n资料来源与链接：', '\n\n资料来源与链接:', '\n资料来源与链接：', '\n资料来源与链接:',
    '\n\n**参考资料', '\n\n**资料来源', '\n\n---\n']
  for (const pat of refPatterns) {
    const fIdx = content.indexOf(pat)
    if (fIdx !== -1) {
      return {
        body: content.substring(0, fIdx).trim(),
        refs: content.substring(fIdx + pat.length).trim()
      }
    }
  }
  return { body: content, refs: '' }
}

const enableWeb = ref(true)
const selectedModel = ref('deepseek-v4-flash')

const generateId = () => Math.random().toString(36).substring(2, 9)

interface ChatSession {
  id: string
  title: string
  messages: {role: 'user'|'assistant', content: string}[]
}

const chatSessions = ref<ChatSession[]>([])
const currentSessionId = ref<string>('')

const loadSessions = () => {
  try {
    const saved = localStorage.getItem('wemp_chat_sessions')
    if (saved) {
      chatSessions.value = JSON.parse(saved)
    }
  } catch (e) {
    console.error('Failed to load chat history', e)
  }
  if (chatSessions.value.length === 0) {
    createNewSession()
  } else {
    currentSessionId.value = chatSessions.value[0].id
  }
}

const saveSessions = () => {
  localStorage.setItem('wemp_chat_sessions', JSON.stringify(chatSessions.value))
}

const createNewSession = () => {
  const newSession: ChatSession = {
    id: generateId(),
    title: '新对话',
    messages: [{ role: 'assistant', content: '您好，我是基于 DolphinDB 向量检索的投研知识库助手。请问有什么可以帮您？' }]
  }
  chatSessions.value.unshift(newSession)
  currentSessionId.value = newSession.id
  saveSessions()
}

const selectSession = (id: string) => {
  currentSessionId.value = id
}

const removeSession = (id: string, event: Event) => {
  event.stopPropagation()
  if (!confirm('确定要删除这段对话历史吗？')) return
  
  const index = chatSessions.value.findIndex(s => s.id === id)
  if (index !== -1) {
    chatSessions.value.splice(index, 1)
    if (currentSessionId.value === id) {
      if (chatSessions.value.length > 0) {
        currentSessionId.value = chatSessions.value[0].id
      } else {
        createNewSession()
      }
    }
    saveSessions()
  }
}

const currentMessages = computed(() => {
  const session = chatSessions.value.find(s => s.id === currentSessionId.value)
  return session ? session.messages : []
})

const currentInput = ref('')
const isChatting = ref(false)

const sendMessage = async () => {
  if (!currentInput.value.trim() || isChatting.value) return
  
  const msg = currentInput.value.trim()
  currentInput.value = ''
  
  const session = chatSessions.value.find(s => s.id === currentSessionId.value)
  if (!session) return

  session.messages.push({ role: 'user', content: msg })
  
  // Auto-generate title for the first user message
  if (session.messages.filter(m => m.role === 'user').length === 1) {
    session.title = msg.length > 15 ? msg.slice(0, 15) + '...' : msg
  }
  
  saveSessions()
  
  isChatting.value = true
  session.messages.push({ role: 'assistant', content: '' })
  const assistantIdx = session.messages.length - 1
  
  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        message: msg, 
        enable_web: enableWeb.value,
        model: selectedModel.value 
      })
    })
    
    if (!response.body) throw new Error('No response body')
    
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value)
      session.messages[assistantIdx].content += chunk
    }
  } catch (e) {
    session.messages[assistantIdx].content = '由于网络或模型问题，回答生成失败。'
  } finally {
    isChatting.value = false
    saveSessions()
  }
}

onMounted(() => {
  loadSessions()
})
</script>

<template>
  <div class="animate-in fade-in duration-500 h-[calc(100vh-100px)] flex flex-col">
    <!-- Chat View (Full Page) -->
    <div class="flex-1 bg-white rounded-2xl shadow-sm border border-slate-200 flex overflow-hidden">
      
      <!-- Left Sidebar: History -->
      <div class="w-64 bg-slate-50 border-r border-slate-200 flex flex-col shrink-0 hidden md:flex">
        <div class="px-4 py-3 border-b border-slate-200 flex justify-between items-center bg-white">
          <span class="font-bold text-slate-700 text-sm">历史对话</span>
          <button @click="createNewSession" class="text-slate-400 hover:text-blue-600 font-bold" title="新建对话">➕</button>
        </div>
        <div class="flex-1 overflow-y-auto p-2 space-y-1">
          <div 
            v-for="session in chatSessions" 
            :key="session.id"
            @click="selectSession(session.id)"
            class="group relative flex items-center p-3 rounded-xl cursor-pointer transition-all duration-200"
            :class="currentSessionId === session.id ? 'bg-blue-50 border-blue-100' : 'hover:bg-slate-50 border-transparent'"
          >
            <div class="flex-1 min-w-0">
              <div class="text-sm font-bold truncate" :class="currentSessionId === session.id ? 'text-blue-700' : 'text-slate-700'">
                {{ session.title }}
              </div>
              <div class="text-[10px] text-slate-400 mt-0.5">{{ session.messages.length }} 条对话</div>
            </div>
            
            <!-- Delete Button -->
            <button 
              @click="removeSession(session.id, $event)"
              class="opacity-0 group-hover:opacity-100 p-1.5 hover:bg-red-50 text-slate-300 hover:text-red-500 rounded-lg transition-all"
              title="删除对话"
            >
              <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <!-- Right Main Chat -->
      <div class="flex-1 flex flex-col bg-white relative">
        <!-- Messages Area -->
        <div class="flex-1 overflow-y-auto p-4 md:p-8 space-y-8">
          <div class="max-w-4xl mx-auto w-full">
            <div v-for="(msg, i) in currentMessages" :key="i" class="flex flex-col mb-8" :class="msg.role === 'user' ? 'items-end' : 'items-start'">
              <div class="flex items-center gap-2 mb-2" v-if="msg.role === 'assistant'">
                <div class="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-sm shadow-sm">AI</div>
                <span class="font-bold text-slate-700">Wemp 投研助手</span>
              </div>
              <div 
                class="px-5 py-4 rounded-2xl shadow-sm leading-normal max-w-[90%] md:max-w-[85%]"
                :class="msg.role === 'user' ? 'bg-blue-600 text-white rounded-tr-sm text-md whitespace-pre-wrap' : 'bg-slate-50 border border-slate-200 text-slate-800 rounded-tl-sm markdown-body'"
              >
                <div v-if="msg.role === 'assistant'" v-html="marked(splitContent(msg.content).body)"></div>
                <div v-else>{{ msg.content }}</div>
                <details v-if="msg.role === 'assistant' && splitContent(msg.content).refs" class="mt-3 border-t border-slate-200 pt-2">
                  <summary class="text-xs text-slate-400 font-bold cursor-pointer hover:text-blue-500 transition-colors select-none">📚 资料来源与链接</summary>
                  <div class="mt-1.5 text-xs text-slate-500 leading-relaxed" v-html="marked(splitContent(msg.content).refs)"></div>
                </details>
              </div>
              <div v-if="isChatting && i === currentMessages.length - 1 && msg.role === 'assistant'" class="mt-2 flex items-center gap-1 text-slate-400 text-sm ml-2">
                <span class="w-2 h-2 bg-slate-400 rounded-full animate-bounce"></span>
                <span class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 0.2s"></span>
                <span class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 0.4s"></span>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Input Area (Centered) -->
        <div class="p-4 bg-white border-t border-slate-100 shrink-0">
          <div class="max-w-4xl mx-auto flex flex-col gap-2">
            
            <!-- Controls Above Input -->
            <div class="flex items-center gap-4 px-1">
              <select 
                v-model="selectedModel"
                class="bg-slate-100 border-none text-slate-700 text-sm font-bold rounded-lg py-1.5 px-3 focus:ring-0 cursor-pointer outline-none"
              >
                <option value="deepseek-v4-flash">DeepSeek-V4-Flash</option>
                <option value="deepseek-v4-pro">DeepSeek-V4-Pro</option>
                <option value="qwen-plus">通义千问 Qwen-Plus</option>
                <option value="qwen-max">通义千问 Qwen-Max</option>
              </select>
              
              <label class="flex items-center gap-2 text-sm text-slate-600 cursor-pointer hover:text-blue-600 transition-colors bg-slate-100 px-3 py-1.5 rounded-lg font-bold">
                <input type="checkbox" v-model="enableWeb" class="w-3.5 h-3.5 text-blue-600 rounded border-slate-300">
                🌐 联网检索
              </label>
            </div>

            <!-- Chat Input Box -->
            <div class="relative bg-slate-50 border border-slate-300 rounded-2xl shadow-sm focus-within:ring-2 focus-within:ring-blue-100 focus-within:border-blue-400 transition-all flex items-end">
              <textarea 
                v-model="currentInput" 
                @keypress.enter.prevent="sendMessage"
                :disabled="isChatting"
                placeholder="向投研知识库提问，例如：总结近期信用债市场的研报观点" 
                class="w-full bg-transparent border-none focus:ring-0 resize-none py-4 pl-5 pr-16 text-slate-800 disabled:opacity-50 min-h-[56px] max-h-[200px]"
                rows="1"
                style="field-sizing: content;"
              ></textarea>
              <button 
                @click="sendMessage" 
                :disabled="isChatting || !currentInput.trim()"
                class="absolute right-2 bottom-2 p-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:bg-slate-200 disabled:text-slate-400 transition-colors flex items-center justify-center w-10 h-10"
              >
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M3.293 9.707a1 1 0 010-1.414l6-6a1 1 0 011.414 0l6 6a1 1 0 01-1.414 1.414L11 5.414V17a1 1 0 11-2 0V5.414L4.707 9.707a1 1 0 01-1.414 0z" clip-rule="evenodd" />
                </svg>
              </button>
            </div>
            <div class="text-center text-xs text-slate-400 mt-1">AI 生成内容仅供参考，不构成任何投资建议，请核实关键信息。</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
