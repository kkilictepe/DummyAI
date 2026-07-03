import { brand, fontMono } from '../../../app/theme'

// One polite live region for the whole turn — announces "Copilot is thinking…" or the
// active tool label. No per-token announcements (that would spam screen readers).
export function TypingIndicator({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 2px',
        fontSize: 13,
        color: 'rgba(230, 237, 246, 0.65)',
      }}
    >
      <span style={{ display: 'inline-flex', gap: 3 }} aria-hidden>
        <Dot delay={0} />
        <Dot delay={160} />
        <Dot delay={320} />
      </span>
      <span style={{ fontFamily: fontMono, fontSize: 12 }}>{label}</span>
    </div>
  )
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      style={{
        width: 5,
        height: 5,
        borderRadius: '50%',
        background: brand.teal,
        opacity: 0.5,
        animation: 'rasyona-caret 1.2s ease-in-out infinite',
        animationDelay: `${delay}ms`,
      }}
    />
  )
}
