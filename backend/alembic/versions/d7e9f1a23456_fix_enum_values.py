"""add uppercase enum values matching SQLAlchemy Enum names

Revision ID: d7e9f1a23456
Revises: c6d8e9f01234
Create Date: 2026-11-19 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "d7e9f1a23456"
down_revision: Union[str, Sequence[str], None] = "c6d8e9f01234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PIPELINE_STEP_NAMES = [
    "INSPECTION",
    "STRUCTURE",
    "MULTILOCUS",
    "POSTGWAS",
    "ANNOTATION",
]

JOB_STATUS_NAMES = [
    "RUNNING_INSPECTION",
    "RUNNING_STRUCTURE",
    "RUNNING_MULTILOCUS",
    "RUNNING_POSTGWAS",
    "RUNNING_ANNOTATION",
]

RESULT_TYPE_NAMES = [
    "GWAS_NORMALIZED_TSV",
    "INSPECTION_REPORT",
    "QC_COMPARISON",
    "PCA_DATA",
    "KINSHIP_DATA",
    "LD_DECAY",
    "MODEL_RECOMMENDATION",
    "THRESHOLDS",
    "LEAD_SNPS",
    "MULTILOCUS_RESULTS",
    "PEAK_COMPARISON",
    "CANDIDATE_GENES",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for val in PIPELINE_STEP_NAMES:
                op.execute(f"ALTER TYPE pipeline_step ADD VALUE IF NOT EXISTS '{val}'")
            for val in JOB_STATUS_NAMES:
                op.execute(f"ALTER TYPE job_status ADD VALUE IF NOT EXISTS '{val}'")
            for val in RESULT_TYPE_NAMES:
                op.execute(f"ALTER TYPE result_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    # PostgreSQL tidak mendukung penghapusan nilai enum secara aman tanpa rebuild tipe.
    pass
