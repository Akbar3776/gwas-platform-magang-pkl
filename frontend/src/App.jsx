import { Routes, Route, Navigate, Link } from 'react-router-dom'
import { useAuth, ProtectedRoute } from './context/AuthContext.jsx'

import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import { GwasUploadPage } from './pages/GwasUploadPage.jsx'
import JobStatusPage from './pages/JobStatusPage.jsx'
import ResultsPage from './pages/ResultsPage.jsx'

function AppLayout({ children }) {
  const { status, user, logout } = useAuth()
  const isAuthenticated = status === 'authenticated'
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/upload" className="brand">GWAS Platform</Link>
        {isAuthenticated && (
          <nav>
            <span>{user?.full_name || user?.email}</span>
            <button onClick={logout}>Keluar</button>
          </nav>
        )}
      </header>
      <main>{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <AppLayout>
      <Routes>
        {/* Rute publik */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Rute terproteksi — ProtectedRoute dari AuthContext.jsx menangani
            status "checking" (loading) dan "unauthenticated" (redirect),
            lalu merender <Outlet/> untuk rute anak di bawah ini. */}
        <Route element={<ProtectedRoute />}>
          <Route path="/upload" element={<GwasUploadPage />} />
          <Route path="/jobs/:jobId" element={<JobStatusPage />} />
          <Route path="/jobs/:jobId/results" element={<ResultsPage />} />
        </Route>

        {/* Default: arahkan ke halaman upload (ProtectedRoute akan redirect ke /login jika belum auth) */}
        <Route path="/" element={<Navigate to="/upload" replace />} />
        <Route path="*" element={<Navigate to="/upload" replace />} />
      </Routes>
    </AppLayout>
  )
}
