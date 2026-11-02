/**
 * AppButton contract tests.
 *
 * The 32/40/48px heights cannot be verified in jsdom (no layout engine), so
 * these tests assert the structural contract — classes, disabled/loading
 * semantics, aria-busy, no double submit — and the real pixel measurement is
 * performed in the browser during acceptance.
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppButton from '@/components/AppButton.vue'

describe('AppButton', () => {
  it('defaults to a medium primary button', () => {
    const wrapper = mount(AppButton, { slots: { default: '保存配置' } })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('app-button--medium')
    expect(button.classes()).toContain('app-button--primary')
    expect(button.attributes('type')).toBe('button')
    expect(button.text()).toContain('保存配置')
  })

  it.each([
    ['small', 'app-button--small'],
    ['medium', 'app-button--medium'],
    ['large', 'app-button--large'],
  ] as const)('applies the %s size class', (size, expectedClass) => {
    const wrapper = mount(AppButton, { props: { size }, slots: { default: 'x' } })
    expect(wrapper.get('button').classes()).toContain(expectedClass)
  })

  it.each([
    ['primary', 'app-button--primary'],
    ['secondary', 'app-button--secondary'],
    ['ghost', 'app-button--ghost'],
    ['danger', 'app-button--danger'],
    ['danger-text', 'app-button--danger-text'],
  ] as const)('applies the %s variant class', (variant, expectedClass) => {
    const wrapper = mount(AppButton, { props: { variant }, slots: { default: 'x' } })
    expect(wrapper.get('button').classes()).toContain(expectedClass)
  })

  it('does not emit click when disabled', async () => {
    const wrapper = mount(AppButton, { props: { disabled: true }, slots: { default: 'x' } })
    const button = wrapper.get('button')
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('does not emit click while loading and marks aria-busy', async () => {
    const wrapper = mount(AppButton, {
      props: { loading: true, loadingText: '保存中…' },
      slots: { default: '保存' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('保存中…')
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('keeps the label visible while loading (stable width)', () => {
    const wrapper = mount(AppButton, {
      props: { loading: true, loadingText: '保存中…' },
      slots: { default: '保存' },
    })
    expect(wrapper.find('.app-button__spinner').exists()).toBe(true)
    expect(wrapper.get('.app-button__label').text()).toContain('保存中…')
  })

  it('requires an accessible name for icon-only buttons', () => {
    const wrapper = mount(AppButton, {
      props: { iconOnly: true, ariaLabel: '搜索' },
      slots: { icon: '<svg />' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('aria-label')).toBe('搜索')
    expect(button.classes()).toContain('app-button--icon')
  })

  it('emits click once per activation when enabled', async () => {
    const wrapper = mount(AppButton, { slots: { default: 'x' } })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('submits with type=submit only when requested', () => {
    const wrapper = mount(AppButton, { props: { type: 'submit' }, slots: { default: 'x' } })
    expect(wrapper.get('button').attributes('type')).toBe('submit')
  })
})
