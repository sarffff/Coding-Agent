import os
import sys
from pathlib import Path

import pytest
from conftest import build_settings

from app.repository_service import RepositoryError, RepositoryService
from app.test_service import TestService as ValidationService


@pytest.mark.parametrize("name", [".venv", "venv"])
def test_project_python_is_used_for_tests_and_static_checks(tmp_path, name):
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / "test_value.py").write_text("def test_value(): pass", encoding="utf-8")
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    interpreter = directory / name / relative
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    settings = build_settings(directory, tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.get(repositories.register(str(directory)).id)
    service = ValidationService(repositories, settings)
    for kind in ("auto", "pytest", "static"):
        assert service._detect_command(repository, kind)[0] == str(interpreter)


def test_broken_project_environment_does_not_fall_back_to_api_python(tmp_path):
    directory = tmp_path / "project"
    (directory / ".venv").mkdir(parents=True)
    settings = build_settings(directory, tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.get(repositories.register(str(directory)).id)
    service = ValidationService(repositories, settings)
    with pytest.raises(RepositoryError) as error:
        service._detect_command(repository, "pytest")
    assert error.value.code == "PROJECT_PYTHON_UNAVAILABLE"


def test_no_collected_tests_is_an_environment_blocker(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    settings = build_settings(directory, tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.get(repositories.register(str(directory)).id)
    service = ValidationService(repositories, settings)
    assert service._detect_command(repository, "pytest")[0] == sys.executable
    result = service.run("no-tests", repository.id, "pytest", 15)
    assert result.status == "blocked"
    assert result.exit_code == 5
    assert "Restart the API" not in result.stderr
    assert result.failed_tests == []

