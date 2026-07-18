import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Plus, Users } from 'lucide-react'
import { listRecruitments } from '@/lib/api'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Pagination } from '@/components/Pagination'

export default function Recruitments() {
  const [page, setPage] = useState(1)
  const { data, isLoading } = useQuery({
    queryKey: ['recruitments', 'list', page],
    queryFn: () => listRecruitments({ page }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  })

  const recruitments = data?.items
  const pageSize = data?.page_size ?? 12
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Recruitments</h1>
          <p className="text-sm text-muted-foreground">
            Create a role, upload resumes, and let the AI shortlist candidates.
          </p>
        </div>
        <Link to="/recruitments/new" className={buttonVariants({ className: 'gap-1.5' })}>
          <Plus className="h-4 w-4" />
          New recruitment
        </Link>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {!isLoading && recruitments?.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No recruitments yet</CardTitle>
            <CardDescription>Create your first recruitment to start shortlisting candidates.</CardDescription>
          </CardHeader>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {recruitments?.map((r) => (
          <Link key={r.id} to={`/recruitments/${r.id}`}>
            <Card className="h-full transition-shadow hover:shadow-md">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="text-base">{r.title}</CardTitle>
                  <Badge variant="outline" className="shrink-0 capitalize">
                    {r.status.replace('_', ' ')}
                  </Badge>
                </div>
                <CardDescription className="line-clamp-2">{r.jd_text}</CardDescription>
              </CardHeader>
              <CardContent className="flex items-center gap-4 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Users className="h-4 w-4" />
                  {r.counts.total_candidates} candidate{r.counts.total_candidates === 1 ? '' : 's'}
                </span>
                {r.counts.scored > 0 && <span>{r.counts.scored} scored</span>}
                {r.counts.advanced > 0 && <span>{r.counts.advanced} advancing</span>}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      <Pagination page={page} pageCount={pageCount} onPageChange={setPage} />
    </div>
  )
}
