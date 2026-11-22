/**
 * Native ResizeObserver shim.
 *
 * Why this exists
 * ---------------
 * antd -> rc-resize-observer imports `resize-observer-polyfill`, which costs
 * ~33 KB of the app's initial critical path. That polyfill only exists for
 * browsers without a native ResizeObserver — but Vite's default build target
 * ("modules") is Chrome 87+ / Edge 88+ / Firefox 78+ / Safari 14+, and native
 * ResizeObserver landed in Chrome 64 / Firefox 69 / Safari 13.1. So for every
 * browser this project actually targets, the polyfill is dead weight.
 *
 * `vite.config.ts` aliases the package name to this module, so
 * `rc-resize-observer` keeps working unchanged:
 *
 *     const resizeObserver = new ResizeObserver(onResize)   // module scope
 *     resizeObserver.observe(el) / .unobserve(el)
 *
 * The callback receives an array of entries carrying `.target`, which matches
 * what rc-resize-observer expects.
 *
 * The no-op fallback keeps module-scope construction from throwing in a
 * non-browser context (SSR / prerender). It degrades to "no resize callbacks"
 * rather than crashing the app.
 */
type ResizeCallback = (entries: { target: Element }[], observer: unknown) => void

class InertResizeObserver {
  constructor(_cb: ResizeCallback) {}
  observe(_target: Element): void {}
  unobserve(_target: Element): void {}
  disconnect(): void {}
}

const NativeResizeObserver =
  typeof window !== 'undefined'
    ? (window as unknown as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver
    : undefined

export default (NativeResizeObserver ?? InertResizeObserver)
