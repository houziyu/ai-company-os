import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  ElConfigProvider,
  ElDialog,
  ElDrawer,
  ElIcon,
  ElLoading,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTooltip,
} from 'element-plus'

import 'element-plus/dist/index.css'
import './styles/tokens.css'
import './styles/element-theme.css'
import './styles/base.css'
import './styles/tables.css'

import App from './App.vue'
import router from './router'

/**
 * Element Plus is registered component-by-component rather than via the whole
 * library plugin. Only what the product actually renders ships to the browser;
 * adding a component here is an explicit, reviewable change.
 */
const app = createApp(App)

app.use(createPinia())
app.use(router)

app.component('ElConfigProvider', ElConfigProvider)
app.component('ElDialog', ElDialog)
app.component('ElDrawer', ElDrawer)
app.component('ElIcon', ElIcon)
app.component('ElOption', ElOption)
app.component('ElSelect', ElSelect)
app.component('ElTable', ElTable)
app.component('ElTableColumn', ElTableColumn)
app.component('ElTooltip', ElTooltip)
app.use(ElLoading)

app.mount('#app')
