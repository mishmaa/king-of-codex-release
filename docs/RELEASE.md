# Release Notes

## v1.0.0

This is the definitive single-file release of King of Codex.

## Included

- Six playable characters
- 3v3 KOF-style team battle
- Arcade, VS, Training, Online, Tournament, Gallery, Achievements, and Replay modes
- Three procedural stages
- Generated sound effects and stage music
- Character select with ordered team picking
- MAX mode, EX specials, supers, Dream Cancel hooks, rolls, burst, guard crush, just defense, blowback, wall bounce, and juggle rules
- Persistent JSON settings, unlocks, and achievements
- Auto-saved replay files in `.kcr` JSON format
- PyInstaller build script

## Validation

The release was checked with:

```powershell
python -m py_compile main.py build.py
python -c "import main; print('final import ok')"
```

Additional smoke tests instantiated major screens and verified the 3v3 team flow:

```text
P1 point character wins -> recovers 25% max health -> P2 second character enters
```
