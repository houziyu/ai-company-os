import { fileURLToPath } from 'node:url'
import base from '../../../../frontend/vitest.config'

export default {
  ...base,
  root: fileURLToPath(new URL('../../../../frontend/', import.meta.url)),
  test: {
    ...base.test,
    include: [fileURLToPath(new URL('./ui-audit.spec.ts', import.meta.url)).replaceAll('\\', '/')],
  },
}
