# King of Codex

**King of Codex** is a procedural KOF-style 2D fighting game built as a single-file Python/Pygame project. It includes 3v3 team battles, six playable characters, arcade mode, VS mode, training tools, generated sound and music, replays, achievements, unlocks, and experimental online/lobby screens.

Version: `v1.0.0`

## Quick Start

```powershell
python -m pip install -r requirements.txt
python main.py
```

Python 3.14 users install `pygame-ce` automatically through the marker in `requirements.txt`; older Python versions use `pygame`.

## Game Modes

- **Arcade Mode**: Select a 3-character team and fight through CPU opponents, ending with Omega.
- **VS Mode**: Local 3v3 team battle for two players.
- **Online Mode**: Host/join/spectate screens with socket-based connection setup, chat, ping display, and rollback/prediction scaffolding.
- **Training**: Dummy settings, meter settings, frame overlay, hitbox viewer, input display, and reset tools.
- **Tournament**: Local 8-player bracket screen and match launcher.
- **Replay Gallery**: Auto-saved `.kcr` replay files with playback controls.
- **Achievements**: Persistent progress and in-game popups.
- **Gallery**: Character art, stats, and bios.

## Controls

### Player 1

- Move: `WASD`
- Light Punch: `F`
- Heavy Punch: `G`
- Light Kick: `H`
- Heavy Kick: `J`
- Select/Confirm: `F`

### Player 2

- Move: Arrow keys
- Light Punch: `Numpad 1`
- Heavy Punch: `Numpad 2`
- Light Kick: `Numpad 3`
- Heavy Kick: `Numpad 4`
- Select/Confirm: `Numpad 1`

### System

- Pause: `Esc`
- Input Display: `Tab`
- FPS Counter: `F12`
- Training Frame Data: `F1`
- Training Hitboxes: `F2`

## Team Battle Rules

Each player selects three fighters in order: point, middle, anchor. When a fighter is defeated:

- The round ends immediately.
- The winner remains on screen.
- The winner recovers 25% of max health, capped at full health.
- The loser’s next character enters at full health.
- Team meter carries between characters.
- The match ends when all three members of a team are defeated.

## Roster

- **Ryujin**: balanced shotokan
- **Kage**: aggressive rushdown
- **Titan**: grappler
- **Blitz**: speed and stance mixups
- **Frost**: zoner and traps
- **Omega**: final boss

## Build

```powershell
python -m pip install pyinstaller
python build.py
```

The build script generates a simple icon and creates a one-folder executable in `dist/`.

## Project Structure

```text
main.py              Complete game source
build.py             PyInstaller release helper
requirements.txt     Runtime dependency markers
docs/                Extra player/developer documentation
replays/             Auto-created replay folder
```

## Notes

All art and sound are generated at runtime with Pygame shapes and sine waves. No external assets are required.
