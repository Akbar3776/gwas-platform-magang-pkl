from pathlib import Path

from app.core.config import settings
from app.services import upload_service
from app.tasks._common import stage_dir


def test_storage_root_is_under_pytest_temp_root(tmp_path_factory):
    storage_root = Path(settings.STORAGE_ROOT).resolve()
    pytest_root = tmp_path_factory.getbasetemp().resolve()

    assert storage_root.is_relative_to(pytest_root)


def test_storage_root_is_not_real_or_container_storage(tmp_path_factory):
    storage_root = Path(settings.STORAGE_ROOT).resolve()
    real_storage = (Path(__file__).resolve().parents[1] / "data" / "storage").resolve()

    assert storage_root != real_storage
    assert not str(storage_root).startswith("/data")


def test_upload_datasets_root_follows_isolated_storage_root():
    storage_root = Path(settings.STORAGE_ROOT).resolve()

    assert upload_service._DATASETS_ROOT.resolve().is_relative_to(storage_root)


def test_stage_dir_is_created_under_isolated_storage_root():
    storage_root = Path(settings.STORAGE_ROOT).resolve()

    result = stage_dir("00000000-0000-0000-0000-000000000001", "structure")

    assert result.exists()
    assert result.resolve().is_relative_to(storage_root)
