/**
 * Idempotency-key lifecycle for one logical task submission (review finding R13).
 *
 * Why a key is not generated per click
 * ------------------------------------
 * The server creates a task (and its company/project holds) inside one
 * transaction and guarantees "same key + same body -> the same task". That
 * guarantee is only useful if the *client* reuses the key when it does not know
 * whether the request landed. A fresh key per click turns a lost response into a
 * second task and a second reservation.
 *
 * The rules implemented here:
 *
 * 1. A draft body and its key are created together. While the body is unchanged
 *    the key stays the same, so re-submitting after a timeout/network error is a
 *    retry of the *same* command, not a new one.
 * 2. Editing any field of the body discards the key: the command is genuinely
 *    different, and reusing the old key would be refused as an idempotency
 *    conflict (or, worse, would silently return the previous task).
 * 3. Only a confirmed success, or an explicit "start another task", clears the
 *    key. A failed request keeps it so the user can retry the same command.
 *
 * The body digest is computed from the exact payload that would be sent, sorted
 * by key, so field order and whitespace around the draft cannot cause a spurious
 * "body changed" reset.
 */

export interface TaskSubmissionBody {
  title: string
  objective: string
  acceptance: string | null
  budgetTokens: number
  demoBehavior: string
  maxAutoRetries: number
}

export interface TaskSubmissionKey {
  key: string
  digest: string
}

export function digestSubmission(body: TaskSubmissionBody): string {
  return JSON.stringify([
    body.title,
    body.objective,
    body.acceptance,
    body.budgetTokens,
    body.demoBehavior,
    body.maxAutoRetries,
  ])
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `task-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/**
 * Return the key to use for this exact body.
 *
 * ``current`` is the key from a previous attempt (if any). It is reused when the
 * body is identical, replaced when the body changed, and created when there is
 * none. The caller stores the returned value for the next attempt.
 */
export function idempotencyKeyFor(
  current: TaskSubmissionKey | null,
  body: TaskSubmissionBody,
): TaskSubmissionKey {
  const digest = digestSubmission(body)
  if (current !== null && current.digest === digest) {
    return current
  }
  return { key: newKey(), digest }
}

/** The submission landed (or the user explicitly started a new one). */
export function clearSubmissionKey(): null {
  return null
}
