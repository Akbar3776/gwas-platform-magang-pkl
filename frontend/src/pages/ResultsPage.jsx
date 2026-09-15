import { useParams } from 'react-router-dom'
import ResultDashboard from '../components/ResultDashboard.jsx'

// ASUMSI (belum terverifikasi — file ResultDashboard.jsx belum diupload):
// - named export seperti VcfUploader/JobStatusTracker/GwasUploadPage
// - menerima prop `jobId` dan mengambil sendiri data hasil (mis. via useGwasResults)
// Kalau ternyata default export, ganti baris import di atas jadi:
//   import ResultDashboard from '../components/ResultDashboard.jsx'
export default function ResultsPage() {
  const { jobId } = useParams()

  return (
    <div className="results-page">
      <h1>Hasil Analisis GWAS</h1>
      <ResultDashboard jobId={jobId} />
    </div>
  )
}
