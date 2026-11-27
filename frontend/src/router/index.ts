import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/alarm_trigger/recheck/:recheckId?',
      name: 'alarm-recheck-detail',
      component: WorkbenchView,
      props: (route) => ({
        moduleKey: 'alarm_trigger',
        initialRecheckId: route.params.recheckId ? String(route.params.recheckId) : undefined,
      }),
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
