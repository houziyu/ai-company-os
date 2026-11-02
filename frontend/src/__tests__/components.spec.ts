/**
 * Components that carry accessibility or state semantics get contract tests.
 * Layout/pixel checks are performed in the browser during acceptance because
 * jsdom has no layout engine.
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'

describe('StatusBadge', () => {
  it('always renders text so colour is not the only signal', () => {
    const wrapper = mount(StatusBadge, { props: { label: '等待审批', tone: 'warning' } })
    expect(wrapper.text()).toContain('等待审批')
    expect(wrapper.classes()).toContain('status-badge--warning')
  })

  it('marks the dot decorative', () => {
    const wrapper = mount(StatusBadge, { props: { label: '执行中', tone: 'info' } })
    expect(wrapper.get('.status-badge__dot').attributes('aria-hidden')).toBe('true')
  })
})

describe('FormField', () => {
  it('associates the label with the control id and describedby ids', () => {
    const wrapper = mount(FormField, {
      props: { label: '项目名称', fieldName: 'project-name', hint: '示例说明', error: '必填' },
      slots: {
        default: `<template #default="{ controlId, describedBy }">
            <input :id="controlId" :aria-describedby="describedBy" />
          </template>`,
      },
    })
    const label = wrapper.get('label')
    const controlId = label.attributes('for')
    expect(controlId).toBeTruthy()
    const input = wrapper.get('input')
    expect(input.attributes('id')).toBe(controlId)
    const describedBy = input.attributes('aria-describedby') ?? ''
    expect(describedBy.split(' ')).toHaveLength(2)
  })

  it('renders the error as an alert and hides the hint when an error exists', () => {
    const wrapper = mount(FormField, {
      props: { label: '登录名', fieldName: 'u', hint: '帮助', error: '不能为空' },
      slots: { default: '<input />' },
    })
    expect(wrapper.get('.form-field__error').attributes('role')).toBe('alert')
    expect(wrapper.find('.form-field__hint').exists()).toBe(false)
  })

  it('announces required state to assistive tech', () => {
    const wrapper = mount(FormField, {
      props: { label: '密码', fieldName: 'p', required: true },
      slots: { default: '<input />' },
    })
    expect(wrapper.text()).toContain('（必填）')
  })
})

describe('EmptyState', () => {
  it('shows the phase note instead of a fake action', () => {
    const wrapper = mount(EmptyState, {
      props: {
        title: '还没有任务',
        description: '任务调度在阶段 03 接入。',
        phaseNote: '阶段 03 开放项目与任务',
      },
    })
    expect(wrapper.text()).toContain('还没有任务')
    expect(wrapper.text()).toContain('阶段 03 开放项目与任务')
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})

describe('ErrorState', () => {
  it('is announced, shows the request id and emits retry', async () => {
    const wrapper = mount(ErrorState, {
      props: { description: '数据库不可用。', requestId: 'abc123' },
    })
    expect(wrapper.attributes('role')).toBe('alert')
    expect(wrapper.text()).toContain('abc123')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})

describe('PageHeader', () => {
  it('renders one action slot region', () => {
    const wrapper = mount(PageHeader, {
      props: { title: '公司概览', description: '查看进展' },
      slots: { actions: '<button>创建</button>' },
    })
    expect(wrapper.get('h1').text()).toBe('公司概览')
    expect(wrapper.findAll('.page-header__actions button')).toHaveLength(1)
  })
})
