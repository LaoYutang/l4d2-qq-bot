import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/chinese_font_cache.py"
SPEC = importlib.util.spec_from_file_location("chinese_font_cache", SCRIPT)
font_cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(font_cache)
DATA = b"fixture font package, never a real executable package"
METADATA = {
    "Package": "fonts-noto-cjk",
    "Version": "1:20230817+repack1-3",
    "Architecture": "all",
    "Filename": "pool/main/f/fonts-noto-cjk/font.deb",
    "SHA256": hashlib.sha256(DATA).hexdigest(),
}


def metadata_text(metadata):
    return "\n".join(f"{key}: {value}" for key, value in metadata.items()) + "\n"


@pytest.fixture
def directory(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps(METADATA), encoding="utf-8")
    return tmp_path


def test_metadata_requires_one_complete_authenticated_package_record():
    text = metadata_text(METADATA)
    assert font_cache.package_metadata(text, "amd64") == METADATA
    assert font_cache.package_metadata(text + "\n" + text, "amd64") == METADATA


@pytest.mark.parametrize(
    "changes",
    [
        {"SHA256": ""},
        {"SHA256": "not-a-sha256"},
        {"Filename": ""},
        {"Package": "another-package"},
        {"Architecture": "arm64"},
        {"Version": ""},
    ],
)
def test_incomplete_or_wrong_package_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        font_cache.package_metadata(metadata_text({**METADATA, **changes}), "amd64")


def test_conflicting_package_records_are_rejected():
    other = {**METADATA, "SHA256": "a" * 64}
    with pytest.raises(ValueError):
        font_cache.package_metadata(metadata_text(METADATA) + "\n" + metadata_text(other), "amd64")


@pytest.mark.parametrize("changed", ["os", "runner_arch", "ubuntu", "apt_arch", "version", "hash"])
def test_cache_key_separates_system_architecture_and_exact_package(monkeypatch, changed):
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    release = {"ID": "ubuntu", "VERSION_ID": "24.04"}
    original = font_cache.cache_key(METADATA, release, "amd64")
    metadata = METADATA.copy()
    architecture = "amd64"
    if changed == "os":
        monkeypatch.setenv("RUNNER_OS", "another-os")
    elif changed == "runner_arch":
        monkeypatch.setenv("RUNNER_ARCH", "ARM64")
    elif changed == "ubuntu":
        release["VERSION_ID"] = "26.04"
    elif changed == "apt_arch":
        architecture = "arm64"
    elif changed == "version":
        metadata["Version"] = "2.0-1"
    else:
        metadata["SHA256"] = "a" * 64
    assert font_cache.cache_key(metadata, release, architecture) != original


