import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  Building2,
  Copy,
  FileScan,
  IndianRupee,
  LogOut,
  Mic,
  Plus,
  ShieldCheck,
  Volume2,
} from 'lucide-react'
import {
  adminLogin,
  createTenant,
  getAdminToken,
  getAdminUsage,
  listTenants,
  setAdminToken,
  updateTenant,
  type AdminTenant,
  type ServiceUsage,
  type UsagePricing,
} from '@/lib/api'
import { tenantBaseUrl } from '@/lib/tenant'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ThemeToggle } from '@/components/ThemeToggle'

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 2,
})
const num = new Intl.NumberFormat('en-IN')
const minutesFmt = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
})

export default function Admin() {
  const [authed, setAuthed] = useState(() => getAdminToken() !== null)
  return authed ? (
    <AdminDashboard
      onSignOut={() => {
        setAdminToken(null)
        setAuthed(false)
      }}
    />
  ) : (
    <AdminLogin onSuccess={() => setAuthed(true)} />
  )
}

function AdminLogin({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const token = await adminLogin(username, password)
      setAdminToken(token)
      onSuccess()
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      setError(
        status === 403
          ? 'The admin panel is not enabled. Contact your system administrator.'
          : 'Invalid username or password.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" />
            <CardTitle className="text-xl">Admin panel</CardTitle>
          </div>
          <CardDescription>Sign in to view system usage and cost.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="admin-user">Username</Label>
              <Input
                id="admin-user"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="admin-pass">Password</Label>
              <Input
                id="admin-pass"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={submitting} className="mt-2 w-full">
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

function toISODate(d: Date): string {
  // Local calendar date as YYYY-MM-DD (avoids the UTC shift of toISOString()).
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Default window: the last 30 days (inclusive of today) — scanning all-time on every login is
// needlessly heavy, and recent spend is what an admin checks first.
const DEFAULT_END = toISODate(new Date())
const DEFAULT_START = toISODate(new Date(Date.now() - 29 * 24 * 60 * 60 * 1000))

function AdminDashboard({ onSignOut }: { onSignOut: () => void }) {
  const [start, setStart] = useState(DEFAULT_START)
  const [end, setEnd] = useState(DEFAULT_END)
  // Dates actually applied to the query (typing a date shouldn't refetch per keystroke).
  const [applied, setApplied] = useState<{ start: string; end: string }>({
    start: DEFAULT_START,
    end: DEFAULT_END,
  })

  const { data: report, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'usage', applied.start, applied.end],
    queryFn: () => getAdminUsage({ start: applied.start, end: applied.end }),
    retry: false,
  })

  const isDefaultRange = applied.start === DEFAULT_START && applied.end === DEFAULT_END
  const periodLabel = isDefaultRange
    ? '· last 30 days'
    : applied.start || applied.end
      ? '· selected period'
      : '· all time'

  // Expired admin token → drop back to the login form (axios interceptor already cleared storage).
  useEffect(() => {
    const status = (error as { response?: { status?: number } } | null)?.response?.status
    if (isError && status === 401 && getAdminToken() === null) onSignOut()
  }, [isError, error, onSignOut])

  const byService = new Map<string, ServiceUsage>((report?.services ?? []).map((s) => [s.service, s]))
  const llm = byService.get('llm')
  const stt = byService.get('stt')
  const tts = byService.get('tts')
  const ocr = byService.get('ocr')
  const pricing = report?.pricing

  return (
    <div className="min-h-svh bg-muted/30">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2 font-semibold text-foreground">
            <ShieldCheck className="h-5 w-5 text-primary" />
            Admin — system usage &amp; cost
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button variant="ghost" size="sm" onClick={onSignOut} className="gap-1.5">
              <LogOut className="h-4 w-4" />
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
        <TenantsManager />

        <h2 className="text-sm font-medium text-muted-foreground">Usage &amp; cost</h2>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Filter by date</CardTitle>
            <CardDescription>
              Showing the last 30 days by default. Pick any range — the end date is inclusive — and
              cost is recalculated for that window. Leave both empty to see all-time usage.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault()
                setApplied({ start, end })
              }}
            >
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="start">From</Label>
                <Input
                  id="start"
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  className="w-44"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="end">To</Label>
                <Input
                  id="end"
                  type="date"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  className="w-44"
                />
              </div>
              <Button type="submit">Apply</Button>
              {!isDefaultRange && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setStart(DEFAULT_START)
                    setEnd(DEFAULT_END)
                    setApplied({ start: DEFAULT_START, end: DEFAULT_END })
                  }}
                >
                  Last 30 days
                </Button>
              )}
              {(applied.start || applied.end) && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setStart('')
                    setEnd('')
                    setApplied({ start: '', end: '' })
                  }}
                >
                  All time
                </Button>
              )}
            </form>
          </CardContent>
        </Card>

        {isError && (
          <p className="text-sm text-destructive">
            Couldn&apos;t load usage — your session may have expired. Sign out and back in.
          </p>
        )}
        {isLoading && <p className="text-sm text-muted-foreground">Loading usage…</p>}

        {report && (
          <>
            <Card>
              <CardContent className="flex flex-wrap items-baseline justify-between gap-2 pt-6">
                <span className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <IndianRupee className="h-4 w-4" />
                  Total cost <span className="text-xs">{periodLabel}</span>
                </span>
                <span className="text-3xl font-semibold tabular-nums text-foreground">
                  {inr.format(report.total_cost_inr)}
                </span>
              </CardContent>
            </Card>

            {/* Individual cost strip for a quick scan of the four services. */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <MiniCost label="AI agent" amount={llm?.cost_inr ?? 0} />
              <MiniCost label="STT" amount={stt?.cost_inr ?? 0} />
              <MiniCost label="TTS" amount={tts?.cost_inr ?? 0} />
              <MiniCost label="OCR" amount={ocr?.cost_inr ?? 0} />
            </div>

            <div>
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">
                Usage &amp; cost by service <span className="text-xs">{periodLabel}</span>
              </h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <ServiceCard
                  icon={<Bot className="h-4 w-4" />}
                  title="AI agent"
                  cost={llm?.cost_inr ?? 0}
                  rows={[
                    ['Input tokens', num.format(llm?.input_tokens ?? 0)],
                    ['Output tokens', num.format(llm?.output_tokens ?? 0)],
                    [
                      'Total tokens',
                      num.format((llm?.input_tokens ?? 0) + (llm?.output_tokens ?? 0)),
                    ],
                    ['API calls metered', num.format(llm?.events ?? 0)],
                  ]}
                  rate={
                    pricing
                      ? `₹${pricing.llm_per_mtok_input} in / ₹${pricing.llm_per_mtok_output} out per 1M tokens`
                      : ''
                  }
                />
                <ServiceCard
                  icon={<Mic className="h-4 w-4" />}
                  title="Speech to text (STT)"
                  cost={stt?.cost_inr ?? 0}
                  rows={[
                    ['Audio minutes', minutesFmt.format(stt?.minutes ?? 0)],
                    ['Sessions metered', num.format(stt?.events ?? 0)],
                  ]}
                  rate={pricing ? `₹${pricing.stt_per_minute} per minute` : ''}
                />
                <ServiceCard
                  icon={<Volume2 className="h-4 w-4" />}
                  title="Text to speech (TTS)"
                  cost={tts?.cost_inr ?? 0}
                  rows={[
                    ['Characters used', num.format(tts?.characters ?? 0)],
                    ['Sessions metered', num.format(tts?.events ?? 0)],
                  ]}
                  rate={pricing ? `₹${pricing.tts_per_1k_chars} per 1,000 characters` : ''}
                />
                <ServiceCard
                  icon={<FileScan className="h-4 w-4" />}
                  title="Resume parser (OCR)"
                  cost={ocr?.cost_inr ?? 0}
                  rows={[
                    ['Pages processed', num.format(ocr?.pages ?? 0)],
                    ['Resumes metered', num.format(ocr?.events ?? 0)],
                  ]}
                  rate={pricing ? `₹${pricing.ocr_per_1k_pages} per 1,000 pages` : ''}
                />
              </div>
            </div>

            {pricing && <PricingReference pricing={pricing} />}

            <p className="text-xs text-muted-foreground">
              Usage is recorded as interviews and resume screening run. If a period looks empty,
              nothing was billable in that window yet.
            </p>
          </>
        )}
      </main>
    </div>
  )
}

