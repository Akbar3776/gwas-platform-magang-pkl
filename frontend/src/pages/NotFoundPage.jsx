import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <section className="not-found-page" aria-labelledby="not-found-heading">
      <p className="not-found-code">404</p>
      <h1 id="not-found-heading">Halaman Tidak Ditemukan (404)</h1>
      <p className="not-found-desc">
        Alamat halaman yang Anda buka tidak tersedia atau telah dipindahkan.
      </p>
      <div className="not-found-actions">
        <Link to="/upload" className="btn-primary-link">
          Ke Workspace
        </Link>
        <Link to="/login" className="btn-secondary-link">
          Halaman Masuk
        </Link>
      </div>
    </section>
  );
}
