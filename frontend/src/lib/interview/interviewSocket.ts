export type ServerMessage =
  | { type: 'welcome' }
  | { type: 'agent_speaking_start' }
  | { type: 'agent_speaking_end' }
  | { type: 'agent_interrupted' }
  | { type: 'agent_transcript'; text: string }
  // English TTS runs in the browser: the server sends the sentence text to synthesize + play,
  // and the client acks with agent_sentence_done once it finishes playing (see pocketTts.ts).
  | { type: 'agent_say'; text: string; id: number }
  | { type: 'candidate_speaking_start' }
  | { type: 'candidate_transcript_partial'; text: string }
  | { type: 'candidate_transcript_final'; text: string }
  | { type: 'timer_update'; elapsed_seconds: number; remaining_seconds: number }
  // The agent decided the interview is over and finished its goodbye — the client should now
  // enable its "End call" button. The session is NOT torn down; the candidate hangs up.
  | { type: 'interview_concluded' }
  // A server-detected integrity flag (currently only "multiple voices" from the audio stream).
  | { type: 'cheating_flag'; kind: string; detail: string | null; at_seconds: number | null }
  | { type: 'interview_ended'; reason: string }
  | { type: 'error'; message: string }

function resolveWsUrl(token: string): string {
  const override = import.meta.env.VITE_AI_SERVICE_WS_URL
  if (override) return `${override.replace(/\/$/, '')}/interview/${token}`
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/ai/interview/${token}`
}

export class InterviewSocket {
  private ws: WebSocket

  constructor(
    token: string,
    handlers: {
      onMessage: (msg: ServerMessage) => void
      onAudio: (chunk: ArrayBuffer) => void
      onOpen?: () => void
      onClose?: () => void
    },
  ) {
    this.ws = new WebSocket(resolveWsUrl(token))
    this.ws.binaryType = 'arraybuffer'

    this.ws.onopen = () => handlers.onOpen?.()
    this.ws.onclose = () => handlers.onClose?.()
    this.ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        handlers.onMessage(JSON.parse(event.data) as ServerMessage)
      } else {
        handlers.onAudio(event.data as ArrayBuffer)
      }
    }
  }

  sendAudio(chunk: ArrayBuffer): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(chunk)
  }

  setMuted(muted: boolean): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: muted ? 'mic_muted' : 'mic_unmuted' }))
    }
  }

  endCall(): void {
    // Tell the server the candidate is hanging up so it finalizes (scores + closes) cleanly,
    // rather than the server only finding out via an abrupt socket drop.
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'end_call' }))
    }
  }

  sendAgentSentenceDone(id: number): void {
    // Tell the server the browser finished playing the agent sentence with this id, which
    // unblocks the server to send the next one (client-side Pocket TTS flow).
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'agent_sentence_done', id }))
    }
  }

  sendCheatingEvent(kind: string, detail: string): void {
    // Client-side (video) proctoring event, for the server to persist to the recruiter review.
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cheating_event', kind, detail }))
    }
  }

  close(): void {
    this.ws.close()
  }
}
