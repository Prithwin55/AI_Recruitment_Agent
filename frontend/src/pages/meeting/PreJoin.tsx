import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { AlertCircle, Clock, Mic, MicOff, Sparkles, Video, VideoOff } from 'lucide-react'
import {
  getPublicInterviewSession,
  setInterviewLanguage,
  startInterview,
  type InterviewLanguageCode,
  type PublicInterviewSession,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import MeetingRoom from './Room'

type ViewState =
  | { kind: 'loading' }
  | { kind: 'not_found' }
  | { kind: 'expired' }
  | { kind: 'already_active' }
  | { kind: 'completed' }
  | { kind: 'prejoin'; session: PublicInterviewSession }
  | { kind: 'started' }
  | { kind: 'ended'; reason: string }

const LANGUAGES: { code: InterviewLanguageCode; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ar-OM', label: 'Arabic (Omani)' },
]

export default function PreJoin() {
  const { token } = useParams<{ token: string }>()
  const [view, setView] = useState<ViewState>({ kind: 'loading' })
  const [mediaError, setMediaError] = useState<string | null>(null)
  const [micMuted, setMicMuted] = useState(false)
  const [joining, setJoining] = useState(false)
  const [joinError, setJoinError] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const startedLanguageRef = useRef<InterviewLanguageCode>('en')

  const loadSession = useCallback(async () => {
    if (!token) return
    try {
      const session = await getPublicInterviewSession(token)
      if (session.token_status === 'expired') setView({ kind: 'expired' })
      else if (session.token_status === 'completed') setView({ kind: 'completed' })
      else if (session.token_status === 'active') setView({ kind: 'already_active' })
      else setView({ kind: 'prejoin', session })
    } catch {
      setView({ kind: 'not_found' })
    }
  }, [token])

  useEffect(() => {
    void loadSession()
  }, [loadSession])

  useEffect(() => {
    if (view.kind !== 'prejoin') return

    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({
        video: true,
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) videoRef.current.srcObject = stream
        setMediaError(null)
      })
      .catch(() => {
        if (!cancelled) setMediaError('Camera/microphone access was denied or unavailable.')
      })

    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }, [view.kind])

  function toggleMic() {
    const stream = streamRef.current
    if (!stream) return
    const nextMuted = !micMuted
    stream.getAudioTracks().forEach((t) => (t.enabled = !nextMuted))
    setMicMuted(nextMuted)
  }

  async function handleLanguageChange(code: InterviewLanguageCode) {
    if (view.kind !== 'prejoin' || !token) return
    const updated = await setInterviewLanguage(token, code)
    setView({ kind: 'prejoin', session: updated })
  }

  async function handleJoin() {
    if (!token) return
    setJoining(true)
    setJoinError(null)
    try {
      const result = await startInterview(token)
      if (!result.success) {
        setJoinError(result.reason ?? 'Could not start the interview.')
        void loadSession()
        return
      }
      if (view.kind === 'prejoin') {
        // MeetingRoom acquires its own fresh camera/mic stream on mount (permission is already
        // granted, so this doesn't re-prompt) — simpler and less fragile than handing off this
        // preview stream's ownership across components.
        startedLanguageRef.current = view.session.language
        streamRef.current?.getTracks().forEach((t) => t.stop())
        setView({ kind: 'started' })
      }
    } catch {
      setJoinError('Could not start the interview. Please try again.')
    } finally {
      setJoining(false)
    }
  }

  if (view.kind === 'started' && token) {
    return (
      <MeetingRoom
        token={token}
        language={startedLanguageRef.current}
        onEnded={(reason) => setView({ kind: 'ended', reason })}
      />
    )
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4 py-10">
      <div className="w-full max-w-lg">
        <MeetingContent
          view={view}
          mediaError={mediaError}
          micMuted={micMuted}
          videoRef={videoRef}
          joining={joining}
          joinError={joinError}
          onToggleMic={toggleMic}
          onLanguageChange={handleLanguageChange}
          onJoin={handleJoin}
        />
      </div>
    </div>
  )
}

