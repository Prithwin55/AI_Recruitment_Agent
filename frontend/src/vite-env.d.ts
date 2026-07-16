/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_AI_SERVICE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
