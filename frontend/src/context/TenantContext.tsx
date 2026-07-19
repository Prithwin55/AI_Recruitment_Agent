import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { fetchCurrentTenant, type TenantPublic } from '@/lib/api'

interface TenantContextValue {
  tenant: TenantPublic | null
  loading: boolean
  error: 'not_found' | 'suspended' | 'other' | null
}

const TenantContext = createContext<TenantContextValue>({ tenant: null, loading: true, error: null })

/** Loads the current workspace's public branding once at boot (works before login). Drives the
 * branded login screen and the app chrome. 404 = unknown slug; 403 = suspended workspace. */
export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenant, setTenant] = useState<TenantPublic | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<'not_found' | 'suspended' | 'other' | null>(null)

  useEffect(() => {
    let active = true
    fetchCurrentTenant()
      .then((t) => active && (setTenant(t), setError(null)))
      .catch((e: unknown) => {
        if (!active) return
        const status = (e as { response?: { status?: number } })?.response?.status
        if (status === 404) setError('not_found')
        else if (status === 403) setError('suspended')
        else setError('other')
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [])

  // Apply the tenant's accent color as the primary CSS var, if set.
  useEffect(() => {
    if (tenant?.primary_color) {
      document.documentElement.style.setProperty('--primary', tenant.primary_color)
    }
  }, [tenant?.primary_color])

  return <TenantContext.Provider value={{ tenant, loading, error }}>{children}</TenantContext.Provider>
}

export function useTenant(): TenantContextValue {
  return useContext(TenantContext)
}
