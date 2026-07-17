import { useEffect, useRef, useState } from 'react'
import { Mic, MicOff, PhoneOff, ShieldAlert, Sparkles } from 'lucide-react'
import { AgentAudioPlayer } from '@/lib/interview/audioPlayback'
import { CheatingDetector } from '@/lib/interview/cheatingDetection'
import { InterviewSocket, type ServerMessage } from '@/lib/interview/interviewSocket'
import { startMicCapture, type MicCapture } from '@/lib/interview/micCapture'
import { Button } from '@/components/ui/button'
import { AgentOrb, type OrbState } from './AgentOrb'

interface TranscriptEntry {
  speaker: 'agent' | 'candidate'
  text: string
  partial: boolean
}

interface IntegrityFlag {
  kind: string
  detail: string
  at: string
}

function flagLabel(kind: string): string {
  switch (kind) {
    case 'multiple_faces':
      return 'Multiple people'
    case 'no_face':
      return 'Not visible'
    case 'looking_away':
      return 'Looking away'
    case 'head_turned':
      return 'Head turned'
    case 'multiple_voices':
      return 'Multiple voices'
    default:
      return kind
  }
}

function formatClock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60)
  const s = Math.floor(totalSeconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function MeetingRoom({ token, onEnded }: { token: string; onEnded: (reason: string) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const playerRef = useRef<AgentAudioPlayer | null>(null)
  const socketRef = useRef<InterviewSocket | null>(null)
  const mutedRef = useRef(false)
  const hasEndedRef = useRef(false)
  const finishRef = useRef<((reason: string) => void) | null>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const detectorRef = useRef<CheatingDetector | null>(null)

  const [ready, setReady] = useState(false)
  const [mediaError, setMediaError] = useState<string | null>(null)
  const [muted, setMuted] = useState(false)
  const [orbState, setOrbState] = useState<OrbState>('idle')
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([])
  const [elapsed, setElapsed] = useState(0)
  const [remaining, setRemaining] = useState(0)
  // Enabled only once the agent decides the interview is over (server sends interview_concluded).
  // There is no automatic end — the candidate hangs up with the End call button below.
  const [canEndCall, setCanEndCall] = useState(false)
  // Integrity/proctoring flags raised during the interview — shown live on the right and kept.
  const [integrityFlags, setIntegrityFlags] = useState<IntegrityFlag[]>([])

  useEffect(() => {
    let cancelled = false

    async function setup() {
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: true,
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
      } catch {
        if (!cancelled) setMediaError('Camera/microphone access was denied or unavailable.')
        return
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream

      const player = new AgentAudioPlayer()
      playerRef.current = player

      function upsertCandidatePartial(text: string, final: boolean) {
        setTranscript((prev) => {
          const last = prev[prev.length - 1]
          if (last && last.speaker === 'candidate' && last.partial) {
            return [...prev.slice(0, -1), { speaker: 'candidate', text, partial: !final }]
          }
          return [...prev, { speaker: 'candidate', text, partial: !final }]
        })
      }

      const joinedAt = performance.now()
      function addFlag(kind: string, detail: string, atSeconds?: number | null) {
        const secs = atSeconds != null ? atSeconds : (performance.now() - joinedAt) / 1000
        setIntegrityFlags((prev) =>
          prev.length >= 100 ? prev : [...prev, { kind, detail, at: formatClock(secs) }],
        )
      }

      function handleMessage(msg: ServerMessage) {
        switch (msg.type) {
          case 'welcome':
            setReady(true)
            break
          case 'agent_speaking_start':
            setOrbState('speaking')
            break
          case 'agent_speaking_end':
            setOrbState('listening')
            break
          case 'agent_interrupted':
            player.clear()
            setOrbState('interrupted')
            setTimeout(() => setOrbState((s) => (s === 'interrupted' ? 'listening' : s)), 450)
            break
          case 'agent_transcript':
            setTranscript((prev) => [...prev, { speaker: 'agent', text: msg.text, partial: false }])
            break
          case 'candidate_speaking_start':
            setOrbState((s) => (s === 'speaking' ? s : 'listening'))
            break
          case 'candidate_transcript_partial':
            upsertCandidatePartial(msg.text, false)
            break
          case 'candidate_transcript_final':
            upsertCandidatePartial(msg.text, true)
            setOrbState('thinking')
            break
          case 'timer_update':
            setElapsed(msg.elapsed_seconds)
            setRemaining(msg.remaining_seconds)
            break
          case 'interview_concluded':
            // Agent finished its goodbye. Enable the End call button; keep the session open
            // until the candidate actually hangs up.
            setCanEndCall(true)
            setOrbState('idle')
            break
          case 'cheating_flag':
            // Server-detected integrity signal (multiple voices) — show it in the panel.
            addFlag(msg.kind, msg.detail ?? flagLabel(msg.kind), msg.at_seconds)
            break
          case 'interview_ended':
            finish(msg.reason)
            break
          case 'error':
            setMediaError(msg.message)
            break
        }
      }

      function finish(reason: string) {
        if (hasEndedRef.current) return
        hasEndedRef.current = true
        cleanup()
        onEnded(reason)
      }
      // Exposed so the component-level End call handler (outside this effect closure) can end
      // the session using the same guarded cleanup path.
      finishRef.current = finish

      const socket = new InterviewSocket(token, {
        onMessage: handleMessage,
        onAudio: (chunk) => player.enqueue(chunk),
        onClose: () => {
          // Normally interview_ended (above) already finished things off before the socket
          // actually closes. If the connection drops any other way — network hiccup, server
          // restart, crash — this is the fallback that guarantees the candidate is never left
          // staring at a dead meeting room instead of a clear "interview ended" screen.
          finish('disconnected')
        },
      })
      socketRef.current = socket

      const mic = await startMicCapture(stream, (chunk) => {
        if (!mutedRef.current) socket.sendAudio(chunk)
      })
      if (cancelled) {
        mic.stop()
        return
      }
      micRef.current = mic

      // Client-side video proctoring. Runs entirely locally; on any failure it self-disables
      // and the interview is unaffected. Each detected event is both shown live and sent to the
      // server for the recruiter review.
      if (videoRef.current) {
        const detector = new CheatingDetector(videoRef.current, ({ kind, detail }) => {
          addFlag(kind, detail)
          socket.sendCheatingEvent(kind, detail)
        })
        detectorRef.current = detector
        void detector.start()
      }
    }

    function cleanup() {
      detectorRef.current?.stop()
      detectorRef.current = null
      micRef.current?.stop()
      micRef.current = null
      playerRef.current?.close()
      playerRef.current = null
      socketRef.current?.close()
      socketRef.current = null
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      if (videoRef.current) videoRef.current.srcObject = null
    }

    void setup()
    return () => {
      cancelled = true
      cleanup()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  // Keep the captions pinned to the newest line as it streams in — the scrollbar itself is
  // hidden (see the no-scrollbar class), so this is the only way the latest text stays visible.
  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [transcript])

  function toggleMute() {
    const next = !muted
    mutedRef.current = next
    setMuted(next)
    socketRef.current?.setMuted(next)
    streamRef.current?.getAudioTracks().forEach((t) => (t.enabled = !next))
  }

  function handleEndCall() {
    if (!canEndCall) return
    socketRef.current?.endCall()
    finishRef.current?.('completed')
  }

  return (
    <div className="flex min-h-svh flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-white/10 px-6 py-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-primary" />
          AI Interview
        </div>
        <div className="rounded-full bg-white/10 px-3 py-1 text-sm tabular-nums">
          {formatClock(elapsed)} / {formatClock(elapsed + remaining)}
        </div>
      </header>

      <main className="relative flex flex-1 flex-col items-center justify-center gap-8 px-4 py-8">
        {integrityFlags.length > 0 && (
          <div className="absolute right-4 top-4 z-10 w-64 max-w-[70vw]">
            <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 shadow-lg backdrop-blur">
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-warning">
                <ShieldAlert className="h-4 w-4" />
                Integrity alerts ({integrityFlags.length})
              </div>
              <ul className="no-scrollbar max-h-[55vh] space-y-1.5 overflow-y-auto">
                {integrityFlags.map((f, i) => (
                  <li key={i} className="rounded-md bg-black/30 px-2.5 py-1.5 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-warning">{flagLabel(f.kind)}</span>
                      <span className="tabular-nums text-slate-400">{f.at}</span>
                    </div>
                    <p className="mt-0.5 text-slate-300">{f.detail}</p>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {mediaError ? (
          <p className="max-w-sm text-center text-sm text-destructive">{mediaError}</p>
        ) : (
          <>
            <AgentOrb
              state={orbState}
              micAnalyser={micRef.current?.analyser ?? null}
              agentAnalyser={playerRef.current?.analyser ?? null}
            />
            <p className="text-sm text-slate-400">
              {!ready
                ? 'Connecting…'
                : canEndCall
                  ? 'Interview complete — you can end the call whenever you’re ready.'
                  : orbState === 'speaking'
                    ? 'Agent is speaking'
                    : orbState === 'thinking'
                      ? 'Thinking…'
                      : orbState === 'interrupted'
                        ? 'Listening — go ahead'
                        : 'Listening'}
            </p>

            <div
              ref={transcriptRef}
              className="no-scrollbar w-full max-w-xl space-y-2 overflow-y-auto rounded-lg bg-white/5 p-4 text-sm"
              style={{ maxHeight: '30vh' }}
            >
              {transcript.length === 0 && <p className="text-slate-500">Live captions will appear here…</p>}
              {transcript.map((entry, i) => (
                <p key={i} className={entry.speaker === 'agent' ? 'text-primary-foreground' : 'text-slate-300'}>
                  <span className="font-medium">{entry.speaker === 'agent' ? 'Interviewer: ' : 'You: '}</span>
                  <span className={entry.partial ? 'opacity-60' : ''}>{entry.text}</span>
                </p>
              ))}
            </div>
          </>
        )}
      </main>

      <footer className="flex items-center justify-center gap-4 border-t border-white/10 px-6 py-4">
        <video ref={videoRef} autoPlay muted playsInline className="h-16 w-24 rounded-md object-cover" />
        <Button variant={muted ? 'destructive' : 'outline'} size="icon" onClick={toggleMute} className="rounded-full">
          {muted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </Button>
        <Button
          variant="destructive"
          onClick={handleEndCall}
          disabled={!canEndCall}
          className="gap-2 rounded-full"
          title={canEndCall ? 'End the call' : 'Available once the interview is complete'}
        >
          <PhoneOff className="h-4 w-4" />
          End call
        </Button>
      </footer>
    </div>
  )
}
