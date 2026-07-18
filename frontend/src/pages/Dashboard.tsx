import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ArrowRight, Plus } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getRecruitmentStats, listRecruitments } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { buttonVariants } from '@/components/ui/button'
import { Pagination } from '@/components/Pagination'
import { cn } from '@/lib/utils'

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export default function Dashboard() {
  const [page, setPage] = useState(1)

  // Aggregate totals across ALL recruitments — independent of the current list page.
  const { data: stats } = useQuery({
    queryKey: ['recruitments', 'stats'],
    queryFn: getRecruitmentStats,
    refetchInterval: 10000,
  })

  // One page of recruitments — drives the funnel chart and the list below.
  const { data, isLoading } = useQuery({
    queryKey: ['recruitments', 'list', page],
    queryFn: () => listRecruitments({ page }),
    placeholderData: keepPreviousData,
    refetchInterval: 10000,
  })

  const recruitments = data?.items
  const pageSize = data?.page_size ?? 12
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))
  const recruitmentsTotal = stats?.recruitments_total ?? 0

  const totals = {
    candidates: stats?.candidates ?? 0,
    scored: stats?.scored ?? 0,
    interviewing: stats?.interviewing ?? 0,
    completed: stats?.completed ?? 0,
    shortlisted: stats?.shortlisted ?? 0,
  }

  const chartData = (recruitments ?? [])
    .filter((r) => r.counts.total_candidates > 0)
    .map((r) => ({
      title: truncate(r.title, 18),
      Advancing: r.counts.advanced,
      Completed: r.counts.interview_completed,
      Shortlisted: r.counts.shortlisted,
    }))

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">Analytics across every recruitment.</p>
        </div>
        <Link to="/recruitments/new" className={buttonVariants({ className: 'gap-1.5' })}>
          <Plus className="h-4 w-4" />
          New recruitment
        </Link>
      </div>

      {!isLoading && recruitmentsTotal === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Get started</CardTitle>
            <CardDescription>
              Create a recruitment, paste the job description, and upload resumes to let the AI shortlist
              candidates.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link to="/recruitments" className={buttonVariants({ className: 'gap-1.5' })}>
              Go to Recruitments
              <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Recruitments" value={recruitmentsTotal} />
            <StatTile label="Candidates" value={totals.candidates} />
            <StatTile label="Scored" value={totals.scored} />
            <StatTile label="Interviewing now" value={totals.interviewing} tone={totals.interviewing > 0 ? 'active' : undefined} />
            <StatTile label="Interviews completed" value={totals.completed} />
            <StatTile label="Shortlisted" value={totals.shortlisted} tone="good" />
          </div>

          {chartData.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Candidate funnel by recruitment</CardTitle>
                <CardDescription>Candidates advanced to interview, interviews completed, and final shortlist — per role.</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="viz-root h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }} barGap={4}>
                      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                      <XAxis
                        dataKey="title"
                        tick={{ fill: 'var(--chart-axis)', fontSize: 12 }}
                        axisLine={{ stroke: 'var(--chart-grid)' }}
                        tickLine={false}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fill: 'var(--chart-axis)', fontSize: 12 }}
                        axisLine={false}
                        tickLine={false}
                        width={28}
                      />
                      <Tooltip
                        cursor={{ fill: 'var(--color-muted)' }}
                        contentStyle={{
                          background: 'var(--color-card)',
                          border: '1px solid var(--color-border)',
                          borderRadius: 8,
                          fontSize: 13,
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: 13 }} />
                      <Bar dataKey="Advancing" fill="var(--chart-series-1)" radius={[4, 4, 0, 0]} maxBarSize={36} />
                      <Bar dataKey="Completed" fill="var(--chart-series-2)" radius={[4, 4, 0, 0]} maxBarSize={36} />
                      <Bar dataKey="Shortlisted" fill="var(--chart-series-3)" radius={[4, 4, 0, 0]} maxBarSize={36} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Recruitments</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="divide-y divide-border">
                {recruitments?.map((r) => (
                  <Link
                    key={r.id}
                    to={`/recruitments/${r.id}`}
                    className="flex flex-wrap items-center justify-between gap-3 px-6 py-3 text-sm hover:bg-accent"
                  >
                    <span className="font-medium text-foreground">{r.title}</span>
                    <span className="flex gap-4 text-muted-foreground">
                      <span>{r.counts.total_candidates} candidates</span>
                      <span>{r.counts.interview_in_progress} interviewing</span>
                      <span>{r.counts.interview_completed} completed</span>
                      <span className="text-success">{r.counts.shortlisted} shortlisted</span>
                    </span>
                  </Link>
                ))}
              </div>
              {pageCount > 1 && (
                <Pagination
                  page={page}
                  pageCount={pageCount}
                  onPageChange={setPage}
                  className="flex items-center justify-between border-t border-border px-6 py-3"
                />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}

function StatTile({ label, value, tone }: { label: string; value: number; tone?: 'good' | 'active' }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          'text-xl font-semibold tabular-nums',
          tone === 'good' && value > 0 && 'text-success',
          tone === 'active' && value > 0 && 'text-primary',
        )}
      >
        {value}
      </p>
    </div>
  )
}
