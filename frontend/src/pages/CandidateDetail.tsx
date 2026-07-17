import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Download, FileWarning, ShieldAlert } from 'lucide-react'
import { CHEATING_FLAG_LABELS, fetchResumeBlob, getCandidate } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import {
  Phase1DecisionBadge,
  Phase2StatusBadge,
  ProcessingStatusBadge,
} from '@/components/StatusBadges'
import { cn } from '@/lib/utils'

const PREVIEWABLE_EXTENSIONS = new Set(['.pdf', '.png', '.jpg', '.jpeg'])

function extensionOf(filename: string): string {
  const idx = filename.lastIndexOf('.')
  return idx === -1 ? '' : filename.slice(idx).toLowerCase()
}

export default function CandidateDetail() {
  const { id } = useParams<{ id: string }>()
  const candidateId = id!
  const [resumeUrl, setResumeUrl] = useState<string | null>(null)
  const [resumeError, setResumeError] = useState(false)

  const { data: candidate, isLoading } = useQuery({
    queryKey: ['candidates', candidateId],
    queryFn: () => getCandidate(candidateId),
  })

  useEffect(() => {
    let objectUrl: string | null = null
    fetchResumeBlob(candidateId)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob)
        setResumeUrl(objectUrl)
      })
      .catch(() => setResumeError(true))
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [candidateId])

  const extension = useMemo(
    () => (candidate ? extensionOf(candidate.original_filename) : ''),
    [candidate],
  )

  if (isLoading || !candidate) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  const session = candidate.interview_sessions[0]

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          to={`/recruitments/${candidate.recruitment_id}`}
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to recruitment
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              {candidate.name ?? candidate.original_filename}
            </h1>
            <p className="text-sm text-muted-foreground">
              {candidate.email ?? 'No email'} {candidate.phone ? `· ${candidate.phone}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <ProcessingStatusBadge status={candidate.processing_status} />
            <Phase1DecisionBadge decision={candidate.phase1_decision} />
            <Phase2StatusBadge status={candidate.phase2_status} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Resume</CardTitle>
            <CardDescription>{candidate.original_filename}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {resumeError ? (
              <div className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
                <FileWarning className="h-4 w-4 shrink-0" />
                Could not load the resume file.
              </div>
            ) : resumeUrl && PREVIEWABLE_EXTENSIONS.has(extension) ? (
              extension === '.pdf' ? (
                <iframe src={resumeUrl} title="Resume preview" className="h-[520px] w-full rounded-md border border-border" />
              ) : (
                <img src={resumeUrl} alt="Resume" className="max-h-[520px] w-full rounded-md border border-border object-contain" />
              )
            ) : (
              <p className="text-sm text-muted-foreground">Preview isn't available for this file type — download to view.</p>
            )}
            {resumeUrl && (
              <a
                href={resumeUrl}
                download={candidate.original_filename}
                className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'w-fit gap-1.5')}
              >
                <Download className="h-4 w-4" />
                Download resume
              </a>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Phase 1 — Resume screening</CardTitle>
            <CardDescription>
              {candidate.phase1_score !== null ? `Match score: ${Math.round(candidate.phase1_score)}/100` : 'Not yet scored'}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 text-sm">
            {candidate.processing_error && (
              <p className="text-destructive">{candidate.processing_error}</p>
            )}
            {candidate.phase1_rationale && <p className="text-foreground">{candidate.phase1_rationale}</p>}
            {candidate.parsed_profile?.summary && (
              <p className="text-muted-foreground">{candidate.parsed_profile.summary}</p>
            )}
            {candidate.parsed_profile?.skills && candidate.parsed_profile.skills.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {candidate.parsed_profile.skills.map((s) => (
                  <Badge key={s} variant="secondary">
                    {s}
                  </Badge>
                ))}
              </div>
            )}
            {candidate.phase1_strengths && candidate.phase1_strengths.length > 0 && (
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-success">Strengths</p>
                <ul className="mt-1 list-inside list-disc text-muted-foreground">
                  {candidate.phase1_strengths.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {candidate.phase1_gaps && candidate.phase1_gaps.length > 0 && (
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-warning-foreground">Gaps</p>
                <ul className="mt-1 list-inside list-disc text-muted-foreground">
                  {candidate.phase1_gaps.map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Phase 2 — AI interview</CardTitle>
          <CardDescription>
            {session
              ? `${session.language === 'ar-OM' ? 'Arabic (Omani)' : 'English'} · ${session.duration_minutes} min`
              : 'No interview on record yet.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {!session && <p className="text-sm text-muted-foreground">This candidate hasn't completed an interview yet.</p>}

          {session && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                {session.final_score !== null && (
                  <span className="text-2xl font-semibold text-primary">{Math.round(session.final_score)}</span>
                )}
                {session.decision && (
                  <Badge variant={session.decision === 'shortlist' ? 'success' : 'destructive'} className="text-sm">
                    {session.decision === 'shortlist' ? 'Shortlisted (final hire)' : 'Rejected'}
                  </Badge>
                )}
                {session.sentiment_summary && (
                  <span className="text-xs text-muted-foreground">
                    Sentiment: {session.sentiment_summary.positive_count} positive ·{' '}
                    {session.sentiment_summary.neutral_count} neutral · {session.sentiment_summary.negative_count} negative
                    {' '}(avg {session.sentiment_summary.average_score.toFixed(2)})
                  </span>
                )}
              </div>

              {(session.ability_score !== null || session.confidence_score !== null) && (
                <div className="grid grid-cols-2 gap-3 sm:max-w-xs">
                  {session.ability_score !== null && (
                    <div className="rounded-lg border border-border bg-muted/40 p-3">
                      <p className="text-xs text-muted-foreground">Ability</p>
                      <p className="text-xl font-semibold tabular-nums text-foreground">
                        {Math.round(session.ability_score)}
                      </p>
                    </div>
                  )}
                  {session.confidence_score !== null && (
                    <div className="rounded-lg border border-border bg-muted/40 p-3">
                      <p className="text-xs text-muted-foreground">Confidence</p>
                      <p className="text-xl font-semibold tabular-nums text-foreground">
                        {Math.round(session.confidence_score)}
                      </p>
                    </div>
                  )}
                </div>
              )}

              {session.summary && <p className="text-sm text-foreground">{session.summary}</p>}

              {(session.strengths?.length || session.weaknesses?.length) && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {session.strengths && session.strengths.length > 0 && (
                    <div>
                      <p className="text-xs font-medium uppercase tracking-wide text-success">Strengths</p>
                      <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
                        {session.strengths.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {session.weaknesses && session.weaknesses.length > 0 && (
                    <div>
                      <p className="text-xs font-medium uppercase tracking-wide text-warning-foreground">Weaknesses</p>
                      <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
                        {session.weaknesses.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {session.rationale ? (
                <div>
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Why the AI made this decision
                  </p>
                  <p className="text-sm text-foreground">{session.rationale}</p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Scoring hasn't finished yet — check back shortly.</p>
              )}

              {session.cheating_flags.length > 0 && (
                <div>
                  <p className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-warning">
                    <ShieldAlert className="h-3.5 w-3.5" />
                    Integrity flags ({session.cheating_flags.length})
                  </p>
                  <p className="mb-2 text-xs text-muted-foreground">
                    Raised during the interview. Informational only — these do not affect the AI's score or decision.
                  </p>
                  <ul className="space-y-1.5">
                    {session.cheating_flags.map((f, i) => (
                      <li
                        key={i}
                        className="flex items-start justify-between gap-3 rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-sm"
                      >
                        <div>
                          <span className="font-medium text-foreground">{CHEATING_FLAG_LABELS[f.kind] ?? f.kind}</span>
                          {f.detail && <p className="text-xs text-muted-foreground">{f.detail}</p>}
                        </div>
                        {f.at_seconds != null && (
                          <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                            {Math.floor(f.at_seconds / 60)}:{String(Math.floor(f.at_seconds % 60)).padStart(2, '0')}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {session.transcript_turns.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Transcript</p>
                  <div className="max-h-96 space-y-2 overflow-y-auto rounded-md bg-muted/50 p-4 text-sm">
                    {session.transcript_turns.map((turn, i) => (
                      <p key={i}>
                        <span className="font-medium">{turn.speaker === 'agent' ? 'Interviewer: ' : 'Candidate: '}</span>
                        <span className="text-foreground">{turn.text}</span>
                        {turn.cut_off && <span className="text-muted-foreground"> [cut off]</span>}
                        {turn.sentiment_label && turn.speaker === 'candidate' && (
                          <Badge
                            variant={
                              turn.sentiment_label === 'positive'
                                ? 'success'
                                : turn.sentiment_label === 'negative'
                                  ? 'destructive'
                                  : 'outline'
                            }
                            className="ml-2 align-middle text-[10px]"
                          >
                            {turn.sentiment_label}
                          </Badge>
                        )}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
