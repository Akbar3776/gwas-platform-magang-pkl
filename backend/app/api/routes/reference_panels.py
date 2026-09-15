"""
api/routers/reference_panels.py

GET /reference-panels
Dipakai React untuk mengisi dropdown pilihan populasi/reference panel pada
wizard konfigurasi pipeline (langkah 5 di UI/UX flow: "Konfigurasi Pipeline").

PENTING: response TIDAK PERNAH menyertakan `vcf_paths` / `genetic_map_paths`.
Itu adalah path fisik di server dan hanya boleh diakses oleh Celery task saat
imputasi benar-benar dieksekusi (lihat tasks/imputation_tasks.py), bukan
dikirim ke browser.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import ReferencePanel

router = APIRouter(prefix="/reference-panels", tags=["reference-panels"])


class ReferencePanelOut(BaseModel):
    id: str
    name: str
    population: str
    genome_build: str
    description: Optional[str] = None
    available_chromosomes: int

    class Config:
        from_attributes = True


@router.get("", response_model=list[ReferencePanelOut])
def list_reference_panels(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    panels = (
        db.query(ReferencePanel)
        .filter(ReferencePanel.is_active.is_(True))
        .order_by(ReferencePanel.genome_build, ReferencePanel.name, ReferencePanel.population)
        .all()
    )

    return [
        ReferencePanelOut(
            id=str(panel.id),
            name=panel.name,
            population=panel.population,
            genome_build=panel.genome_build,
            description=panel.description,
            available_chromosomes=len(panel.vcf_paths or {}),
        )
        for panel in panels
    ]
