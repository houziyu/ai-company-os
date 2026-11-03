/**
 * Navigation and page metadata in one place.
 *
 * Pages that exist in Phase 01 as shells declare the phase that activates them.
 * Both the sidebar and the placeholder pages read this file, so a page cannot
 * claim to be "open" in one place and "coming later" in another.
 */

export interface NavEntry {
  label: string
  path: string
  /** Human-readable purpose shown on placeholder pages. */
  purpose: string
  /** Phase that makes the page functional; null when already functional. */
  readyPhase: number | null
}

export const navEntries: NavEntry[] = [
  {
    label: '公司概览',
    path: '/overview',
    purpose: '查看公司在做什么、哪里需要你决定、产物是否通过验证。',
    readyPhase: null,
  },
  {
    label: '组织',
    path: '/organization',
    purpose: '查看五个岗位、员工实例、汇报关系与模型分配。',
    readyPhase: null,
  },
  {
    label: '模型中心',
    path: '/models',
    purpose: '配置模型供应商、逻辑模型与员工模型分配。',
    readyPhase: null,
  },
  {
    label: '项目与任务',
    path: '/projects',
    purpose: '创建项目、提交任务、跟踪每次执行的状态与证据。',
    readyPhase: null,
  },
  {
    label: '提案中心',
    path: '/proposals',
    purpose: '处理 Finding 提案：审批、要求补充、拒绝或升级。',
    readyPhase: 6,
  },
  {
    label: '知识',
    path: '/knowledge',
    purpose: '查看候选与已验证的项目知识和组织制度。',
    readyPhase: 7,
  },
  {
    label: '预算',
    path: '/budgets',
    purpose: '查看公司、项目额度与 Token 账本明细。',
    readyPhase: null,
  },
  {
    label: '审计',
    path: '/audit',
    purpose: '按主体、动作、对象和时间检索审计事件。',
    readyPhase: null,
  },
]

export const settingsEntry: NavEntry = {
  label: '设置',
  path: '/settings',
  purpose: '查看账号、服务状态、版本与备份恢复说明。',
  readyPhase: null,
}

export function findNavEntry(path: string): NavEntry | undefined {
  return [...navEntries, settingsEntry].find((entry) => entry.path === path)
}

export function phaseNote(entry: NavEntry): string {
  return entry.readyPhase === null
    ? ''
    : `此阶段尚未开放：本页在阶段 0${entry.readyPhase} 接入真实功能，当前展示的是空状态。`
}
