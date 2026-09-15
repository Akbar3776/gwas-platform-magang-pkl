import { useCallback, useState } from "react";
import { triggerFullPipeline } from "../api/jobsApi";
import { JobStatusTracker } from "../components/JobStatusTracker";
import { VcfUploader } from "../components/VcfUploader";

// Path reference panel adalah path internal worker Docker, bukan path komputer user.
const REFERENCE_PANELS = [
  {
    value: "/data/reference-panels/1000g_phase3_grch38/reference.vcf.gz",
    label: "1000 Genomes Phase 3 - GRCh38",
  },
  {
    value: "/data/reference-panels/1000g_phase3_grch37/reference.vcf.gz",
    label: "1000 Genomes Phase 3 - GRCh37",
  },
];

const INITIAL_FORM = {
  maf: "0.01",
  geno: "0.05",
  mind: "0.1",
  hwe: "0.000001",
  reference_vcf_path: REFERENCE_PANELS[0].value,
  nthreads: "2",
  beagle_max_memory: "-Xmx8g",
  phenotype_path: "",
  model: "lmm",
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: "grid", gap: 6, color: "#334155", fontSize: 13 }}>
      <span style={{ fontWeight: 700 }}>{label}</span>
      {children}
      {hint && <span style={{ color: "#64748b", fontSize: 11 }}>{hint}</span>}
    </label>
  );
}

const inputStyle = {
  width: "100%",
  boxSizing: "border-box",
  border: "1px solid #cbd5e1",
  borderRadius: 7,
  padding: "9px 10px",
  color: "#0f172a",
  background: "#fff",
};

