/** Keep transport independent of Pinia/router; stores own their subscription. */
const listeners = new Set<() => void>()
let sessionGeneration = 0

/** Browser-local response ownership, never a server permission or credential. */
export function getSessionGeneration(): number {
  return sessionGeneration
}

export function advanceSessionGeneration(): number {
  return ++sessionGeneration
}

export function onAuthFailure(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function notifyAuthFailure(requestGeneration: number) {
  if (requestGeneration !== sessionGeneration) return
  for (const listener of listeners) listener()
}
