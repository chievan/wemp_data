<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

const logFiles = ref<any[]>([])
const activeLogKey = ref('')
const logContent = ref<string[]>([])
const logsLoading = ref(false)
const tailLines = ref(200)
const isReversed = ref(true) // 默认开启倒序，最新的在上面

const fetchLogFiles = async () => {
  try {
    const res = await axios.get(`${API_BASE}/logs/list`)
    logFiles.value = res.data
    if (logFiles.value.length > 0 && !activeLogKey.value) {
      fetchLogContent(logFiles.value[0].key)
    }
  } catch (e) {
    console.error(e)
  }
}

const fetchLogContent = async (key: string) => {
  activeLogKey.value = key
  logsLoading.value = true
  try {
    const res = await axios.get(`${API_BASE}/logs/read/${key}?tail=${tailLines.value}`)
    logContent.value = res.data.lines || []
  } catch (e) {
    console.error(e)
    logContent.value = ['获取日志失败']
  } finally {
    logsLoading.value = false
  }
}

const displayLogs = computed(() => {
  if (isReversed.value) {
    return [...logContent.value].reverse()
  }
  return logContent.value
})

onMounted(() => {
  fetchLogFiles()
})
</script>

<template>
  <div class="animate-in fade-in duration-500 h-[calc(100vh-120px)] flex flex-col">
    <div class="mb-4 shrink-0">
      <h1 class="text-2xl font-extrabold text-slate-900 tracking-tight">🖥️ 系统日志管理</h1>
      <p class="text-slate-500 font-medium text-sm mt-1">实时查看 API 服务、同步流水线、投委会 Agent 及向量库后台日志。</p>
    </div>

    <!-- 系统日志管理 -->
    <div class="flex-1 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden flex flex-col">
      <div class="px-6 py-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
        <div class="flex items-center gap-4">
          <h2 class="text-base font-bold text-slate-800">实时运行日志</h2>
          <div class="h-4 w-[1px] bg-slate-300"></div>
          <div class="flex gap-1">
             <span v-for="file in logFiles" :key="file.key" 
               @click="fetchLogContent(file.key)"
               class="px-3 py-1 text-xs rounded-full cursor-pointer transition-all border font-bold"
               :class="file.key === activeLogKey 
                 ? 'bg-blue-600 text-white border-blue-600 shadow-sm' 
                 : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'">
               {{ file.filename.replace('.log', '') }}
             </span>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-2 text-xs font-bold text-slate-500 cursor-pointer hover:text-blue-600 transition-colors bg-white px-3 py-1.5 rounded-lg border border-slate-200">
            <input type="checkbox" v-model="isReversed" class="w-3.5 h-3.5 text-blue-600 rounded border-slate-300">
            最新的在最上方
          </label>
          <select 
            v-model="tailLines" 
            @change="fetchLogContent(activeLogKey)"
            class="bg-white border border-slate-300 text-slate-700 text-xs font-bold rounded-lg py-1.5 px-3 focus:ring-0 cursor-pointer outline-none"
          >
            <option :value="100">查看 100 行</option>
            <option :value="200">查看 200 行</option>
            <option :value="500">查看 500 行</option>
            <option :value="1000">查看 1000 行</option>
          </select>
          <button 
            @click="fetchLogContent(activeLogKey)"
            class="px-4 py-1.5 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-700 transition-colors text-xs flex items-center gap-1"
          >
            <span>🔄</span> 刷新日志
          </button>
        </div>
      </div>
      
      <div class="flex-1 flex overflow-hidden">
        <!-- 右侧日志内容 -->
        <div class="flex-1 bg-slate-900 p-6 font-mono text-[12px] overflow-auto text-slate-300 leading-relaxed custom-scrollbar">
          <div v-if="logsLoading" class="flex items-center gap-2 text-blue-400 font-bold mb-4">
            <div class="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin"></div>
            正在实时拉取日志内容...
          </div>
          <div v-else-if="logContent.length === 0" class="text-slate-500 italic py-10 text-center">暂无日志内容</div>
          <div v-for="(line, idx) in displayLogs" :key="idx" class="whitespace-pre-wrap mb-1 border-l-2 border-slate-800 pl-4 hover:border-blue-500 transition-colors">
            <span class="text-slate-600 mr-4 select-none opacity-50 w-8 inline-block">{{ isReversed ? logContent.length - idx : idx + 1 }}</span>
            <span :class="line.includes('ERROR') || line.includes('CRITICAL') ? 'text-red-400 font-bold' : line.includes('WARNING') ? 'text-amber-300' : ''">
              {{ line }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 10px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: #0f172a;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #334155;
  border-radius: 5px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #475569;
}
</style>
