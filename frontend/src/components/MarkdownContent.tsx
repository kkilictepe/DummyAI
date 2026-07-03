import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Shared markdown renderer (future flows render markdown too). GFM tables/strikethrough
// enabled; NO rehype-raw, so any raw HTML in model output renders as escaped text
// (XSS-safe). Links open in a new tab with noopener; tables scroll horizontally.
const components: Components = {
  a({ node: _node, ...props }) {
    return <a {...props} target="_blank" rel="noopener noreferrer" />
  },
  table({ node: _node, ...props }) {
    return (
      <div className="md-table-scroll">
        <table {...props} />
      </div>
    )
  },
}

export function MarkdownContent({ children }: { children: string }) {
  return (
    <div className="md-content">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </Markdown>
    </div>
  )
}
