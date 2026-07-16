from pathlib import Path

import pytest

from benchmarks.locomo.runner import DockerComposeController, RunnerError


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = '') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ''


class _CommandRunner:
    def __init__(self, responses: list[_Completed]) -> None:
        self.responses = responses
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> _Completed:
        self.commands.append(command)
        return self.responses.pop(0)


def test_compose_restart_uses_stop_and_start_without_volume_deletion(tmp_path: Path) -> None:
    commands = _CommandRunner([_Completed(), _Completed()])
    docker = DockerComposeController(
        repository=tmp_path,
        project='locomo-test',
        run_command=commands,
    )

    docker.stop()
    docker.start_existing()

    flattened = [' '.join(command) for command in commands.commands]
    assert any('stop neo4j' in command for command in flattened)
    assert any('start neo4j' in command for command in flattened)
    assert all('down' not in command and '-v' not in command for command in commands.commands)


def test_new_run_rejects_existing_volume(tmp_path: Path) -> None:
    commands = _CommandRunner([_Completed(returncode=0)])
    docker = DockerComposeController(
        repository=tmp_path,
        project='locomo-test',
        run_command=commands,
    )

    with pytest.raises(RunnerError, match='already exists'):
        docker.require_new_volume()