function TenantsManager() {
  const queryClient = useQueryClient()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [recruiterEmail, setRecruiterEmail] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [created, setCreated] = useState<{
    email: string
    password: string
    slug: string
    loginUrl: string
  } | null>(null)
  const [showForm, setShowForm] = useState(false)

  const { data: tenants, isLoading } = useQuery({ queryKey: ['admin', 'tenants'], queryFn: listTenants })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })

  const createMut = useMutation({
    mutationFn: () => createTenant({ slug, name, recruiter_email: recruiterEmail }),
    onSuccess: (res) => {
      setCreated({
        email: res.recruiter_email,
        password: res.temp_password,
        slug: res.tenant.slug,
        loginUrl: tenantBaseUrl(res.tenant.slug),
      })
      setSlug('')
      setName('')
      setRecruiterEmail('')
      setShowForm(false)
      setFormError(null)
      invalidate()
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response?.status
      setFormError(
        status === 409
          ? 'That subdomain is already taken.'
          : status === 422
            ? 'Check the subdomain (lowercase letters, digits, hyphens) and email.'
            : 'Could not create the workspace.',
      )
    },
  })

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'suspended' }) =>
      updateTenant(id, { status }),
    onSuccess: invalidate,
  })

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Building2 className="h-4 w-4 text-primary" />
              Organizations
            </CardTitle>
            <CardDescription>
              Create a workspace and its default recruiter account — they get a clean setup on their
              subdomain.
            </CardDescription>
          </div>
          <Button size="sm" className="gap-1.5" onClick={() => setShowForm((v) => !v)}>
            <Plus className="h-4 w-4" />
            New organization
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {showForm && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              setFormError(null)
              createMut.mutate()
            }}
            className="flex flex-wrap items-end gap-3 rounded-md border border-border p-3"
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-slug">Subdomain</Label>
              <Input
                id="t-slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="acme"
                className="w-40"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-name">Organization name</Label>
              <Input
                id="t-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Acme Inc."
                className="w-48"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-email">Recruiter email</Label>
              <Input
                id="t-email"
                type="email"
                value={recruiterEmail}
                onChange={(e) => setRecruiterEmail(e.target.value)}
                placeholder="recruiter@acme.com"
                className="w-56"
                required
              />
            </div>
            <Button type="submit" disabled={createMut.isPending}>
              {createMut.isPending ? 'Creating…' : 'Create'}
            </Button>
            {formError && <p className="w-full text-sm text-destructive">{formError}</p>}
          </form>
        )}

        {created && (
          <div className="rounded-md border border-success/40 bg-success/10 p-3 text-sm">
            <p className="font-medium text-foreground">
              Organization <strong>{created.slug}</strong> created — default recruiter: {created.email}
            </p>
            <p className="mt-1 text-muted-foreground">
              Login URL:{' '}
              <a
                href={created.loginUrl}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-foreground underline underline-offset-2"
              >
                {created.loginUrl}
              </a>
            </p>
            <p className="mt-1 text-muted-foreground">One-time password (won't be shown again):</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="rounded bg-background px-2 py-1 font-mono text-foreground">{created.password}</code>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={() => navigator.clipboard?.writeText(created.password)}
              >
                <Copy className="h-3.5 w-3.5" />
                Copy
              </Button>
            </div>
          </div>
        )}


        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="pb-2 pr-4 font-medium">Workspace</th>
                  <th className="pb-2 pr-4 font-medium">Subdomain</th>
                  <th className="pb-2 pr-4 font-medium tabular-nums">Users</th>
                  <th className="pb-2 pr-4 font-medium tabular-nums">Recruitments</th>
                  <th className="pb-2 pr-4 font-medium tabular-nums">Candidates</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {(tenants ?? []).map((t: AdminTenant) => (
                  <tr key={t.id} className="border-b border-border/60">
                    <td className="py-2 pr-4 font-medium text-foreground">{t.name}</td>
                    <td className="py-2 pr-4 text-muted-foreground">{t.slug}</td>
                    <td className="py-2 pr-4 tabular-nums">{t.users}</td>
                    <td className="py-2 pr-4 tabular-nums">{t.recruitments}</td>
                    <td className="py-2 pr-4 tabular-nums">{t.candidates}</td>
                    <td className="py-2 pr-4">
                      <Badge variant={t.status === 'active' ? 'success' : 'destructive'}>{t.status}</Badge>
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={statusMut.isPending}
                        onClick={() =>
                          statusMut.mutate({
                            id: t.id,
                            status: t.status === 'active' ? 'suspended' : 'active',
                          })
                        }
                      >
                        {t.status === 'active' ? 'Suspend' : 'Reactivate'}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function MiniCost({ label, amount }: { label: string; amount: number }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1 pt-4 pb-4">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <span className="text-lg font-semibold tabular-nums text-foreground">{inr.format(amount)}</span>
      </CardContent>
    </Card>
  )
}

function ServiceCard({
  icon,
  title,
  cost,
  rows,
  rate,
}: {
  icon: ReactNode
  title: string
  cost: number
  rows: [string, string][]
  rate: string
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <span className="text-primary">{icon}</span>
            {title}
          </CardTitle>
          <span className="text-lg font-semibold tabular-nums text-foreground">{inr.format(cost)}</span>
        </div>
        {rate && <CardDescription>Rate: {rate}</CardDescription>}
      </CardHeader>
      <CardContent>
        <dl className="flex flex-col gap-1.5 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="tabular-nums text-foreground">{value}</dd>
            </div>
          ))}
          <div className="mt-1 flex items-baseline justify-between gap-3 border-t border-border pt-2">
            <dt className="font-medium text-foreground">Cost (₹)</dt>
            <dd className="font-semibold tabular-nums text-foreground">{inr.format(cost)}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function PricingReference({ pricing }: { pricing: UsagePricing }) {
  const rows: [string, string][] = [
    ['AI agent — input', `₹${pricing.llm_per_mtok_input} / 1M tokens`],
    ['AI agent — output', `₹${pricing.llm_per_mtok_output} / 1M tokens`],
    ['Speech to text', `₹${pricing.stt_per_minute} / minute`],
    ['Text to speech', `₹${pricing.tts_per_1k_chars} / 1,000 characters`],
    ['Resume parser (OCR)', `₹${pricing.ocr_per_1k_pages} / 1,000 pages`],
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Rate card</CardTitle>
        <CardDescription>The rates every cost above is calculated from.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">Service</th>
                <th className="pb-2 font-medium">Rate</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {rows.map(([service, rate], i) => (
                <tr key={service} className={i < rows.length - 1 ? 'border-b border-border/60' : ''}>
                  <td className="py-2 pr-4">{service}</td>
                  <td className="py-2">{rate}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}