function MeetingContent({
  view,
  mediaError,
  micMuted,
  videoRef,
  joining,
  joinError,
  onToggleMic,
  onLanguageChange,
  onJoin,
}: {
  view: ViewState
  mediaError: string | null
  micMuted: boolean
  videoRef: React.RefObject<HTMLVideoElement | null>
  joining: boolean
  joinError: string | null
  onToggleMic: () => void
  onLanguageChange: (code: InterviewLanguageCode) => void
  onJoin: () => void
}) {
  if (view.kind === 'loading') {
    return <p className="text-center text-sm text-muted-foreground">Loading…</p>
  }

  if (view.kind === 'not_found') {
    return (
      <StatusCard
        icon={<AlertCircle className="h-8 w-8 text-destructive" />}
        title="Invalid interview link"
        description="This link doesn't match any scheduled interview. Double-check the link from your invitation email."
      />
    )
  }

  if (view.kind === 'expired') {
    return (
      <StatusCard
        icon={<Clock className="h-8 w-8 text-destructive" />}
        title="This interview link has expired"
        description="Please reach out to the recruiter who invited you to request a new link."
      />
    )
  }

  if (view.kind === 'already_active') {
    return (
      <StatusCard
        icon={<AlertCircle className="h-8 w-8 text-warning-foreground" />}
        title="This interview is already in progress"
        description="It looks like this interview was already started. If you were disconnected, contact the recruiter for help."
      />
    )
  }

  if (view.kind === 'completed') {
    return (
      <StatusCard
        icon={<Sparkles className="h-8 w-8 text-success" />}
        title="This interview has already been completed"
        description="Thanks for taking the time to interview — the recruiter will be in touch."
      />
    )
  }

  if (view.kind === 'started') {
    // Unreachable in practice — the top-level PreJoin component renders <MeetingRoom> directly
    // for this state before MeetingContent is ever invoked. Handled here only so the
    // exhaustiveness of the ViewState union type-checks.
    return null
  }

  if (view.kind === 'ended') {
    return (
      <StatusCard
        icon={<Sparkles className="h-8 w-8 text-success" />}
        title="Interview complete"
        description={
          view.reason === 'time_up'
            ? "Thanks for your time — we've covered everything for today. The recruiter will follow up with next steps."
            : 'Thanks for taking the time to interview — the recruiter will be in touch with next steps.'
        }
      />
    )
  }

  const { session } = view

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {session.candidate_first_name ? `Hi ${session.candidate_first_name},` : 'Welcome'} let's get you ready
        </CardTitle>
        <CardDescription>
          Interview for <strong className="text-foreground">{session.role_title}</strong> — about{' '}
          {session.duration_minutes} minutes.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-black/90">
          <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
          {mediaError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80 px-6 text-center text-sm text-white">
              <VideoOff className="h-6 w-6" />
              {mediaError}
            </div>
          )}
        </div>

        <div className="flex items-center justify-center gap-3">
          <Button variant="outline" size="icon" onClick={onToggleMic} disabled={!!mediaError}>
            {micMuted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          </Button>
          <span className="text-sm text-muted-foreground">{micMuted ? 'Mic muted' : 'Mic on'}</span>
          <span className="mx-2 h-4 w-px bg-border" />
          <Video className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Camera on</span>
        </div>

        <div>
          <p className="mb-2 text-sm font-medium text-foreground">Interview language</p>
          <div className="flex gap-2">
            {LANGUAGES.map((lang) => (
              <button
                key={lang.code}
                type="button"
                onClick={() => onLanguageChange(lang.code)}
                className={cn(
                  'flex-1 rounded-md border px-3 py-2 text-sm font-medium transition-colors',
                  session.language === lang.code
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-input text-muted-foreground hover:bg-accent',
                )}
              >
                {lang.label}
              </button>
            ))}
          </div>
        </div>

        {joinError && <p className="text-sm text-destructive">{joinError}</p>}

        <Button size="lg" disabled={!!mediaError || joining} onClick={onJoin}>
          {joining ? 'Joining…' : 'Join interview'}
        </Button>
      </CardContent>
    </Card>
  )
}

function StatusCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode
  title: string
  description: string
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        {icon}
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}
