/**
 * Type declarations for Vite-specific imports.
 *
 * Added in Session 203 Tier 2.1 so the `import VerifyChainWorker from
 * './verifyChainWorker.ts?worker'` pattern in `useBrowserVerifyFull.ts`
 * compiles. The full `vite/client` reference would also pull in types for
 * `import.meta.env`, hot-reload, etc.
 */
/// <reference types="vite/client" />
