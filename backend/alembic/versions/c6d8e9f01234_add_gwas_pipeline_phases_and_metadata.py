"""add gwas pipeline phases and metadata

Revision ID: c6d8e9f01234
Revises: b5f7c68dd45f
Create Date: 2026-10-28 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c6d8e9f01234"
down_revision: Union[str, Sequence[str], None] = "b5f7c68dd45f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_PIPELINE_STEPS = [
    "inspection",
    "structure",
    "multilocus",
    "postgwas",
    "annotation",
]

NEW_JOB_STATUSES = [
    "running_inspection",
    "running_structure",
    "running_multilocus",
    "running_postgwas",
    "running_annotation",
]

NEW_RESULT_TYPES = [
    "inspection_report",
    "qc_comparison",
    "pca_data",
    "kinship_data",
    "ld_decay",
    "model_recommendation",
    "thresholds",
    "lead_snps",
    "multilocus_results",
    "peak_comparison",
    "candidate_genes",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for val in NEW_PIPELINE_STEPS:
                op.execute(f"ALTER TYPE pipeline_step ADD VALUE IF NOT EXISTS '{val}'")
            for val in NEW_JOB_STATUSES:
                op.execute(f"ALTER TYPE job_status ADD VALUE IF NOT EXISTS '{val}'")
            for val in NEW_RESULT_TYPES:
                op.execute(f"ALTER TYPE result_type ADD VALUE IF NOT EXISTS '{val}'")

    op.add_column("datasets", sa.Column("annotation_file_path", sa.String(length=1024), nullable=True))
    json_col = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()
    op.add_column("datasets", sa.Column("extra_metadata", json_col, nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "extra_metadata")
    op.drop_column("datasets", "annotation_file_path")
