/// <reference types="vite/client" />

// The two env vars the app reads (only ever through src/lib/env.ts).
interface ImportMetaEnv {
  readonly VITE_AGUI_BASE_URL?: string
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
