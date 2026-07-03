import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MarkdownContent } from './MarkdownContent'

describe('MarkdownContent', () => {
  it('renders a GFM table', () => {
    render(<MarkdownContent>{'| System | State |\n|---|---|\n| KHP | ok |'}</MarkdownContent>)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('System')).toBeInTheDocument()
    expect(screen.getByText('KHP')).toBeInTheDocument()
  })

  it('does not render raw HTML as markup (no rehype-raw — XSS safe)', () => {
    const { container } = render(
      <MarkdownContent>{'<script>alert(1)</script>\n\nplain answer'}</MarkdownContent>,
    )
    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByText('plain answer')).toBeInTheDocument()
  })

  it('opens links in a new tab with noopener', () => {
    render(<MarkdownContent>{'[docs](https://example.com)'}</MarkdownContent>)
    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })
})
