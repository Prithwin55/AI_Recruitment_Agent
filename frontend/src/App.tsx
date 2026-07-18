import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from '@/context/AuthContext'
import ProtectedRoute from '@/components/ProtectedRoute'
import AppShell from '@/components/AppShell'
import Login from '@/pages/Login'
import ChangePassword from '@/pages/ChangePassword'
import Dashboard from '@/pages/Dashboard'
import Recruitments from '@/pages/Recruitments'
import NewRecruitment from '@/pages/NewRecruitment'
import RecruitmentDetail from '@/pages/RecruitmentDetail'
import CandidateDetail from '@/pages/CandidateDetail'
import Admin from '@/pages/Admin'
import PreJoin from '@/pages/meeting/PreJoin'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* Separate env credentials (ADMIN_USERNAME / ADMIN_PASSWORD); not recruiter auth. */}
          <Route path="/admin" element={<Admin />} />
          <Route path="/interview/:token" element={<PreJoin />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/change-password" element={<ChangePassword />} />
            <Route element={<AppShell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/recruitments" element={<Recruitments />} />
              <Route path="/recruitments/new" element={<NewRecruitment />} />
              <Route path="/recruitments/:id" element={<RecruitmentDetail />} />
              <Route path="/candidates/:id" element={<CandidateDetail />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
