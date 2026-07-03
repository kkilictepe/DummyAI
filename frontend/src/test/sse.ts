// Helpers to build AG-UI SSE responses for tests.
//
// Wire format (see backend/src/agui/runner.py + tests/agui/test_copilot_endpoint_sse.py):
// each event is one `data: <json>\n\n` frame; the event type lives in the JSON `type` field.
// The SDK reads response.body via getReader() and picks the SSE parser for any content-type
// that is not the AG-UI protobuf media type, so `text/event-stream` is correct.

const SSE_HEADERS: HeadersInit = {
  'Content-Type': 'text/event-stream; charset=utf-8',
  'Cache-Control': 'no-cache',
}

export function sseFrame(event: unknown): string {
  return `data: ${JSON.stringify(event)}\n\n`
}

/** Whole-body SSE response: every frame encoded up front. Use for end-state assertions. */
export function sseResponse(events: readonly unknown[]): Response {
  return new Response(events.map(sseFrame).join(''), { headers: SSE_HEADERS })
}

/** A response whose body errors immediately — models a transport failure. */
export function networkErrorResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error('network error'))
    },
  })
  return new Response(stream, { headers: SSE_HEADERS })
}

export interface ControlledSse {
  /** Pass to an MSW resolver (or an injected fetch) as the streamed response. */
  readonly response: Response
  /** Enqueue one well-formed AG-UI event frame. */
  push: (event: unknown) => void
  /** Enqueue a raw string chunk (for partial-frame / malformed-stream tests). */
  raw: (chunk: string) => void
  /** Close the stream cleanly (end of run). */
  close: () => void
  /** Abort the stream with an error (mid-run transport failure). */
  error: (err?: unknown) => void
}

/**
 * A controllable SSE stream: push frames over time, then close (or error). Use for
 * mid-run tests (streaming phases, stop/abort, RUN_ERROR mid-message). The returned
 * `response` never completes until `close()`/`error()` is called.
 */
export function controlledSse(): ControlledSse {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  return {
    response: new Response(stream, { headers: SSE_HEADERS }),
    push: (event) => controller.enqueue(encoder.encode(sseFrame(event))),
    raw: (chunk) => controller.enqueue(encoder.encode(chunk)),
    close: () => controller.close(),
    error: (err) => controller.error(err),
  }
}
