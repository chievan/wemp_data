<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

const navItems = [
  { key: 'knowledge', label: '🔍 研报知识库', path: '/knowledge' },
  { key: 'ai_committee', label: '⚖️ 智能投委会', path: '/ai-committee' },
  { key: 'data_asset', label: '📊 数据资产中心', path: '/dashboard' },
  { key: 'system_logs', label: '🖥️ 系统日志管理', path: '/logs' },
]
</script>

<template>
  <div class="min-h-screen w-full bg-slate-50 flex flex-col font-sans text-slate-800">
    <!-- Top Navigation Bar -->
    <header class="fixed top-0 left-0 right-0 z-50 bg-white/95 backdrop-blur-md border-b border-slate-200 h-[68px] flex items-center px-6 shadow-sm">
      <div class="flex items-center gap-3 w-1/4">
        <div class="text-3xl drop-shadow-sm">🏢</div>
        <div>
          <div class="text-[1.05rem] font-extrabold text-slate-900 leading-tight">Wemp 投研门户</div>
          <div class="text-xs text-slate-500 font-medium tracking-wide">知识库 · 投委会 · 数据运维</div>
        </div>
      </div>
      
      <nav class="flex-1 flex items-center justify-center gap-2">
        <router-link
          v-for="item in navItems"
          :key="item.key"
          :to="item.path"
          class="px-4 py-2 rounded-lg text-sm font-semibold transition-all duration-200"
          :class="route.path === item.path 
            ? 'bg-slate-100 text-blue-900 shadow-sm border border-slate-200' 
            : 'text-slate-600 hover:bg-slate-100/50 hover:text-slate-900 border border-transparent'"
        >
          {{ item.label }}
        </router-link>
      </nav>
      
      <div class="w-1/4"></div>
    </header>

    <!-- Main Content Area -->
    <main class="flex-1 mt-[68px] p-6 md:p-8 w-full mx-auto">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
