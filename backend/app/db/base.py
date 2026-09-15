"""
Base declarative class untuk seluruh model ORM.

Dipisah dari `database.py` agar Alembic (env.py) bisa meng-import `Base`
tanpa ikut menjalankan setup engine/session sungguhan.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class untuk semua model ORM di aplikasi ini."""
    pass
