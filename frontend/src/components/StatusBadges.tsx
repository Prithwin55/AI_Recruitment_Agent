import { Badge } from '@/components/ui/badge'
import type { Phase1Decision, Phase2Status, ProcessingStatus } from '@/lib/api'

export function ProcessingStatusBadge({ status }: { status: ProcessingStatus }) {
  switch (status) {
    case 'queued':
      return <Badge variant="secondary">Queued</Badge>
    case 'processing':
      return <Badge variant="warning">Processing…</Badge>
    case 'scored':
      return <Badge variant="success">Scored</Badge>
    case 'failed':
      return <Badge variant="destructive">Failed</Badge>
  }
}

export function Phase1DecisionBadge({ decision }: { decision: Phase1Decision }) {
  switch (decision) {
    case 'advance':
      return <Badge variant="success">Advancing</Badge>
    case 'hold':
      return <Badge variant="warning">On hold</Badge>
    case 'reject':
      return <Badge variant="destructive">Rejected</Badge>
    case 'pending':
      return <Badge variant="outline">Undecided</Badge>
  }
}

export function Phase2StatusBadge({ status }: { status: Phase2Status }) {
  switch (status) {
    case 'not_scheduled':
      return <Badge variant="outline">Not scheduled</Badge>
    case 'scheduled':
      return <Badge variant="secondary">Scheduled</Badge>
    case 'in_progress':
      return <Badge variant="warning">Interview in progress</Badge>
    case 'completed':
      return <Badge variant="success">Interview completed</Badge>
    case 'expired':
      return <Badge variant="destructive">Link expired</Badge>
    case 'no_show':
      return <Badge variant="destructive">No-show</Badge>
  }
}
