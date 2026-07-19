import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/context/AuthContext'
import { useTenant } from '@/context/TenantContext'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function Login() {
  const { login } = useAuth()
  const { tenant, loading: tenantLoading, error: tenantError } = useTenant()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const workspaceName = tenant?.display_name || tenant?.name

  if (tenantError === 'not_found') {
    return (
      <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle className="text-xl">Workspace not found</CardTitle>
            <CardDescription>
              This address doesn't match any workspace. Check the link, or contact your administrator.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }
  // resolve_tenant returns 403 for SUSPENDED before /tenant/current can return a body, so we
  // surface suspended from the error code as well as from a status field if present.
  if (tenantError === 'suspended' || tenant?.status === 'suspended') {
    return (
      <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle className="text-xl">
              {workspaceName ? `${workspaceName} is unavailable` : 'Workspace unavailable'}
            </CardTitle>
            <CardDescription>This workspace is currently suspended. Contact your administrator.</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const user = await login(email, password)
      navigate(user.must_change_password ? '/change-password' : '/', { replace: true })
    } catch {
      setError('Invalid email or password.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">
            {tenantLoading ? 'AI Recruitment' : workspaceName || 'AI Recruitment'}
          </CardTitle>
          <CardDescription>Sign in to your recruiter portal.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
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
