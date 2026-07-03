import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import { server } from './test/server'

// --- jsdom gaps that AntD and our components rely on ---

// matchMedia: jsdom doesn't implement it; AntD calls it unconditionally (Sider
// `breakpoint`, responsive tokens). Non-matching stub with modern + legacy APIs.
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {}, // legacy pair for older antd 5.x code paths
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// ResizeObserver: not in jsdom; used by AntD layout primitives.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof globalThis.ResizeObserver
}

// scrollTo: jsdom logs "Not implemented"; our stick-to-bottom logic calls it.
const noopScroll = (): void => {}
Element.prototype.scrollTo = noopScroll as unknown as Element['scrollTo']
window.scrollTo = noopScroll as unknown as typeof window.scrollTo

// clipboard: the copy-raw-markdown button uses it.
if (!navigator.clipboard) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: vi.fn(() => Promise.resolve()),
      readText: vi.fn(() => Promise.resolve('')),
    },
  })
}

// --- MSW lifecycle (unhandled requests fail loudly) ---
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
