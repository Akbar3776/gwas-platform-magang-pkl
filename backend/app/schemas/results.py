from typing import List

from pydantic import BaseModel, Field


class ManhattanDataResponse(BaseModel):
    """
    Response format KOLOM (columnar), bukan list-of-objects, karena:
      - Payload JSON jauh lebih kecil (nama key CHR/BP/SNP/LOG10_P tidak
        diulang di setiap baris seperti pada list-of-dicts).
      - Lebih mudah dikonsumsi sebagai typed array di frontend (mis. untuk
        rendering scatter plot berbasis WebGL/canvas dengan puluhan ribu titik).
    """

    job_id: str
    total_points: int = Field(..., description="Jumlah titik SNP setelah downsampling")
    chr: List[int] = Field(..., description="Nomor kromosom per titik (indeks sejajar dengan bp/snp/log10_p)")
    bp: List[int] = Field(..., description="Posisi basepair per titik")
    snp: List[str] = Field(..., description="ID SNP (rsID) per titik")
    log10_p: List[float] = Field(..., description="-log10(p-value) per titik")

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "b3f1e2b0-4c1a-4e9e-8b0a-2f6a1c9d0e11",
                "total_points": 3,
                "chr": [1, 1, 2],
                "bp": [752721, 798959, 128882],
                "snp": ["rs3131972", "rs12562034", "rs4040617"],
                "log10_p": [0.52, 1.87, 4.31],
            }
        }
    }
