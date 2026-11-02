import { createRouter, createMemoryHistory } from 'vue-router'
import type { Router } from 'vue-router'

/**
 * A test router that mounts real views with a memory history.
 *
 * Routes are declared explicitly so a view can be exercised without importing
 * the production guard (which requires a live API).
 */
export function createTestRouter(
  view: unknown,
  options: { path?: string; name?: string } = {},
): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', redirect: '/overview' },
      {
        path: options.path ?? '/',
        name: options.name ?? 'test-view',
        component: view as never,
      },
      { path: '/overview', name: 'overview', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/setup', name: 'setup', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', name: 'fallback', component: { template: '<div />' } },
    ],
  })
}
