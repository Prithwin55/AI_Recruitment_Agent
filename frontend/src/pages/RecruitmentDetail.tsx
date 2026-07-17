import { useRef, useState, type DragEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, ChevronDown, ChevronUp, ShieldAlert, UploadCloud } from 'lucide-react'
import {
  bulkUploadResumes,
  CHEATING_FLAG_LABELS,
  getRecruitment,
  listCandidates,
  scheduleInterviews,
  updateCandidateDecision,
  type Candidate,
} from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import {
  Phase1DecisionBadge,
  Phase2StatusBadge,
  ProcessingStatusBadge,
} from '@/components/StatusBadges'

const ACTIVE_STATUSES = new Set(['queued', 'processing'])

export default function RecruitmentDetail() {
  const { id } = useParams<{ id: string }>()
  const recruitmentId = id!
  const queryClient = useQueryClient()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: recruitment } = useQuery({
    queryKey: ['recruitments', recruitmentId],
    queryFn: () => getRecruitment(recruitmentId),
    refetchInterval: 5000,
  })

  const { data: candidates } = useQuery({
    queryKey: ['recruitments', recruitmentId, 'candidates'],
    queryFn: () => listCandidates(recruitmentId),
    refetchInterval: (query) => {
      const list = query.state.data as Candidate[] | undefined
      return list?.some((c) => ACTIVE_STATUSES.has(c.processing_status)) ? 3000 : false
    },
  })

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => bulkUploadResumes(recruitmentId, files),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId] })
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId, 'candidates'] })
    },
  })

  const decisionMutation = useMutation({
    mutationFn: ({ candidateId, decision }: { candidateId: string; decision: 'advance' | 'hold' | 'reject' }) =>
      updateCandidateDecision(candidateId, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId] })
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId, 'candidates'] })
    },
  })

  const scheduleMutation = useMutation({
    mutationFn: () => scheduleInterviews(recruitmentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId] })
      queryClient.invalidateQueries({ queryKey: ['recruitments', recruitmentId, 'candidates'] })
    },
  })

  const readyToSchedule =
    candidates?.filter((c) => c.phase1_decision === 'advance' && c.phase2_status === 'not_scheduled') ?? []

  function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    uploadMutation.mutate(Array.from(fileList))
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  if (!recruitment) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{recruitment.title}</h1>
        <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{recruitment.jd_text}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <StatTile label="Candidates" value={recruitment.counts.total_candidates} />
        <StatTile label="Queued" value={recruitment.counts.queued} />
        <StatTile label="Processing" value={recruitment.counts.processing} />
        <StatTile label="Scored" value={recruitment.counts.scored} />
        <StatTile label="Advancing" value={recruitment.counts.advanced} />
        <StatTile label="Failed" value={recruitment.counts.failed} tone={recruitment.counts.failed > 0 ? 'bad' : undefined} />
      </div>

      {readyToSchedule.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Ready to interview</CardTitle>
            <CardDescription>
              {readyToSchedule.length} candidate{readyToSchedule.length === 1 ? '' : 's'} marked to advance —
              send them a one-time interview link by email (plus a calendar invite, if configured).
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Button
              className="w-fit gap-1.5"
              disabled={scheduleMutation.isPending}
              onClick={() => scheduleMutation.mutate()}
            >
              <CalendarClock className="h-4 w-4" />
              {scheduleMutation.isPending
                ? 'Sending…'
                : `Schedule interview${readyToSchedule.length === 1 ? '' : 's'} for ${readyToSchedule.length}`}
            </Button>

            {scheduleMutation.data && (
              <div className="flex flex-col gap-1 text-sm">
                {scheduleMutation.data.scheduled.length > 0 && (
                  <p className="text-success">
                    Sent to {scheduleMutation.data.scheduled.map((s) => s.name ?? s.email).join(', ')}.
                  </p>
                )}
                {scheduleMutation.data.skipped_no_email.length > 0 && (
                  <p className="text-warning-foreground">
                    Skipped (no email on file):{' '}
                    {scheduleMutation.data.skipped_no_email.map((s) => s.name ?? s.candidate_id).join(', ')}.
                  </p>
                )}
                {scheduleMutation.data.failed.length > 0 && (
                  <div className="text-destructive">
                    <p>Failed to send:</p>
                    <ul className="list-inside list-disc">
                      {scheduleMutation.data.failed.map((f) => (
                        <li key={f.candidate_id}>
                          {f.name ?? f.candidate_id} — {f.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {scheduleMutation.isError && (
              <p className="text-sm text-destructive">Something went wrong sending interview invites.</p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Upload resumes</CardTitle>
          <CardDescription>PDF, DOCX, JPG, or PNG — up to 15 MB each. Scoring starts automatically.</CardDescription>
        </CardHeader>
        <CardContent>
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-input px-6 py-10 text-center transition-colors',
              dragOver && 'border-primary bg-accent',
            )}
          >
            <UploadCloud className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-foreground">
              <span className="font-medium text-primary">Click to browse</span> or drag and drop resumes here
            </p>
            <p className="text-xs text-muted-foreground">You can select multiple files at once.</p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.png,.jpg,.jpeg"
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>

          {uploadMutation.isPending && (
            <p className="mt-3 text-sm text-muted-foreground">Uploading…</p>
          )}
          {uploadMutation.data && uploadMutation.data.rejected.length > 0 && (
            <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <p className="font-medium">Some files were rejected:</p>
              <ul className="mt-1 list-inside list-disc">
                {uploadMutation.data.rejected.map((r) => (
                  <li key={r.filename}>
                    {r.filename} — {r.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Candidates</CardTitle>
          <CardDescription>Ranked by AI match score. Highest first.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {!candidates || candidates.length === 0 ? (
            <p className="px-6 pb-6 text-sm text-muted-foreground">No resumes uploaded yet.</p>
          ) : (
            <div className="divide-y divide-border">
              {candidates.map((candidate) => (
                <CandidateRow
                  key={candidate.id}
                  candidate={candidate}
                  expanded={expandedId === candidate.id}
                  onToggle={() => setExpandedId(expandedId === candidate.id ? null : candidate.id)}
                  onDecide={(decision) => decisionMutation.mutate({ candidateId: candidate.id, decision })}
                  deciding={decisionMutation.isPending}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatTile({ label, value, tone }: { label: string; value: number; tone?: 'bad' }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn('text-xl font-semibold tabular-nums', tone === 'bad' && value > 0 && 'text-destructive')}>
        {value}
      </p>
    </div>
  )
}

function CandidateRow({
  candidate,
  expanded,
  onToggle,
  onDecide,
  deciding,
}: {
  candidate: Candidate
  expanded: boolean
  onToggle: () => void
  onDecide: (decision: 'advance' | 'hold' | 'reject') => void
  deciding: boolean
}) {
  const canDecide = candidate.processing_status === 'scored'
  const cheatingCount = candidate.interview_cheating_flags?.length ?? 0
  const hasCheatingFlags = cheatingCount > 0

  return (
    <div className="px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link to={`/candidates/${candidate.id}`} className="truncate font-medium text-foreground hover:text-primary hover:underline">
              {candidate.name ?? candidate.original_filename}
            </Link>
            {candidate.phase1_score !== null && (
              <span className="tabular-nums text-sm font-semibold text-primary">
                {Math.round(candidate.phase1_score)}
              </span>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {candidate.email ?? candidate.original_filename}
            {candidate.processing_error && (
              <span className="text-destructive"> — {candidate.processing_error}</span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <ProcessingStatusBadge status={candidate.processing_status} />
          <Phase1DecisionBadge decision={candidate.phase1_decision} />
          <Phase2StatusBadge status={candidate.phase2_status} />
          {candidate.interview_decision && (
            <Badge variant={candidate.interview_decision === 'shortlist' ? 'success' : 'destructive'}>
              {candidate.interview_decision === 'shortlist' ? 'Shortlisted' : 'Rejected'}
              {candidate.interview_score !== null && ` · ${Math.round(candidate.interview_score)}`}
            </Badge>
          )}
          {hasCheatingFlags && (
            <Badge
              variant="outline"
              className="gap-1 border-warning/50 text-warning"
              title={`${cheatingCount} integrity flag${cheatingCount > 1 ? 's' : ''} raised during the interview`}
            >
              <ShieldAlert className="h-3 w-3" />
              {cheatingCount}
            </Badge>
          )}

          {canDecide && (
            <div className="flex gap-1">
              <Button size="sm" variant="outline" disabled={deciding} onClick={() => onDecide('advance')}>
                Advance
              </Button>
              <Button size="sm" variant="ghost" disabled={deciding} onClick={() => onDecide('hold')}>
                Hold
              </Button>
              <Button size="sm" variant="ghost" disabled={deciding} onClick={() => onDecide('reject')}>
                Reject
              </Button>
            </div>
          )}

          {(candidate.phase1_rationale || candidate.interview_rationale || hasCheatingFlags) && (
            <Button size="icon" variant="ghost" onClick={onToggle}>
              {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          )}
        </div>
      </div>

      {expanded && (candidate.phase1_rationale || candidate.interview_rationale || hasCheatingFlags) && (
        <div className="mt-3 flex flex-col gap-4">
          {candidate.interview_cheating_flags && candidate.interview_cheating_flags.length > 0 && (
            <div className="rounded-md border border-warning/30 bg-warning/5 p-4 text-sm">
              <p className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-warning">
                <ShieldAlert className="h-3.5 w-3.5" />
                Integrity flags ({candidate.interview_cheating_flags.length})
              </p>
              <p className="mb-2 text-xs text-muted-foreground">
                Detected during the interview — informational only, not part of the AI's score or decision.
              </p>
              <ul className="space-y-1">
                {candidate.interview_cheating_flags.map((f, i) => (
                  <li key={i} className="text-xs text-foreground">
                    <span className="font-medium">{CHEATING_FLAG_LABELS[f.kind] ?? f.kind}</span>
                    {f.detail ? <span className="text-muted-foreground"> — {f.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {candidate.interview_rationale && (
            <div className="rounded-md bg-muted/60 p-4 text-sm">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Interview review</p>
                <Badge variant={candidate.interview_decision === 'shortlist' ? 'success' : 'destructive'}>
                  {candidate.interview_decision === 'shortlist' ? 'Shortlisted' : 'Rejected'}
                </Badge>
              </div>

              {(candidate.interview_ability_score !== null || candidate.interview_confidence_score !== null) && (
                <div className="mb-3 flex gap-4">
                  {candidate.interview_ability_score !== null && (
                    <span className="text-xs text-muted-foreground">
                      Ability: <span className="font-semibold text-foreground">{Math.round(candidate.interview_ability_score)}</span>
                    </span>
                  )}
                  {candidate.interview_confidence_score !== null && (
                    <span className="text-xs text-muted-foreground">
                      Confidence: <span className="font-semibold text-foreground">{Math.round(candidate.interview_confidence_score)}</span>
                    </span>
                  )}
                </div>
              )}

              {candidate.interview_summary && <p className="mb-3 text-foreground">{candidate.interview_summary}</p>}

              {(candidate.interview_strengths?.length || candidate.interview_weaknesses?.length) && (
                <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {candidate.interview_strengths && candidate.interview_strengths.length > 0 && (
                    <div>
                      <p className="text-xs font-medium uppercase tracking-wide text-success">Strengths</p>
                      <ul className="mt-1 list-inside list-disc text-muted-foreground">
                        {candidate.interview_strengths.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {candidate.interview_weaknesses && candidate.interview_weaknesses.length > 0 && (
                    <div>
                      <p className="text-xs font-medium uppercase tracking-wide text-warning-foreground">Weaknesses</p>
                      <ul className="mt-1 list-inside list-disc text-muted-foreground">
                        {candidate.interview_weaknesses.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Reason for {candidate.interview_decision === 'shortlist' ? 'shortlisting' : 'rejection'}
              </p>
              <p className="text-foreground">{candidate.interview_rationale}</p>

              <Link to={`/candidates/${candidate.id}`} className="mt-3 inline-block text-xs text-primary hover:underline">
                View full transcript →
              </Link>
            </div>
          )}
          {candidate.phase1_rationale && (
            <div className="rounded-md bg-muted/60 p-4 text-sm">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Resume screening</p>
              <p className="text-foreground">{candidate.phase1_rationale}</p>
              {candidate.phase1_strengths && candidate.phase1_strengths.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-success">Strengths</p>
                  <ul className="mt-1 list-inside list-disc text-muted-foreground">
                    {candidate.phase1_strengths.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {candidate.phase1_gaps && candidate.phase1_gaps.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-warning-foreground">Gaps</p>
                  <ul className="mt-1 list-inside list-disc text-muted-foreground">
                    {candidate.phase1_gaps.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
