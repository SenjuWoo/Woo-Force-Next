"""Build the beta upgrade and developer tools from immutable committed source.

Needs only Python and Git. Writes a reproducible ZIP, checksums and provenance to --out.
"""
import argparse
import hashlib
import io
import json
import platform
import re
import subprocess
import tarfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ("external/mods/woo_brain.lua", "data/woo_brain.zss", "requirements.txt", "LICENSE")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def build_upgrade(root, out):
    """Package only the selected commit's runtime and tools, excluding all player data."""
    root, out = Path(root), Path(out)
    commit = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    tracked = git(root, "ls-tree", "-r", "-z", "--name-only", commit).decode("utf-8").split("\0")
    if not set(REQUIRED).issubset(tracked):
        raise ValueError("the committed brain, bridge, requirements and license are required")
    selected = sorted(set(REQUIRED) | {p for p in tracked if p.startswith("woo/tools/")
                                      and PurePosixPath(p).suffix in (".py", ".lua")})
    payload, destinations = {}, set()
    packed_source = git(root, "archive", "--format=tar", commit, "--", *selected)
    with tarfile.open(fileobj=io.BytesIO(packed_source), mode="r:") as source:
        seen = set()
        for member in source:
            if member.isdir():
                continue
            name = member.name
            path = PurePosixPath(name)
            if (name not in selected or not member.isfile() or path.is_absolute()
                    or ".." in path.parts or path.as_posix() != name or "\\" in name or ":" in name):
                raise ValueError(f"unsafe source entry: {name}")
            target = {"requirements.txt": "woo/tools/requirements.txt", "LICENSE": "woo/LICENSE.txt"}.get(name, name)
            if target.casefold() in destinations:
                raise ValueError(f"duplicate destination: {target}")
            destinations.add(target.casefold())
            seen.add(name)
            payload[target] = source.extractfile(member).read()
    if seen != set(selected):
        raise ValueError("committed source archive is incomplete")
    version = re.search(rb"(?m)^local VERSION = '(\d+\.\d+\.\d+(?:-beta\.\d+)?)'", payload[REQUIRED[0]])
    if not version:
        raise ValueError("committed brain has no supported VERSION constant")
    version = version.group(1).decode("ascii")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"Woo-Force-Next-Upgrade-{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name, body in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, body, compresslevel=9)
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None or package.namelist() != sorted(payload):
            raise ValueError("built ZIP failed CRC or entry verification")
    receipt = {
        "version": version, "commit": commit,
        "toolchain": {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION},
        "archive": {"name": archive.name, "size": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "files": {name: {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                  for name, body in sorted(payload.items())},
    }
    manifest = out / "release-manifest.json"
    manifest.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in (archive, manifest)]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    receipt = build_upgrade(ROOT, args.out)
    print(f"built {receipt['archive']['name']} from {receipt['commit']} ({len(receipt['files'])} files)")


if __name__ == "__main__":
    main()
