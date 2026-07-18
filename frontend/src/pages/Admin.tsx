import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, FileScan, IndianRupee, LogOut, Mic, ShieldCheck, Volume2 } from 'lucide-react'
import {
  adminLogin,
  getAdminToken,
  getAdminUsage,
  setAdminToken,
  type ServiceUsage,
  type UsagePricing,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
          ? 'The admin panel is disabled — set ADMIN_PASSWORD in the environment to enable it.'
          : 'Invalid admin credentials.',
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
          <CardDescription>
            System usage &amp; cost monitoring. Credentials come from{' '}
            <code className="text-xs">ADMIN_USERNAME</code> /{' '}
            <code className="text-xs">ADMIN_PASSWORD</code> in the server environment.
          </CardDescription>
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

function AdminDashboard({ onSignOut }: { onSignOut: () => void }) {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  // Dates actually applied to the query (typing a date shouldn't refetch per keystroke).
  const [applied, setApplied] = useState<{ start: string; end: string }>({ start: '', end: '' })

  const { data: report, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'usage', applied.start, applied.end],
    queryFn: () => getAdminUsage({ start: applied.start, end: applied.end }),
    retry: false,
  })

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
  const periodLabel = applied.start || applied.end ? 'for the selected period' : '(all time)'

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
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Filter by date</CardTitle>
            <CardDescription>
              Leave both empty for all-time usage. The end date is inclusive. Costs are recalculated
              for the filtered window using the current env rates.
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
                  Clear — all time
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
                  Total cost {periodLabel}
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
                Usage &amp; cost by service {periodLabel}
              </h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <ServiceCard
                  icon={<Bot className="h-4 w-4" />}
                  title="AI agent (Claude)"
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
              Rates are configured in the server environment (<code>PRICE_INR_*</code>) and applied
              at read time — changing a rate re-prices the whole history. Usage is recorded as
              interviews and resume scoring run; an empty dashboard means nothing has been metered
              yet for this period.
            </p>
          </>
        )}
      </main>
    </div>
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
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Active pricing (from env)</CardTitle>
        <CardDescription>
          USD reference rates converted at ₹86/$ into the <code>PRICE_INR_*</code> env vars. Edit
          those to re-price history.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">Service</th>
                <th className="pb-2 pr-4 font-medium">USD reference</th>
                <th className="pb-2 font-medium">Active rate (₹)</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              <tr className="border-b border-border/60">
                <td className="py-2 pr-4">AI agent — input</td>
                <td className="py-2 pr-4">$3 / 1M tokens</td>
                <td className="py-2">₹{pricing.llm_per_mtok_input} / 1M tokens</td>
              </tr>
              <tr className="border-b border-border/60">
                <td className="py-2 pr-4">AI agent — output</td>
                <td className="py-2 pr-4">$15 / 1M tokens</td>
                <td className="py-2">₹{pricing.llm_per_mtok_output} / 1M tokens</td>
              </tr>
              <tr className="border-b border-border/60">
                <td className="py-2 pr-4">STT</td>
                <td className="py-2 pr-4">$0.0154 / min</td>
                <td className="py-2">₹{pricing.stt_per_minute} / min</td>
              </tr>
              <tr className="border-b border-border/60">
                <td className="py-2 pr-4">TTS</td>
                <td className="py-2 pr-4">$0.10 / 1,000 chars</td>
                <td className="py-2">₹{pricing.tts_per_1k_chars} / 1,000 chars</td>
              </tr>
              <tr>
                <td className="py-2 pr-4">OCR</td>
                <td className="py-2 pr-4">$1.50 / 1,000 pages</td>
                <td className="py-2">₹{pricing.ocr_per_1k_pages} / 1,000 pages</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}
