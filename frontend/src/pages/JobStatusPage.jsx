import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { JobFilesPanel } from '../components/JobFilesPanel.jsx'
import { JobStatusTracker } from '../components/JobStatusTracker.jsx'

const TERMINAL_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'FAILED', 'CANCELLED'])

export default function JobStatusPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [polledStatus, setPolledStatus] = useState('')
  const isJobRunning = !TERMINAL_STATUSES.has(String(polledStatus || '').toUpperCase())

  return (
    <div className="job-status-page">
      <h1>Progress Analisis</h1>
      <JobStatusTracker
        jobId={jobId}
        onComplete={() => navigate(`/jobs/${jobId}/results`)}
        onStatusChange={setPolledStatus}
      />
      <JobFilesPanel jobId={jobId} isRunning={isJobRunning} />
    </div>
  )
}
