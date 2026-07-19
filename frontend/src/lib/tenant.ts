// Tenant is identified by the subdomain: {slug}.<VITE_ROOT_DOMAIN>. In dev VITE_ROOT_DOMAIN is
// "localhost" and you browse acme.localhost:5173.

const ROOT_DOMAIN = (import.meta.env.VITE_ROOT_DOMAIN as string | undefined) ?? 'localhost'
const RESERVED = new Set(['www', 'app', 'api', 'admin', 'auth'])

/** The tenant slug from the current hostname, or null when there's no tenant subdomain. */
export function getTenantSlug(): string | null {
  const host = window.location.hostname.toLowerCase()
  const root = ROOT_DOMAIN.split(':')[0].toLowerCase()
  if (host === root || !host.endsWith('.' + root)) return null
  const label = host.slice(0, -(root.length + 1)).split('.').pop() ?? ''
  if (!label || RESERVED.has(label)) return null
  return label
}

/** Public base URL for a tenant workspace (preserves current scheme + port in dev). */
export function tenantBaseUrl(slug: string): string {
  const root = ROOT_DOMAIN.split(':')[0]
  const { protocol, port } = window.location
  const host = port ? `${slug}.${root}:${port}` : `${slug}.${root}`
  return `${protocol}//${host}`
}
