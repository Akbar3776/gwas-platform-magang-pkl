import { useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const from = location.state?.from?.pathname || '/upload'
  const errorId = 'login-form-error'

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
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
          <input
            type="email"
            name="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onInvalid={(e) => {
              if (e.target.validity.valueMissing) {
                e.target.setCustomValidity('Email wajib diisi.')
              } else {
                e.target.setCustomValidity('Masukkan alamat email yang valid.')
              }
            }}
            onInput={(e) => e.target.setCustomValidity('')}
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={error ? errorId : undefined}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onInvalid={(e) => e.target.setCustomValidity('Password wajib diisi.')}
            onInput={(e) => e.target.setCustomValidity('')}
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={error ? errorId : undefined}
            required
          />
        </label>
        {error && (
          <p id={errorId} role="alert" className="form-error">
            {error}
          </p>
        )}
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
