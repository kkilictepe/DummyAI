import { useCallback, useEffect, useRef, useState } from 'react'

// Keep the newest message in view while the user is at the bottom, but stop auto-
// scrolling the moment they scroll up to read history. Scroll math lives here (not in
// the component) because jsdom has no layout — tests drive it via defined metrics.

const PIN_THRESHOLD_PX = 48

export interface StickToBottom {
  containerRef: React.RefObject<HTMLDivElement | null>
  isPinned: boolean
  scrollToBottom: () => void
  onScroll: () => void
}

export function useStickToBottom(watched: unknown): StickToBottom {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [isPinned, setIsPinned] = useState(true)

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current
    if (el) el.scrollTo({ top: el.scrollHeight })
  }, [])

  const onScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setIsPinned(distanceFromBottom <= PIN_THRESHOLD_PX)
  }, [])

  useEffect(() => {
    if (isPinned) scrollToBottom()
  }, [watched, isPinned, scrollToBottom])

  return { containerRef, isPinned, scrollToBottom, onScroll }
}
