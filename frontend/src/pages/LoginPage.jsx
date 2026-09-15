import { useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

// Placeholder minimal — sesuaikan styling dengan kebutuhan UI final.
export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const from = location.state?.from?.pathname || '/upload'

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      // login() di AuthContext.jsx melempar Error biasa dengan .message
      // sudah berisi pesan yang siap ditampilkan ke user.
      await login(email, password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.message || 'Login gagal. Periksa kembali email/password.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <h1>Masuk ke GWAS Platform</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="form-error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Memproses...' : 'Masuk'}
        </button>
      </form>
      <p>
        Belum punya akun? <Link to="/register">Daftar di sini</Link>
      </p>
    </div>
  )
}
