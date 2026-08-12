/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATA_SOURCE?: 'mock'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
