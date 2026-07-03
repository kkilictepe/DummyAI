import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useStickToBottom } from './useStickToBottom'

// jsdom has no layout, so drive the scroll math with a fake element carrying explicit
// scroll metrics.
function fakeContainer(
  metrics: Partial<{ scrollHeight: number; scrollTop: number; clientHeight: number }>,
  onScrollTo?: (opts: ScrollToOptions) => void,
): HTMLDivElement {
  return {
    scrollHeight: 0,
    scrollTop: 0,
    clientHeight: 0,
    ...metrics,
    scrollTo: (opts: ScrollToOptions) => onScrollTo?.(opts),
  } as unknown as HTMLDivElement
}

describe('useStickToBottom', () => {
  it('stays pinned when the user is near the bottom', () => {
    const { result } = renderHook(() => useStickToBottom(0))
    act(() => {
      result.current.containerRef.current = fakeContainer({
        scrollHeight: 1000,
        scrollTop: 970,
        clientHeight: 40,
      }) // distance = -10 -> pinned
    })
    act(() => result.current.onScroll())
    expect(result.current.isPinned).toBe(true)
  })

  it('unpins when the user scrolls up past the threshold', () => {
    const { result } = renderHook(() => useStickToBottom(0))
    act(() => {
      result.current.containerRef.current = fakeContainer({
        scrollHeight: 1000,
        scrollTop: 500,
        clientHeight: 400,
      }) // distance = 100 > 48
    })
    act(() => result.current.onScroll())
    expect(result.current.isPinned).toBe(false)
  })

  it('scrollToBottom scrolls the container to its full height', () => {
    let scrolledTo: ScrollToOptions | undefined
    const { result } = renderHook(() => useStickToBottom(0))
    act(() => {
      result.current.containerRef.current = fakeContainer({ scrollHeight: 1234 }, (o) => {
        scrolledTo = o
      })
    })
    act(() => result.current.scrollToBottom())
    expect(scrolledTo).toEqual({ top: 1234 })
  })
})
