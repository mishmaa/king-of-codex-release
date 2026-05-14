"""Release builder for KING OF CODEX.

The game generates its art and sound at runtime, so packaging only needs the
Python sources plus save/replay folders. PyInstaller must be installed first:

    python -m pip install pyinstaller
    python build.py
"""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ICON = ROOT / "king_of_codex.ico"
VERSION = "1.0.0"


def make_icon():
    """Generate a tiny procedural icon with pygame when available."""
    try:
        import pygame

        pygame.init()
        surface = pygame.Surface((64, 64), pygame.SRCALPHA)
        surface.fill((12, 14, 24, 255))
        pygame.draw.rect(surface, (255, 214, 72), (8, 8, 48, 48), 3, border_radius=8)
        pygame.draw.polygon(surface, (70, 145, 255), [(18, 48), (30, 16), (42, 48)])
        pygame.draw.circle(surface, (230, 60, 55), (42, 24), 8)
        pygame.image.save(surface, str(ICON))
        pygame.quit()
    except Exception:
        return False
    return True


def run_pyinstaller():
    """Build a one-folder executable for the current platform."""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        f"KingOfCodex-{VERSION}",
        "--onedir",
        "--noconfirm",
        "--add-data",
        f"{ROOT / 'replays'}{os.pathsep}replays",
    ]
    if ICON.exists():
        cmd += ["--icon", str(ICON)]
    cmd.append(str(ROOT / "main.py"))
    subprocess.check_call(cmd, cwd=ROOT)


def main():
    (ROOT / "replays").mkdir(exist_ok=True)
    make_icon()
    try:
        run_pyinstaller()
    except subprocess.CalledProcessError as exc:
        print("Build failed. Install PyInstaller with: python -m pip install pyinstaller")
        raise SystemExit(exc.returncode)
    print("Build complete. See the dist folder for Windows, macOS, or Linux output for this machine.")


if __name__ == "__main__":
    main()
