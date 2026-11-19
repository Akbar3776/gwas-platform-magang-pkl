import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ fullName: '', email: '', password: '' })
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const errorId = 'register-form-error'

  const handleChange = (e) => setForm((f) => ({ ...f, [e.target.name]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await register(form.email, form.password, form.fullName)
      navigate('/login', { replace: true })
    } catch (err) {
      setError(err.message || 'Registrasi gagal.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <h1>Daftar Akun Peneliti</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Nama Lengkap
          <input
            type="text"
            name="fullName"
            autoComplete="name"
            value={form.fullName}
            onChange={handleChange}
            onInvalid={(e) => e.target.setCustomValidity('Nama lengkap wajib diisi.')}
            onInput={(e) => e.target.setCustomValidity('')}
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={error ? errorId : undefined}
            required
          />
        </label>
        <label>
          Email
          <input
            type="email"
            name="email"
            autoComplete="email"
            value={form.email}
            onChange={handleChange}
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
            autoComplete="new-password"
            value={form.password}
            onChange={handleChange}
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
          {submitting ? 'Memproses...' : 'Daftar'}
        </button>
      </form>
      <p>
        Sudah punya akun? <Link to="/login">Masuk</Link>
      </p>
    </div>
  )
}
