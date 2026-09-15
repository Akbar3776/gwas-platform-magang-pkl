import { useParams, useNavigate } from 'react-router-dom'
import { JobStatusTracker } from '../components/JobStatusTracker.jsx'

// JobStatusTracker adalah named export (dipakai juga di GwasUploadPage.jsx
// dengan prop `jobId`). ASUMSI YANG MASIH PERLU DICEK: apakah ia punya
// callback `onComplete` — kalau tidak ada, hapus prop tsb dan biarkan user
// pindah ke halaman hasil secara manual (mis. lewat tombol/link).
export default function JobStatusPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()

  return (
    <div className="job-status-page">
      <h1>Progress Analisis</h1>
      <JobStatusTracker
        jobId={jobId}
        onComplete={() => navigate(`/jobs/${jobId}/results`)}
      />
    </div>
  )
}
