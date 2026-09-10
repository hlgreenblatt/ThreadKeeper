import os

import helper


def test_memory_file_path_uses_configured_staging_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGACLAW_MEMORY_DIR", str(tmp_path))
    assert helper.memory_file_path() == os.path.join(str(tmp_path), "history.metta")


def test_memory_file_path_preserves_legacy_default(monkeypatch):
    monkeypatch.delenv("OMEGACLAW_MEMORY_DIR", raising=False)
    assert helper.memory_file_path() == os.path.join(
        "repos", "OmegaClaw-Core", "memory", "history.metta"
    )
