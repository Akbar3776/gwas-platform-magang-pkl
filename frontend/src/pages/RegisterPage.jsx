import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

// Placeholder minimal — pakai register() dari AuthContext.jsx langsung
// (bukan panggil apiClient sendiri) supaya endpoint & error handling konsisten.
export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ fullName: '', email: '', password: '' })
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const handleChange = (e) => setForm((f) => ({ ...f, [e.target.name]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      // Signature: register(email, password, fullName) — lihat AuthContext.jsx.
      // Registrasi berhasil TIDAK auto-login, jadi arahkan ke /login.
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
          <input name="fullName" value={form.fullName} onChange={handleChange} required />
        </label>
        <label>
          Email
          <input type="email" name="email" value={form.email} onChange={handleChange} required />
        </label>
        <label>
          Password
          <input type="password" name="password" value={form.password} onChange={handleChange} required />
        </label>
        {error && <p className="form-error">{error}</p>}
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
