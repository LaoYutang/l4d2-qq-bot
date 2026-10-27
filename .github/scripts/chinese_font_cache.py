"""Cache only an authenticated APT font package, never APT state or credentials."""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from email.parser import Parser
from pathlib import Path

PACKAGE = "fonts-noto-cjk"
UBUNTU_SOURCES = Path("/etc/apt/sources.list.d/ubuntu.sources")
APT_OPTIONS = [
    "-o",
    "Acquire::Retries=2",
    "-o",
    "Acquire::http::Timeout=30",
    "-o",
    "Acquire::https::Timeout=30",
    "-o",
    "Acquire::AllowInsecureRepositories=false",
    "-o",
    "Acquire::AllowDowngradeToInsecureRepositories=false",
    "-o",
    "APT::Get::AllowUnauthenticated=false",
]


def source_options():
    # Read the runner's existing Ubuntu sources, without unrelated third-party feeds.
    return ["-o", f"Dir::Etc::sourcelist={UBUNTU_SOURCES}", "-o", "Dir::Etc::sourceparts=-"]


def run(command, seconds=15, *, capture=False, cwd=None):
    # Privileged commands need a privileged timeout process to kill their children.
    bounded = ["timeout", "--kill-after=10s", f"{seconds}s"]
    bounded = ["sudo", *bounded, *command[1:]] if command[0] == "sudo" else [*bounded, *command]
    return subprocess.run(
        bounded,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE if capture else None,
        env={**os.environ, "LC_ALL": "C"},
        cwd=cwd,
    ).stdout


def package_metadata(text, architecture):
    records = set()
    fields = ("Package", "Version", "Architecture", "Filename", "SHA256")
    for paragraph in text.strip().split("\n\n"):
        record = Parser().parsestr(paragraph)
        values = tuple(record.get(field, "") for field in fields)
        if values[0] == PACKAGE and values[2] in {"all", architecture} and all(values):
            records.add(values)
    if len(records) != 1:
        raise ValueError(
            "Expected one complete font package record from authenticated APT indexes."
        )
    metadata = dict(zip(fields, records.pop(), strict=True))
    if not re.fullmatch(r"[0-9a-f]{64}", metadata["SHA256"]):
        raise ValueError("APT package record has no valid SHA256.")
    return metadata


def cache_key(metadata, release, architecture):
    parts = [
        "noto-cjk-v1",
        os.environ["RUNNER_OS"],
        os.environ["RUNNER_ARCH"],
        release["ID"],
        release["VERSION_ID"],
        architecture,
        metadata["Version"],
        metadata["SHA256"],
    ]
    if any(not re.fullmatch(r"[A-Za-z0-9_.:+~-]+", part) for part in parts):
        raise ValueError("Invalid font cache key component.")
    return "-".join(parts)


def prepare(directory):
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu":
        raise ValueError("The CI font cache requires the Ubuntu runner's signed APT sources.")
    if not UBUNTU_SOURCES.is_file():
        raise ValueError(f"Expected the Ubuntu runner's existing sources at {UBUNTU_SOURCES}.")
    # Keep the runner's official repository configuration and signature checks.
    # Strict update prevents a partial failure from silently using stale indexes.
    run(["sudo", "apt-get", *source_options(), *APT_OPTIONS, "--error-on=any", "update"], 120)
    architecture = run(["dpkg", "--print-architecture"], capture=True).strip()
    metadata = package_metadata(
        run(["apt-cache", *source_options(), "show", "--no-all-versions", PACKAGE], capture=True),
        architecture,
    )
    directory.mkdir(parents=True, exist_ok=True)
    # This fresh manifest is deliberately outside the cache action's file path.
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    key = cache_key(metadata, release, architecture)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"cache-path={directory / 'fonts-noto-cjk.deb'}\ncache-key={key}\n")
    print(f"APT font candidate: {metadata['Version']} ({metadata['Architecture']})")
    print(f"Font cache key: {key}")


def verified(archive, metadata):
    if archive.is_symlink() or not archive.is_file():
        return False
    with archive.open("rb") as contents:
        if hashlib.file_digest(contents, "sha256").hexdigest() != metadata["SHA256"]:
            return False
    try:
        fields = run(
            [
                "dpkg-deb",
                "--show",
                "--showformat=${Package}\n${Version}\n${Architecture}",
                str(archive),
            ],
            capture=True,
        ).splitlines()
    except subprocess.CalledProcessError:
        return False
    return fields == [PACKAGE, metadata["Version"], metadata["Architecture"]]


def install(directory):
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    archive = directory / "fonts-noto-cjk.deb"
    if verified(archive, metadata):
        print("Font package cache verified; no font download needed.")
    else:
        if archive.exists() or archive.is_symlink():
            print("Rejected font cache; downloading again. An existing cache key is immutable.")
            archive.unlink()
        else:
            print("Font package cache miss; downloading the exact APT candidate.")
        for attempt in range(2):
            with tempfile.TemporaryDirectory(prefix="font-download-", dir=directory) as temporary:
                try:
                    # APT verifies the exact version against its signed indexes as usual.
                    # Download as the runner, not root, and keep partial files out of cache.
                    run(
                        [
                            "apt-get",
                            *source_options(),
                            *APT_OPTIONS,
                            "download",
                            f"{PACKAGE}={metadata['Version']}",
                        ],
                        120,
                        cwd=temporary,
                    )
                except subprocess.CalledProcessError:
                    if attempt == 1:
                        raise
                    print("Font download failed or timed out; retrying once.")
                    continue
                downloads = list(Path(temporary).glob("*.deb"))
                if len(downloads) != 1 or not verified(downloads[0], metadata):
                    raise ValueError(
                        "Downloaded font package failed SHA256 or package identity check."
                    )
                downloads[0].replace(archive)
                break
    print(f"Installing verified font package {metadata['Version']}.")
    run(
        [
            "sudo",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            *source_options(),
            *APT_OPTIONS,
            "install",
            "--no-install-recommends",
            "-y",
            str(archive),
        ],
        90,
    )


if __name__ == "__main__":
    cache_directory = Path(os.environ["RUNNER_TEMP"]) / "l4d2-qq-bot-font"
    if sys.argv[1:] == ["prepare"]:
        prepare(cache_directory)
    elif sys.argv[1:] == ["install"]:
        install(cache_directory)
    else:
        raise SystemExit("Usage: chinese_font_cache.py prepare|install")
