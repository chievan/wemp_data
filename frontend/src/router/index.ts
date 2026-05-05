import { createRouter, createWebHistory } from 'vue-router'
import Knowledge from '../views/Knowledge.vue'
import AiCommittee from '../views/AiCommittee.vue'
import Dashboard from '../views/Dashboard.vue'
import SystemLogs from '../views/SystemLogs.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/knowledge'
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: Knowledge
    },
    {
      path: '/ai-committee',
      name: 'ai_committee',
      component: AiCommittee
    },
    {
      path: '/dashboard',
      name: 'data_asset',
      component: Dashboard
    },
    {
      path: '/logs',
      name: 'system_logs',
      component: SystemLogs
    }
  ]
})

export default router