def test_prepare_keeps_fresh_metadata_outside_the_cached_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    sources = tmp_path / "ubuntu.sources"
    sources.write_text("fixture, never used by a real APT command", encoding="utf-8")
    monkeypatch.setattr(font_cache, "UBUNTU_SOURCES", sources)
    monkeypatch.setattr(
        font_cache.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    runner = Mock(side_effect=[None, "amd64\n", metadata_text(METADATA)])
    monkeypatch.setattr(font_cache, "run", runner)
    cache = tmp_path / "font-cache"
    font_cache.prepare(cache)
    assert json.loads((cache / "metadata.json").read_text()) == METADATA
    assert f"cache-path={cache / 'fonts-noto-cjk.deb'}\n" in output.read_text()
    assert "metadata.json" not in output.read_text()
    update, seconds = runner.call_args_list[0].args
    assert seconds == 120 and "--error-on=any" in update
    assert "Acquire::AllowInsecureRepositories=false" in update
    assert "APT::Get::AllowUnauthenticated=false" in update
    assert f"Dir::Etc::sourcelist={sources}" in update
    assert "Dir::Etc::sourceparts=-" in update
    assert f"Dir::Etc::sourcelist={sources}" in runner.call_args_list[2].args[0]


def test_index_failure_does_not_write_cache_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(font_cache.platform, "freedesktop_os_release", lambda: {"ID": "ubuntu"})
    sources = tmp_path / "ubuntu.sources"
    sources.touch()
    monkeypatch.setattr(font_cache, "UBUNTU_SOURCES", sources)
    runner = Mock(side_effect=subprocess.CalledProcessError(100, ["apt-get", "update"]))
    monkeypatch.setattr(font_cache, "run", runner)
    with pytest.raises(subprocess.CalledProcessError):
        font_cache.prepare(tmp_path / "font-cache")
    assert not (tmp_path / "font-cache").exists()
    runner.assert_called_once()


def test_missing_official_source_configuration_fails_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(font_cache.platform, "freedesktop_os_release", lambda: {"ID": "ubuntu"})
    monkeypatch.setattr(font_cache, "UBUNTU_SOURCES", tmp_path / "missing.sources")
    runner = Mock()
    monkeypatch.setattr(font_cache, "run", runner)
    with pytest.raises(ValueError, match="existing sources"):
        font_cache.prepare(tmp_path / "font-cache")
    runner.assert_not_called()


@pytest.mark.parametrize("state", ["cold", "warm", "corrupt"])
def test_cold_warm_and_corrupt_cache_only_install_verified_bytes(monkeypatch, directory, state):
    archive = directory / "fonts-noto-cjk.deb"
    if state != "cold":
        archive.write_bytes(DATA if state == "warm" else b"corrupted cache")
    downloads, unpacks, installs = [], [], []

    def run(command, seconds=15, *, capture=False, cwd=None):
        if command[0] == "dpkg-deb":
            return "\n".join(METADATA[key] for key in ("Package", "Version", "Architecture"))
        if command[0] == "dpkg-query":
            return f"ii \n{METADATA['Version']}"
        if command[0] == "apt-get":
            assert command[-2:] == ["download", f"fonts-noto-cjk={METADATA['Version']}"]
            assert seconds == 120
            downloads.append(command)
            (Path(cwd) / "downloaded.deb").write_bytes(DATA)
        elif command[:2] == ["sudo", "dpkg"]:
            assert command[2:] == ["--unpack", str(archive)] and seconds == 30
            assert archive.read_bytes() == DATA
            unpacks.append(command)
        else:
            assert command[:3] == ["sudo", "env", "DEBIAN_FRONTEND=noninteractive"]
            assert command[-1] == f"fonts-noto-cjk={METADATA['Version']}" and seconds == 90
            assert "--fix-broken" in command and "--no-remove" in command and len(unpacks) == 1
            assert archive.read_bytes() == DATA
            installs.append(command)

    monkeypatch.setattr(font_cache, "run", run)
    font_cache.install(directory)
    assert len(downloads) == (0 if state == "warm" else 1)
    assert len(unpacks) == len(installs) == 1
    assert archive.read_bytes() == DATA
    assert not list(directory.glob("font-download-*"))


@pytest.mark.parametrize("status", ["iU ", "wrong-version"])
def test_configured_package_status_and_version_are_required(monkeypatch, directory, status):
    (directory / "fonts-noto-cjk.deb").write_bytes(DATA)

    def run(command, *args, **kwargs):
        if command[0] == "dpkg-deb":
            return "\n".join(METADATA[key] for key in ("Package", "Version", "Architecture"))
        if command[0] == "dpkg-query":
            return f"{status}\n{METADATA['Version']}" if status == "iU " else "ii \nwrong-version"

    monkeypatch.setattr(font_cache, "run", run)
    with pytest.raises(ValueError, match="not fully configured"):
        font_cache.install(directory)


def test_dependency_configuration_failure_is_not_suppressed(monkeypatch, directory):
    (directory / "fonts-noto-cjk.deb").write_bytes(DATA)
    runner = Mock(
        side_effect=[
            "\n".join(METADATA[key] for key in ("Package", "Version", "Architecture")),
            None,
            subprocess.CalledProcessError(100, ["apt-get", "install", "--fix-broken"]),
        ]
    )
    monkeypatch.setattr(font_cache, "run", runner)
    with pytest.raises(subprocess.CalledProcessError):
        font_cache.install(directory)
    assert runner.call_count == 3


@pytest.mark.parametrize("failure", ["sha256", "identity"])
def test_untrusted_download_is_never_cached_or_installed(monkeypatch, directory, failure):
    def run(command, seconds=15, *, capture=False, cwd=None):
        if command[0] == "apt-get":
            (Path(cwd) / "downloaded.deb").write_bytes(b"tampered" if failure == "sha256" else DATA)
        elif command[0] == "dpkg-deb":
            return "wrong-package\nwrong-version\nall"
        else:
            pytest.fail("Unverified bytes reached privileged installation")

    monkeypatch.setattr(font_cache, "run", run)
    with pytest.raises(ValueError, match="SHA256 or package identity"):
        font_cache.install(directory)
    assert not (directory / "fonts-noto-cjk.deb").exists()


def test_download_timeout_retries_only_once_and_fails_without_install(monkeypatch, directory):
    runner = Mock(side_effect=subprocess.CalledProcessError(124, ["timeout", "apt-get"]))
    monkeypatch.setattr(font_cache, "run", runner)
    with pytest.raises(subprocess.CalledProcessError):
        font_cache.install(directory)
    assert runner.call_count == 2
    assert all(
        call.args[0][0] == "apt-get" and call.args[1] == 120 for call in runner.call_args_list
    )


def test_command_timeout_applies_to_the_process_group(monkeypatch):
    process = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="verified"))
    monkeypatch.setattr(font_cache.subprocess, "run", process)
    assert font_cache.run(["sudo", "apt-get", "update"], 120, capture=True) == "verified"
    assert process.call_args.args[0] == [
        "sudo",
        "timeout",
        "--kill-after=10s",
        "120s",
        "apt-get",
        "update",
    ]
    assert process.call_args.kwargs["check"] is True
    assert process.call_args.kwargs["env"]["LC_ALL"] == "C"
