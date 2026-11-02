/**
 * Session store.
 *
 * Holds only what the UI needs: the current owner profile and expiry. The real
 * authority is the server session cookie; this store never decides access, it
 * only renders what /auth/session reported.
 */

import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref } from 'vue'

import { authApi } from '@/api/endpoints'
import { ApiError, NetworkError } from '@/api/client'
import {
  advanceSessionGeneration,
  getSessionGeneration,
  onAuthFailure,
} from '@/api/auth-failure'
import type { OwnerProfile, SetupPayload } from '@/api/types'

export type AuthState = 'unknown' | 'anonymous' | 'authenticated'

export const useSessionStore = defineStore('session', () => {
  const state = ref<AuthState>('unknown')
  const user = ref<OwnerProfile | null>(null)
  const expiresAt = ref<string | null>(null)
  const setupComplete = ref<boolean | null>(null)
  // Company initialization is a separate step after owner setup, so the router
  // can send an authenticated owner to the wizard exactly once.
  const companyInitialized = ref<boolean | null>(null)
  const companyName = ref<string | null>(null)
  const passwordMinLength = ref<number>(12)
  const lastError = ref<string | null>(null)
  // Authentication commands have their own ordering: a valid credential
  // response may succeed after the old cookie receives a 401 in the meantime.
  let authOperation = 0

  const isAuthenticated = computed(() => state.value === 'authenticated')
  const isReady = computed(() => state.value !== 'unknown')

  function setSession(profile: OwnerProfile, expiry: string) {
    state.value = 'authenticated'
    user.value = profile
    expiresAt.value = expiry
    setupComplete.value = true
    lastError.value = null
  }

  /**
   * Record that the wizard created the company, so the guard stops redirecting
   * to it. Called only after the server confirmed creation.
   */
  function markCompanyInitialized(name: string) {
    companyInitialized.value = true
    companyName.value = name
  }

  function applySession(profile: OwnerProfile, expiry: string) {
    authOperation++
    advanceSessionGeneration()
    setSession(profile, expiry)
  }

  function invalidateSession() {
    advanceSessionGeneration()
    state.value = 'anonymous'
    user.value = null
    expiresAt.value = null
  }

  function clearSession() {
    authOperation++
    invalidateSession()
  }

  onScopeDispose(
    onAuthFailure(() => {
      invalidateSession()
      lastError.value = '登录已失效，请重新登录。'
    }),
  )

  async function loadSetupStatus(): Promise<boolean> {
    const generation = getSessionGeneration()
    const operation = authOperation
    const status = await authApi.setupStatus()
    if (generation !== getSessionGeneration() || operation !== authOperation)
      return setupComplete.value ?? status.setupComplete
    setupComplete.value = status.setupComplete
    companyInitialized.value = status.companyInitialized
    companyName.value = status.companyName
    passwordMinLength.value = status.passwordMinLength
    return status.setupComplete
  }

  /** Restore the session from the server; never trusts local storage. */
  async function refresh(): Promise<void> {
    const generation = getSessionGeneration()
    const operation = authOperation
    const isCurrent = () =>
      generation === getSessionGeneration() && operation === authOperation
    try {
      const session = await authApi.session()
      if (!isCurrent()) return
      // A read confirms the same session; it must not hide concurrent valid
      // 401s by creating a new generation on every successful refresh.
      setSession(session.user, session.expiresAt)
    } catch (error) {
      // Includes the transport's own 401 notification, which already cleared
      // this generation. Never overwrite a newer login or logout here.
      if (!isCurrent()) return
      if (error instanceof ApiError && error.isAuthFailure) {
        invalidateSession()
        return
      }
      // A network failure must not be rendered as "logged out" — that would
      // mislead the user into entering credentials again while the API is down.
      state.value = 'unknown'
      lastError.value =
        error instanceof NetworkError
          ? error.message
          : '无法确认登录状态，请稍后重试。'
      throw error
    }
  }

  async function login(username: string, password: string): Promise<void> {
    const operation = ++authOperation
    const session = await authApi.login({ username, password })
    if (operation !== authOperation) return
    applySession(session.user, session.expiresAt)
  }

  async function completeSetup(payload: SetupPayload): Promise<void> {
    const operation = ++authOperation
    const session = await authApi.setup(payload)
    if (operation !== authOperation) return
    applySession(session.user, session.expiresAt)
    setupComplete.value = true
  }

  async function logout(): Promise<void> {
    // Invalidate reads already in flight as soon as logout starts. Guard the
    // final cleanup too, so a late logout cannot erase a subsequent login.
    const operation = ++authOperation
    advanceSessionGeneration()
    lastError.value = null
    try {
      await authApi.logout()
    } catch (error) {
      if (operation !== authOperation) return
      if (error instanceof ApiError && error.isAuthFailure) return
      lastError.value = '注销结果尚未确认，服务器会话可能仍然有效。请重试注销。'
      throw error
    } finally {
      // Local clearing is not evidence of server-side revocation. The caller
      // must display lastError and offer retry when the result is uncertain.
      if (operation === authOperation) clearSession()
    }
  }

  return {
    state,
    user,
    expiresAt,
    setupComplete,
    companyInitialized,
    companyName,
    passwordMinLength,
    lastError,
    isAuthenticated,
    isReady,
    applySession,
    clearSession,
    markCompanyInitialized,
    loadSetupStatus,
    refresh,
    login,
    completeSetup,
    logout,
  }
})