export function GwasUploadPage() {
  const [datasetId, setDatasetId] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [form, setForm] = useState(INITIAL_FORM);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleDatasetReady = useCallback((readyDatasetId) => {
    setDatasetId(readyDatasetId);
    setJobId(null);
    setError(null);
  }, []);

  const updateField = (event) => {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!datasetId) return;
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await triggerFullPipeline(datasetId, {
        qc: {
          maf: Number(form.maf),
          geno: Number(form.geno),
          mind: Number(form.mind),
          hwe: Number(form.hwe),
        },
        imputation: {
          reference_vcf_path: form.reference_vcf_path,
          nthreads: Number(form.nthreads),
          beagle_max_memory: form.beagle_max_memory,
        },
        gwas: {
          phenotype_path: form.phenotype_path,
          model: form.model,
        },
      });
      setJobId(response.job_id);
    } catch (requestError) {
      setError(
        requestError.response?.data?.detail ||
          requestError.message ||
          "Pipeline tidak dapat dimulai. Periksa konfigurasi Anda.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main style={{ maxWidth: 860, margin: "0 auto", padding: "28px 20px", color: "#0f172a" }}>
      <header style={{ marginBottom: 24 }}>
        <p style={{ margin: 0, color: "#2563eb", fontSize: 12, fontWeight: 800, letterSpacing: "0.08em" }}>GWAS WORKSPACE</p>
        <h1 style={{ margin: "6px 0", fontSize: 28 }}>Siapkan analisis Anda</h1>
        <p style={{ margin: 0, color: "#64748b" }}>Unggah VCF, pilih pengaturan yang sesuai, lalu pantau seluruh proses dari satu halaman.</p>
      </header>

      <section style={{ display: "grid", gap: 18 }}>
        <div style={{ padding: 18, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
          <h2 style={{ margin: "0 0 12px", fontSize: 17 }}>1. Data genotipe</h2>
          <VcfUploader onDatasetReady={handleDatasetReady} />
          {datasetId && <p style={{ margin: "12px 0 0", color: "#15803d", fontSize: 13 }}>File siap diproses.</p>}
        </div>

        {datasetId && !jobId && (
          <form onSubmit={handleSubmit} style={{ display: "grid", gap: 18, padding: 18, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 17 }}>2. Konfigurasi pipeline</h2>
              <p style={{ margin: "6px 0 0", color: "#64748b", fontSize: 13 }}>Nilai awal dipilih agar aman untuk percobaan pertama. Pengaturan ini dapat disesuaikan oleh peneliti.</p>
            </div>

            <fieldset style={{ display: "grid", gap: 12, border: 0, padding: 0, margin: 0 }}>
              <legend style={{ fontWeight: 800, marginBottom: 2 }}>Quality Control</legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
                <Field label="MAF" hint="Minimum frekuensi alel minor">
                  <input name="maf" type="number" min="0" max="1" step="0.001" value={form.maf} onChange={updateField} style={inputStyle} required />
                </Field>
                <Field label="GENO" hint="Batas missingness varian">
                  <input name="geno" type="number" min="0" max="1" step="0.01" value={form.geno} onChange={updateField} style={inputStyle} required />
                </Field>
                <Field label="MIND" hint="Batas missingness sampel">
                  <input name="mind" type="number" min="0" max="1" step="0.01" value={form.mind} onChange={updateField} style={inputStyle} required />
                </Field>
                <Field label="HWE" hint="Ambang Hardy-Weinberg">
                  <input name="hwe" type="number" min="0" max="1" step="any" value={form.hwe} onChange={updateField} style={inputStyle} required />
                </Field>
              </div>
            </fieldset>

            <fieldset style={{ display: "grid", gap: 12, border: 0, padding: 0, margin: 0 }}>
              <legend style={{ fontWeight: 800, marginBottom: 2 }}>Imputasi</legend>
              <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
                <Field label="Reference panel">
                  <select name="reference_vcf_path" value={form.reference_vcf_path} onChange={updateField} style={inputStyle}>
                    {REFERENCE_PANELS.map((panel) => <option key={panel.value} value={panel.value}>{panel.label}</option>)}
                  </select>
                </Field>
                <Field label="Thread komputasi" hint="Jumlah thread Beagle">
                  <input name="nthreads" type="number" min="1" max="64" value={form.nthreads} onChange={updateField} style={inputStyle} required />
                </Field>
                <Field label="Batas memory JVM" hint="Contoh: -Xmx8g">
                  <input name="beagle_max_memory" pattern="^-Xmx\d+[mMgG]$" value={form.beagle_max_memory} onChange={updateField} style={inputStyle} required />
                </Field>
              </div>
            </fieldset>

            <fieldset style={{ display: "grid", gap: 12, border: 0, padding: 0, margin: 0 }}>
              <legend style={{ fontWeight: 800, marginBottom: 2 }}>Analisis GWAS</legend>
              <Field label="File fenotip" hint="Path file yang tersedia pada volume worker">
                <input name="phenotype_path" value={form.phenotype_path} onChange={updateField} style={inputStyle} placeholder="/data/storage/datasets/.../phenotype.tsv" required />
              </Field>
              <Field label="Model statistik">
                <select name="model" value={form.model} onChange={updateField} style={inputStyle}>
                  <option value="lmm">Linear Mixed Model (LMM)</option>
                  <option value="lm">Linear Model (LM)</option>
                </select>
              </Field>
            </fieldset>

            {error && <p role="alert" style={{ margin: 0, padding: 12, borderRadius: 8, background: "#fef2f2", color: "#991b1b", fontSize: 13 }}>{error}</p>}
            <button type="submit" disabled={isSubmitting} style={{ border: 0, borderRadius: 8, padding: "12px 16px", background: isSubmitting ? "#94a3b8" : "#1d4ed8", color: "#fff", fontWeight: 800, cursor: isSubmitting ? "wait" : "pointer" }}>
              {isSubmitting ? "Menyiapkan pipeline..." : "Mulai analisis GWAS"}
            </button>
          </form>
        )}

        {jobId && (
          <div>
            <h2 style={{ margin: "0 0 10px", fontSize: 17 }}>3. Progress analisis</h2>
            <JobStatusTracker jobId={jobId} />
          </div>
        )}
      </section>
    </main>
  );
}
