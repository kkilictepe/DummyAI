// View-model types for the Copilot chat, plus the tool-name → friendly-label map.

export type ToolPhase = 'streaming-args' | 'executing' | 'done'

export interface ToolRunStatus {
  toolCallId: string
  toolName: string
  phase: ToolPhase
}

// Friendly present-tense labels for the 10 tools bound to the Copilot agent
// (backend src/tools/__init__.py). Shown while a tool runs; fallback covers any
// future tool the UI hasn't been taught yet.
export const TOOL_LABELS: Record<string, string> = {
  tool_sap_metric_categorized: 'Reading SAP metrics',
  metric_lookup: 'Looking up a metric',
  prometheus_metrics_advance_query: 'Querying Prometheus',
  list_sap_systems: 'Listing SAP systems',
  es_field_search: 'Searching logs',
  es_aggregation: 'Aggregating logs',
  es_compare_windows: 'Comparing log windows',
  es_drilldown_around: 'Drilling into logs',
  es_cluster_errors: 'Clustering log errors',
  es_raw_query: 'Running a log query',
}

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? `Running ${name}`
}

/** A single tool call paired with its result, for the activity timeline. */
export interface ToolActivityVM {
  toolCallId: string
  toolName: string
  label: string
  phase: ToolPhase
  /** Raw JSON args string from the assistant tool call. */
  args: string
  /** Raw JSON result string from the tool message (undefined until it returns). */
  result?: string
}

export type ChatItem =
  | { kind: 'user'; id: string; content: string }
  | {
      kind: 'assistant'
      id: string
      content: string
      toolActivities: ToolActivityVM[]
      isStreaming: boolean
      isPartial: boolean
    }
