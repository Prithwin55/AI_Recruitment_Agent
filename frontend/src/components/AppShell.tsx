import { Outlet, Link, useLocation } from 'react-router-dom'
import { LayoutDashboard, LogOut, Sparkles, Users } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'
import { useTenant } from '@/context/TenantContext'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
  { to: '/recruitments', label: 'Recruitments', icon: Users, exact: false },
]

export default function AppShell() {
  const { user, logout } = useAuth()
  const { tenant } = useTenant()
  const location = useLocation()

  const workspaceName = tenant?.display_name || tenant?.name || 'AI Recruitment'

  return (
    <div className="flex min-h-svh flex-col bg-muted/30">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-6">
            <Link to="/" className="flex items-center gap-2 font-semibold text-foreground">
              {tenant?.logo_url ? (
                <img src={tenant.logo_url} alt="" className="h-5 w-5 rounded object-contain" />
              ) : (
                <Sparkles className="h-5 w-5 text-primary" />
              )}
              {workspaceName}
            </Link>
            <nav className="flex items-center gap-1">
              {NAV_ITEMS.map((item) => {
                const active = item.exact
                  ? location.pathname === item.to
                  : location.pathname.startsWith(item.to)
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    className={cn(
                      'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                      active
                        ? 'bg-accent text-accent-foreground'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                    )}
                  >
                    <item.icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                )
              })}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span className="hidden sm:inline">{user?.email}</span>
            <ThemeToggle />
            <Button variant="ghost" size="sm" onClick={logout} className="gap-1.5">
              <LogOut className="h-4 w-4" />
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
