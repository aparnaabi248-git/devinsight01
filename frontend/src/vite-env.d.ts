/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL for the FastAPI backend. Defaults to the same-origin `/api` proxy. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
