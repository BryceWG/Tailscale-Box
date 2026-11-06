#!/usr/bin/env python3
"""Build installable ARM64 module ZIPs; never execute downloaded binaries."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tarfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STABLE = "https://pkgs.tailscale.com/stable/"
REPOSITORY = "https://github.com/BryceWG/Tailscale-Box"
FILES = (
    "module.prop", "customize.sh", "action.sh", "ctl.sh", "update.sh",
    "webui.sh", "service.sh", "uninstall.sh", "README.md",
    "LICENSE.tailscale", "LICENSE.reference",
)


def metadata(root=ROOT):
    values = dict(line.split("=", 1) for line in (root / "module.prop").read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", values["version"]):
        raise ValueError("Invalid module version")
    if not values["versionCode"].isdigit():
        raise ValueError("versionCode must be numeric")
    return values


def fetch(url):
    with urlopen(url, timeout=120) as response:
        return response.read()


def latest_binaries():
    meta = json.loads(fetch(STABLE + "?mode=json"))
    archive = meta["Tarballs"]["arm64"]
    match = re.fullmatch(r"tailscale_([0-9.]+)_arm64\.tgz", archive)
    if not match:
        raise ValueError("Invalid ARM64 archive in official metadata")
    content = fetch(STABLE + archive)
    expected = fetch(STABLE + archive + ".sha256").decode().split()[0]
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected:
        raise ValueError("Official archive SHA256 mismatch")
    binaries = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        for name in ("tailscale", "tailscaled"):
            member = tar.getmember(f"tailscale_{match[1]}_arm64/{name}")
            if not member.isfile():
                raise ValueError(f"Not a regular binary: {name}")
            binaries[f"bin/{name}"] = tar.extractfile(member).read()
    provenance = f"Tailscale {match[1]} (ARM64)\nSource: {STABLE}{archive}\nArchive SHA256: {digest}\n"
    for name, binary in binaries.items():
        provenance += f"{hashlib.sha256(binary).hexdigest()}  {name}\n"
    return binaries, provenance


def build(variant, output, root=ROOT):
    version = metadata(root)["version"]
    files = {name: (root / name).read_bytes() for name in FILES}
    manifest_name = "update.json" if variant == "minimal" else "update-bundled.json"
    update_url = f"{REPOSITORY}/releases/latest/download/{manifest_name}"
    prop_lines = files["module.prop"].decode().splitlines()
    prop_lines = [line for line in prop_lines if not line.startswith("updateJson=")]
    files["module.prop"] = ("\n".join(prop_lines) + f"\nupdateJson={update_url}\n").encode()
    for folder in ("META-INF", "webroot"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    if "webroot/index.html" not in files:
        raise ValueError("Missing WebUI entry point")
    if variant == "bundled":
        binaries, provenance = latest_binaries()
        files.update(binaries)
    else:
        provenance = "Minimal package: Tailscale binaries are not included.\nInstall from the module Action or WebUI.\n"
    files["PROVENANCE.txt"] = provenance.encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"tailscale-box-{version}-{variant}-arm64.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            executable = name.endswith(".sh") or name.startswith("bin/") or name.endswith("/update-binary")
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)
    update = {
        "version": version,
        "versionCode": int(metadata(root)["versionCode"]),
        "zipUrl": f"{REPOSITORY}/releases/download/v{version}/{target.name}",
        "changelog": f"{REPOSITORY}/releases/download/v{version}/changelog.md",
    }
    (output / manifest_name).write_text(json.dumps(update, indent=2) + "\n")
    (output / "changelog.md").write_text(
        f"# Tailscale Box {version}\n\n"
        "模块管理器按已安装包类型更新：minimal 下载 minimal，bundled 下载 bundled。\n\n"
        "minimal 升级保留已有 Tailscale 二进制；bundled 使用新包附带的二进制。两者均保留身份数据。\n\n"
        f"[版本说明]({REPOSITORY}/releases/tag/v{version})\n"
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("minimal", "bundled", "both"), default="both")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    variants = ("minimal", "bundled") if args.variant == "both" else (args.variant,)
    for variant in variants:
        print(build(variant, args.output))
