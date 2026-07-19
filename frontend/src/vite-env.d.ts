/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_AI_SERVICE_WS_URL?: string
  /** Apex domain tenants are subdomains of (default "localhost" → acme.localhost). */
  readonly VITE_ROOT_DOMAIN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
