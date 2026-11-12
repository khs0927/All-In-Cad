"""Explicit opt-in external research model adapters; never auto-download weights."""
from __future__ import annotations

import subprocess
from pathlib import Path


def mapanything_command(checkout: Path, images: Path, output: Path, *, apache: bool = True) -> list[str]:
    """Official upstream image-only inference entrypoint, with commercial-friendly opt-in flag."""
    script = checkout / "scripts" / "demo_images_only_inference.py"
    if not script.is_file():
        raise FileNotFoundError(f"MapAnything checkout missing script: {script}")
    if not images.is_dir() or not any(p.suffix.lower() in (".jpg", ".jpeg", ".png") for p in images.iterdir()):
        raise ValueError("An image directory with JPG/PNG files is required")
    cmd = ["python", str(script), "--image_folder", str(images),
           "--output_path", str(output), "--save_glb"]
    if apache:
        cmd.append("--apache")
    return cmd


def run_mapanything(checkout: Path, images: Path, output: Path, *,
                    timeout_seconds: int = 3600) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = mapanything_command(checkout, images, output, apache=True)
    subprocess.run(cmd, check=True, timeout=timeout_seconds)
    if not output.is_file():
        raise RuntimeError("MapAnything did not produce the expected GLB output")
    return output


def colmap_commands(images: Path, workdir: Path) -> list[list[str]]:
    """SfM reconstruction; models are only RELATIVE scale until explicitly aligned."""
    return [
        ["colmap", "feature_extractor", "--database_path", str(workdir / "database.db"),
         "--image_path", str(images), "--ImageReader.single_camera", "1"],
        ["colmap", "exhaustive_matcher", "--database_path", str(workdir / "database.db")],
        ["colmap", "mapper", "--database_path", str(workdir / "database.db"),
         "--image_path", str(images), "--output_path", str(workdir / "sparse")],
    ]
