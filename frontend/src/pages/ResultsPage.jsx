import { Link, useParams } from "react-router-dom";
import GwasResultsPanel from "../components/GwasResultsPanel.jsx";
import JobFilesPanel from "../components/JobFilesPanel.jsx";
import JobProcessLogs from "../components/JobProcessLogs.jsx";
import ResultDashboard from "../components/ResultDashboard.jsx";

export default function ResultsPage() {
  const { jobId } = useParams();

  return (
    <div className="results-page">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <p style={{ margin: 0, color: "#2563eb", fontSize: 12, fontWeight: 800, letterSpacing: "0.08em" }}>
            GWAS RESULTS
          </p>
          <h1 style={{ margin: "4px 0 0", fontSize: 26 }}>Hasil Analisis GWAS</h1>
        </div>
        <Link
          to="/upload"
          style={{
            padding: "8px 14px",
            borderRadius: 8,
            border: "1px solid #cbd5e1",
            background: "#fff",
            color: "#0f172a",
            textDecoration: "none",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          ← Workspace & Riwayat Job
        </Link>
      </div>

      <div style={{ display: "grid", gap: 24 }}>
        <ResultDashboard jobId={jobId} />
        <GwasResultsPanel jobId={jobId} />
        <JobFilesPanel jobId={jobId} isRunning={false} />
        <JobProcessLogs jobId={jobId} defaultExpanded={true} />
      </div>
    </div>
  );
}
