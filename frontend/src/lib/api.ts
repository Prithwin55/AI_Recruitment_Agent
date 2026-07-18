import axios from 'axios'

const TOKEN_KEY = 'recruiter_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
})

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      setToken(null)
      const path = window.location.pathname
      // Don't bounce the admin panel (or login) into the recruiter login flow.
      if (!path.startsWith('/login') && !path.startsWith('/admin')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export interface User {
  id: string
  email: string
  must_change_password: boolean
}

export interface LoginResponse {
  access_token: string
  token_type: string
  must_change_password: boolean
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>('/auth/login', { email, password })
  return data
}

export async function fetchCurrentUser(): Promise<User> {
  const { data } = await api.get<User>('/auth/me')
  return data
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<User> {
  const { data } = await api.post<User>('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  return data
}

// --- Recruitments ---

export interface RecruitmentCounts {
  total_candidates: number
  queued: number
  processing: number
  scored: number
  failed: number
  advanced: number
  awaiting_schedule: number
  interview_in_progress: number
  interview_completed: number
  shortlisted: number
}

export interface Recruitment {
  id: string
  title: string
  jd_text: string
  status: string
  created_at: string
  counts: RecruitmentCounts
}

export interface PaginatedRecruitments {
  items: Recruitment[]
  total: number
  page: number
  page_size: number
}

export interface RecruitmentStats {
  recruitments_total: number
  candidates: number
  scored: number
  advancing: number
  interviewing: number
  completed: number
  shortlisted: number
}

export interface FunnelBar {
  title: string
  advancing: number
  completed: number
  shortlisted: number
}

export interface PipelineBreakdown {
  shortlisted: number
  interviewed: number
  advancing: number
  rejected: number
  screening: number
  failed: number
}

export interface PageAnalytics {
  page: number
  page_size: number
  total: number
  candidates: number
  funnel: FunnelBar[] // per-recruitment, for the bar chart (this page only)
  pipeline: PipelineBreakdown // mutually-exclusive buckets, for the pie (this page only)
}

export async function listRecruitments(
  opts: { page?: number; pageSize?: number } = {},
): Promise<PaginatedRecruitments> {
  const { data } = await api.get<PaginatedRecruitments>('/recruitments', {
    params: {
      page: opts.page ?? 1,
      // Omit page_size so the backend applies its env-configured default; only send when overridden.
      page_size: opts.pageSize,
    },
  })
  return data
}

export async function getRecruitmentStats(): Promise<RecruitmentStats> {
  const { data } = await api.get<RecruitmentStats>('/recruitments/stats')
  return data
}

export async function getPageAnalytics(
  opts: { page?: number; pageSize?: number } = {},
): Promise<PageAnalytics> {
  const { data } = await api.get<PageAnalytics>('/recruitments/analytics', {
    params: { page: opts.page ?? 1, page_size: opts.pageSize },
  })
  return data
}

export async function getRecruitment(id: string): Promise<Recruitment> {
  const { data } = await api.get<Recruitment>(`/recruitments/${id}`)
  return data
}

export async function createRecruitment(title: string, jdText: string): Promise<Recruitment> {
  const { data } = await api.post<Recruitment>('/recruitments', { title, jd_text: jdText })
  return data
}

// --- Candidates ---

export type ProcessingStatus = 'queued' | 'processing' | 'scored' | 'failed'
export type Phase1Decision = 'pending' | 'advance' | 'hold' | 'reject'
export type Phase2Status =
  | 'not_scheduled'
  | 'scheduled'
  | 'in_progress'
  | 'completed'
  | 'expired'
  | 'no_show'

export interface Candidate {
  id: string
  original_filename: string
  name: string | null
  email: string | null
  phone: string | null
  processing_status: ProcessingStatus
  processing_error: string | null
  phase1_score: number | null
  phase1_rationale: string | null
  phase1_strengths: string[] | null
  phase1_gaps: string[] | null
  phase1_decision: Phase1Decision
  auto_advanced: boolean
  phase2_status: Phase2Status
  created_at: string
  interview_score: number | null
  interview_decision: 'shortlist' | 'reject' | null
  interview_rationale: string | null
  interview_summary: string | null
  interview_strengths: string[] | null
  interview_weaknesses: string[] | null
  interview_ability_score: number | null
  interview_confidence_score: number | null
  interview_cheating_flags: CheatingFlag[] | null
}

export interface CheatingFlag {
  kind: string
  detail: string | null
  at_seconds: number | null
  created_at: string
}

export const CHEATING_FLAG_LABELS: Record<string, string> = {
  multiple_faces: 'Multiple people on camera',
  no_face: 'Candidate not visible',
  looking_away: 'Looking away from screen',
  head_turned: 'Head turned away',
  multiple_voices: 'Multiple voices detected',
}

export interface RejectedUpload {
  filename: string
  reason: string
}

export interface BulkUploadResult {
  created: Candidate[]
  rejected: RejectedUpload[]
}

export interface PaginatedCandidates {
  interviews: Candidate[] // one page of shortlisted (advanced), ongoing/scheduled first
  interviews_total: number
  interviews_page: number
  ready_to_schedule_total: number // shortlisted & not-yet-scheduled across ALL pages
  pool: Candidate[] // one page of not-shortlisted candidates, newest activity first
  pool_total: number
  pool_page: number
  page_size: number
}

export async function listCandidates(
  recruitmentId: string,
  opts: { interviewsPage?: number; poolPage?: number; pageSize?: number; search?: string } = {},
): Promise<PaginatedCandidates> {
  const { data } = await api.get<PaginatedCandidates>(`/recruitments/${recruitmentId}/candidates`, {
    params: {
      interviews_page: opts.interviewsPage ?? 1,
      pool_page: opts.poolPage ?? 1,
      // Omit page_size so the backend applies its env-configured default; only send when a caller
      // explicitly overrides it. The response's page_size reflects whatever was actually used.
      page_size: opts.pageSize,
      search: opts.search?.trim() || undefined,
    },
  })
  return data
}

export async function bulkUploadResumes(recruitmentId: string, files: File[]): Promise<BulkUploadResult> {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  const { data } = await api.post<BulkUploadResult>(
    `/recruitments/${recruitmentId}/candidates/bulk-upload`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function updateCandidateDecision(
  candidateId: string,
  decision: 'advance' | 'hold' | 'reject',
): Promise<Candidate> {
  const { data } = await api.patch<Candidate>(`/candidates/${candidateId}/decision`, { decision })
  return data
}

// --- Scheduling ---

export interface ScheduledCandidate {
  candidate_id: string
  name: string | null
  email: string | null
  calendar_invited: boolean
}

export interface FailedCandidate {
  candidate_id: string
  name: string | null
  reason: string
}

export interface ScheduleInterviewsResult {
  scheduled: ScheduledCandidate[]
  failed: FailedCandidate[]
  skipped_no_email: FailedCandidate[]
}

export async function scheduleInterviews(recruitmentId: string): Promise<ScheduleInterviewsResult> {
  const { data } = await api.post<ScheduleInterviewsResult>(
    `/recruitments/${recruitmentId}/schedule-interviews`,
  )
  return data
}

// --- Public interview link (candidate-facing, no auth) ---

export type InterviewLanguageCode = 'en' | 'ar-OM'

export interface PublicInterviewSession {
  token_status: 'pending' | 'active' | 'completed' | 'expired'
  language: InterviewLanguageCode
  role_title: string
  candidate_first_name: string | null
  duration_minutes: number
  expires_at: string
  started_at: string | null
}

export async function getPublicInterviewSession(token: string): Promise<PublicInterviewSession> {
  const { data } = await api.get<PublicInterviewSession>(`/interview/${token}`)
  return data
}

export async function setInterviewLanguage(
  token: string,
  language: InterviewLanguageCode,
): Promise<PublicInterviewSession> {
  const { data } = await api.patch<PublicInterviewSession>(`/interview/${token}/language`, { language })
  return data
}

export interface StartInterviewResult {
  success: boolean
  reason: string | null
  started_at: string | null
}

export async function startInterview(token: string): Promise<StartInterviewResult> {
  const { data } = await api.post<StartInterviewResult>(`/interview/${token}/start`)
  return data
}

// --- Candidate detail (phase 1 + phase 2 results) ---

export interface TranscriptTurn {
  speaker: 'agent' | 'candidate'
  text: string
  cut_off: boolean
  sentiment_label: string | null
  sentiment_score: number | null
  created_at: string
}

export interface SentimentSummary {
  average_score: number
  positive_count: number
  neutral_count: number
  negative_count: number
}

export interface InterviewSessionDetail {
  id: string
  language: InterviewLanguageCode
  token_status: 'pending' | 'active' | 'completed' | 'expired'
  scheduled_start: string | null
  started_at: string | null
  ended_at: string | null
  duration_minutes: number
  summary: string | null
  strengths: string[] | null
  weaknesses: string[] | null
  ability_score: number | null
  confidence_score: number | null
  final_score: number | null
  rationale: string | null
  decision: 'shortlist' | 'reject' | null
  sentiment_summary: SentimentSummary | null
  transcript_turns: TranscriptTurn[]
  cheating_flags: CheatingFlag[]
}

export interface ParsedProfile {
  summary?: string
  skills?: string[]
  years_experience?: number
  education?: string[]
}

export interface CandidateDetail extends Candidate {
  recruitment_id: string
  parsed_profile: ParsedProfile | null
  interview_sessions: InterviewSessionDetail[]
}

export async function getCandidate(candidateId: string): Promise<CandidateDetail> {
  const { data } = await api.get<CandidateDetail>(`/candidates/${candidateId}`)
  return data
}

export async function fetchResumeBlob(candidateId: string): Promise<Blob> {
  const { data } = await api.get<Blob>(`/candidates/${candidateId}/resume`, { responseType: 'blob' })
  return data
}

// --- Admin panel (separate credential set from recruiter accounts) ---

const ADMIN_TOKEN_KEY = 'admin_token'

export function getAdminToken(): string | null {
  return localStorage.getItem(ADMIN_TOKEN_KEY)
}

export function setAdminToken(token: string | null): void {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token)
  else localStorage.removeItem(ADMIN_TOKEN_KEY)
}

// Own axios instance: the main `api` interceptor injects the RECRUITER token, which the admin
// endpoints reject. This one injects the admin token and clears it on 401 (no /login redirect —
// the admin page renders its own login form).
export const adminApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
})

adminApi.interceptors.request.use((config) => {
  const token = getAdminToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

adminApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) setAdminToken(null)
    return Promise.reject(error)
  },
)

export async function adminLogin(username: string, password: string): Promise<string> {
  const { data } = await adminApi.post<{ access_token: string }>('/admin/login', { username, password })
  return data.access_token
}

export interface ServiceUsage {
  service: 'llm' | 'stt' | 'tts' | 'ocr'
  input_tokens: number
  output_tokens: number
  minutes: number
  characters: number
  pages: number
  events: number
  cost_inr: number
}

export interface UsagePricing {
  llm_per_mtok_input: number
  llm_per_mtok_output: number
  stt_per_minute: number
  tts_per_1k_chars: number
  ocr_per_1k_pages: number
}

export interface UsageReport {
  start: string | null
  end: string | null
  services: ServiceUsage[]
  total_cost_inr: number
  pricing: UsagePricing
}

export async function getAdminUsage(opts: { start?: string; end?: string } = {}): Promise<UsageReport> {
  const { data } = await adminApi.get<UsageReport>('/admin/usage', {
    params: { start: opts.start || undefined, end: opts.end || undefined },
  })
  return data
}
