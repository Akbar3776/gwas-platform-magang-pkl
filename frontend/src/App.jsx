import { Suspense, lazy, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useLocation, useParams } from 'react-router-dom'
import { useAuth, ProtectedRoute } from './context/AuthContext.jsx'

import LoginPage from './pages/LoginPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'

const GwasUploadPage = lazy(() =>
  import('./pages/GwasUploadPage.jsx').then((m) => ({ default: m.GwasUploadPage }))
)
const JobStatusPage = lazy(() => import('./pages/JobStatusPage.jsx'))
const ResultsPage = lazy(() => import('./pages/ResultsPage.jsx'))

function getRouteTitle(pathname) {
  if (pathname === '/login') return 'Masuk — GWAS Platform'
  if (pathname === '/register') return 'Daftar Akun — GWAS Platform'
  if (pathname === '/upload' || pathname === '/') return 'Workspace & Upload Data — GWAS Platform'
  if (/^\/jobs\/[^/]+\/results\/?$/.test(pathname)) return 'Hasil Analisis GWAS — GWAS Platform'
  if (/^\/jobs\/[^/]+\/?$/.test(pathname)) return 'Progress Analisis — GWAS Platform'
  return 'Halaman Tidak Ditemukan (404) — GWAS Platform'
}

function ResultsPageRoute() {
  const { jobId } = useParams()
  return <ResultsPage key={jobId} />
}

function AppLayout({ children }) {
  const { status, user, logout } = useAuth()
  const location = useLocation()
  const isAuthenticated = status === 'authenticated'

  useEffect(() => {
    document.title = getRouteTitle(location.pathname)
  }, [location.pathname])

  const handleLogout = () => {
    Promise.resolve(logout()).catch(() => {})
  }
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/upload" className="brand">GWAS Platform</Link>
        {isAuthenticated && (
          <nav>
            <span>{user?.full_name || user?.email}</span>
            <button type="button" onClick={handleLogout}>Keluar</button>
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
      <Suspense
        fallback={
          <div
            role="status"
            className="flex session-status items-center justify-center text-sm text-slate-500"
          >
            Memuat halaman…
          </div>
        }
      >
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
            <Route path="/jobs/:jobId/results" element={<ResultsPageRoute />} />
          </Route>

          {/* Default & 404 */}
          <Route path="/" element={<Navigate to="/upload" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </AppLayout>
  )
}
