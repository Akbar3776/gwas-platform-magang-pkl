import { useParams, useNavigate } from 'react-router-dom'
import { JobFilesPanel } from '../components/JobFilesPanel.jsx'
import { JobStatusTracker } from '../components/JobStatusTracker.jsx'

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
      <JobFilesPanel jobId={jobId} isRunning={true} />
    </div>
  )
}
