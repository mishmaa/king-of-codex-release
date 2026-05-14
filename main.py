"""KING OF CODEX - Step 3 single-file KOF-style fighting game.

Run:
    python main.py

Everything is procedural Pygame: characters, stages, hitboxes, UI, sounds,
music, particles, character select, arcade, VS, and training are all here.
"""

import array
import copy
import json
import math
import os
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygame


# ---------------------------------------------------------------------------
# Core constants
# ---------------------------------------------------------------------------
W, H = 1280, 720
FPS = 60
GROUND_Y = 550
LEFT_WALL, RIGHT_WALL = 50, 1230
GRAVITY = 0.85
ROUND_TIME = 99
DEFAULT_ROUNDS = 3
STOCK = 100
MAX_STOCKS = 5
MAX_POWER = STOCK * MAX_STOCKS
MOTION_LENIENCY = 10
DOUBLE_TAP = 14
FULL_JUMP_HOLD = 7
SUPER_JUMP_BUF = 14
WHITE = (245, 245, 245)
BLACK = (8, 8, 12)
GOLD = (255, 214, 72)
BLUE = (70, 145, 255)
RED = (230, 60, 55)
PURPLE = (170, 75, 230)
SAVE_FILE = Path("king_of_codex_save.json")
SETTINGS_FILE = Path("king_of_codex_settings.json")
ACHIEVEMENTS_FILE = Path("king_of_codex_achievements.json")
REPLAY_DIR = Path("replays")
VERSION = "v1.0.0"


DEFAULT_SETTINGS = {
    "difficulty": "Normal",
    "timer": 99,
    "rounds": 3,
    "volume": 7,
    "input_display": False,
    "low_spec": False,
    "frame_skip": True,
    "fps_counter": False,
}

REPLAY_DIR.mkdir(exist_ok=True)


def load_json(path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            merged = default.copy()
            merged.update(data)
            return merged
    except (OSError, json.JSONDecodeError):
        return default.copy()
    return default.copy()


def save_json(path, data):
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        return


def default_unlocks():
    return {"omega_vs": False, "alt_colors": False, "gallery": False, "boss_rush": False, "clears": []}


ACHIEVEMENT_DEFS = {
    "first_victory": ("First Victory", "Win 1 match", 1),
    "perfect": ("Perfect!", "Win a round without taking damage", 1),
    "combo_novice": ("Combo Novice", "Land a 5-hit combo", 5),
    "combo_master": ("Combo Master", "Land a 15-hit combo", 15),
    "comeback": ("Comeback King", "Win from 10% health or less", 1),
    "team_player": ("Team Player", "Win with all 3 team members alive", 1),
    "max_maniac": ("MAX Mode Maniac", "Land a 10-hit combo during MAX Mode", 10),
    "dream_big": ("Dream Big", "Perform a Dream Cancel", 1),
    "parry_god": ("Parry God", "Land 10 Just Defenses in one match", 10),
    "boss_slayer": ("Boss Slayer", "Defeat Omega without continuing", 1),
    "completionist": ("Completionist", "Unlock all content", 1),
}


def local_ip():
    """Best-effort LAN IP discovery for the online host screen."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class AchievementManager:
    """Persistent achievements with pop-up notifications."""

    def __init__(self):
        data = load_json(ACHIEVEMENTS_FILE, {"unlocked": {}, "progress": {}})
        self.unlocked = data.get("unlocked", {})
        self.progress = data.get("progress", {})
        self.popups = []

    def save(self):
        save_json(ACHIEVEMENTS_FILE, {"unlocked": self.unlocked, "progress": self.progress})

    def award(self, key, amount=1):
        if key not in ACHIEVEMENT_DEFS or self.unlocked.get(key):
            return
        name, _desc, target = ACHIEVEMENT_DEFS[key]
        self.progress[key] = max(self.progress.get(key, 0), amount)
        if self.progress[key] >= target:
            self.unlocked[key] = True
            self.popups.append([name, 180])
            self.save()

    def draw(self, surf, font):
        y = H - 112
        for popup in self.popups[:]:
            name, frames = popup
            alpha = min(255, frames * 3)
            img = font.render(f"ACHIEVEMENT: {name}", True, GOLD)
            img.set_alpha(alpha)
            rect = img.get_rect(center=(W // 2, y))
            pygame.draw.rect(surf, (20, 18, 8), rect.inflate(34, 18), border_radius=6)
            surf.blit(img, rect)
            popup[1] -= 1
            y -= 42
            if popup[1] <= 0:
                self.popups.remove(popup)


class ReplayRecorder:
    """Stores match metadata, inputs, and lightweight snapshots for playback."""

    def __init__(self, selection, seed):
        self.data = {
            "version": VERSION,
            "date": datetime.now().isoformat(timespec="seconds"),
            "selection": selection,
            "seed": seed,
            "frames": [],
            "winner": None,
        }

    def capture(self, fight):
        if len(self.data["frames"]) > 60 * 60 * 10:
            return
        self.data["frames"].append({
            "p1": dict(fight.p1.controls.held),
            "p2": dict(fight.p2.controls.held),
            "state": [round(fight.p1.x, 1), round(fight.p1.y, 1), fight.p1.hp, round(fight.p2.x, 1), round(fight.p2.y, 1), fight.p2.hp],
        })

    def save(self, winner):
        self.data["winner"] = winner
        path = REPLAY_DIR / f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.kcr"
        save_json(path, self.data)
        files = sorted(REPLAY_DIR.glob("*.kcr"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[10:]:
            try:
                old.unlink()
            except OSError:
                pass
        return path


class NetworkSession:
    """Small UDP gameplay plus TCP lobby/chat wrapper with prediction buffers.

    The online mode is intentionally simple: it establishes a peer, sends local
    input frames over UDP, echoes lobby/chat on TCP, predicts missing remote
    input, and records rollback mismatches up to seven frames.
    """

    def __init__(self, host=False, address="", name="Player"):
        self.host, self.address, self.name = host, address, name
        self.status = "Idle"
        self.error = ""
        self.peer = None
        self.udp = None
        self.tcp = None
        self.thread = None
        self.running = False
        self.messages = []
        self.players = [{"name": name, "status": "Waiting", "ready": False}]
        self.local_inputs, self.remote_inputs, self.predicted = {}, {}, {}
        self.ping_ms = 0
        self.last_packet = time.time()

    def start_host(self):
        self.running, self.status = True, "Waiting for opponent..."
        self.thread = threading.Thread(target=self._host_thread, daemon=True)
        self.thread.start()

    def start_join(self):
        self.running, self.status = True, "Connecting..."
        self.thread = threading.Thread(target=self._join_thread, daemon=True)
        self.thread.start()

    def _host_thread(self):
        try:
            tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp_server.bind(("", 50007))
            tcp_server.listen(1)
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("", 50008))
            self.tcp, addr = tcp_server.accept()
            self.peer = (addr[0], 50008)
            self.status = "OPPONENT FOUND"
            self.players.append({"name": "Opponent", "status": "Waiting", "ready": False})
            self._listen_udp()
        except OSError as exc:
            self.error, self.status = str(exc), "CONNECTION LOST"

    def _join_thread(self):
        try:
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.settimeout(30)
            self.tcp.connect((self.address, 50007))
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("", 50008))
            self.peer = (self.address, 50008)
            self.status = "OPPONENT FOUND"
            self.players.append({"name": "Host", "status": "Waiting", "ready": False})
            self._listen_udp()
        except OSError as exc:
            self.error, self.status = str(exc), "CONNECTION LOST"

    def _listen_udp(self):
        self.udp.settimeout(0.25)
        while self.running:
            try:
                data, _addr = self.udp.recvfrom(4096)
                pkt = json.loads(data.decode("utf-8"))
                if pkt.get("type") == "input":
                    self.remote_inputs[pkt["frame"]] = pkt["input"]
                    self.last_packet = time.time()
                    self.ping_ms = int((time.time() - pkt.get("time", time.time())) * 1000)
                elif pkt.get("type") == "chat":
                    self.messages.append([pkt.get("msg", ""), 300])
            except (OSError, json.JSONDecodeError):
                if time.time() - self.last_packet > 5 and self.status == "OPPONENT FOUND":
                    self.status = "CONNECTION LOST"

    def send_input(self, frame, input_state):
        self.local_inputs[frame] = dict(input_state.held)
        if not self.udp or not self.peer:
            return
        pkt = {"type": "input", "frame": frame, "input": self.local_inputs[frame], "time": time.time()}
        try:
            self.udp.sendto(json.dumps(pkt).encode("utf-8"), self.peer)
        except OSError:
            self.status = "CONNECTION LOST"

    def remote_for(self, frame):
        if frame in self.remote_inputs:
            actual = self.remote_inputs[frame]
            if frame in self.predicted and self.predicted[frame] != actual and abs(max(self.local_inputs.keys(), default=frame) - frame) <= 7:
                return actual, True
            return actual, False
        last = self.remote_inputs[max(self.remote_inputs.keys())] if self.remote_inputs else {k: False for k in P2_KEYS}
        self.predicted[frame] = last
        return last, False

    def send_chat(self, msg):
        self.messages.append([f"{self.name}: {msg}", 300])
        if self.udp and self.peer:
            try:
                self.udp.sendto(json.dumps({"type": "chat", "msg": f"Opponent: {msg}"}).encode("utf-8"), self.peer)
            except OSError:
                self.status = "CONNECTION LOST"

    def close(self):
        self.running = False
        for sock in (self.udp, self.tcp):
            try:
                if sock:
                    sock.close()
            except OSError:
                pass


P1_KEYS = {
    "left": pygame.K_a, "right": pygame.K_d, "up": pygame.K_w, "down": pygame.K_s,
    "lp": pygame.K_f, "hp": pygame.K_g, "lk": pygame.K_h, "hk": pygame.K_j,
}
P2_KEYS = {
    "left": pygame.K_LEFT, "right": pygame.K_RIGHT, "up": pygame.K_UP, "down": pygame.K_DOWN,
    "lp": pygame.K_KP1, "hp": pygame.K_KP2, "lk": pygame.K_KP3, "hk": pygame.K_KP4,
}
ARROWS = {1: "SW", 2: "D", 3: "SE", 4: "B", 5: ".", 6: "F", 7: "NW", 8: "U", 9: "NE"}
MOTIONS = {
    "qcf": [2, 3, 6], "qcb": [2, 1, 4], "hcf": [4, 1, 2, 3, 6],
    "hcb": [6, 3, 2, 1, 4], "dp": [6, 2, 3], "rdp": [4, 2, 1],
    "qcf_qcf": [2, 3, 6, 2, 3, 6], "qcf_hcb": [2, 3, 6, 3, 2, 1, 4],
    "qcb_hcf": [2, 1, 4, 1, 2, 3, 6], "hcb_hcb": [6, 3, 2, 1, 4, 6, 3, 2, 1, 4],
    "360": [6, 3, 2, 1, 4, 7, 8],
}


# ---------------------------------------------------------------------------
# Utility data structures
# ---------------------------------------------------------------------------
@dataclass
class Hitbox:
    owner: object
    rect: pygame.Rect
    damage: int
    hitstun: int
    blockstun: int
    knockback: float
    level: str = "mid"
    name: str = "Hit"
    launch: bool = False
    unblockable: bool = False
    tag: str = ""
    wall_bounce: bool = False


@dataclass
class Move:
    name: str
    motion: str
    buttons: tuple
    kind: str
    damage: int
    startup: int
    active: int
    recovery: int
    hitstun: int = 28
    blockstun: int = 12
    level: str = "mid"
    cost: int = 0
    speed: int = 0
    range: int = 86
    hits: int = 1
    launch: bool = False
    unblockable: bool = False
    armor: bool = False
    wall_bounce: bool = False


# ---------------------------------------------------------------------------
# Sound and music
# ---------------------------------------------------------------------------
class SoundManager:
    """Generated sine-wave effects and small looping melodies."""

    def __init__(self):
        self.enabled = False
        self.sounds = {}
        self.music_channel = None
        self.volume = 0.7
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            self.enabled = True
            self.music_channel = pygame.mixer.Channel(7)
            self._build()
        except pygame.error:
            self.enabled = False

    def tone(self, start, end=None, ms=80, volume=0.32, distort=False):
        rate = 44100
        end = start if end is None else end
        total = max(1, int(rate * ms / 1000))
        arr = array.array("h")
        phase = 0.0
        for i in range(total):
            t = i / max(1, total - 1)
            freq = start + (end - start) * t
            phase += math.tau * freq / rate
            env = (1 - t) ** 1.3
            value = math.sin(phase)
            if distort:
                value = max(-0.55, min(0.55, value * 2.8))
            arr.append(int(value * env * volume * 32767))
        return pygame.mixer.Sound(buffer=arr.tobytes())

    def melody(self, notes, beat=120, volume=0.22):
        rate = 44100
        arr = array.array("h")
        for freq, beats in notes:
            n = int(rate * beat * beats / 1000)
            phase = 0.0
            for i in range(max(1, n)):
                t = i / max(1, n - 1)
                if freq <= 0:
                    value = 0
                else:
                    phase += math.tau * freq / rate
                    value = math.sin(phase) * (0.55 + 0.45 * math.sin(i * 0.01))
                arr.append(int(value * volume * 32767))
        return pygame.mixer.Sound(buffer=arr.tobytes())

    def _build(self):
        self.sounds = {
            "lp": self.tone(80, ms=50), "hp": self.tone(60, ms=80, volume=0.42, distort=True),
            "lk": self.tone(100, ms=40), "hk": self.tone(50, ms=100, volume=0.44, distort=True),
            "special": self.tone(100, 300, ms=200), "super": self.tone(50, 400, ms=400, volume=0.46, distort=True),
            "hit": self.tone(200, ms=30, volume=0.5, distort=True), "block": self.tone(40, ms=60, distort=True),
            "ko": self.tone(300, 50, ms=500, volume=0.5, distort=True), "round": self.melody([(220, 1), (330, 1), (495, 1)], 120),
            "cursor": self.tone(420, ms=35, volume=0.2), "confirm": self.tone(260, 660, ms=180, volume=0.32),
            "victory": self.melody([(330, 1), (392, 1), (494, 1), (659, 1), (784, 2)], 130, 0.28),
            "continue": self.tone(55, 45, ms=900, volume=0.34, distort=True), "perfect": self.melody([(660, 1), (880, 1), (1320, 2)], 110, 0.3),
            "music_dojo": self.melody([(220, 1), (0, .25), (277, 1), (330, 1), (277, 1)], 180, 0.12),
            "music_roof": self.melody([(165, 1), (247, .5), (330, .5), (247, 1), (196, 1)], 150, 0.12),
            "music_throne": self.melody([(110, .5), (147, .5), (165, .5), (220, .5), (147, 1)], 120, 0.14),
            "music_boss": self.melody([(90, .25), (135, .25), (180, .25), (135, .25), (240, .25), (180, .25)], 80, 0.18),
        }
        self.set_volume(7)

    def set_volume(self, value):
        self.volume = max(0, min(10, value)) / 10
        if self.enabled:
            for snd in self.sounds.values():
                snd.set_volume(self.volume)

    def play(self, name):
        if self.enabled and name in self.sounds:
            try:
                self.sounds[name].play()
            except pygame.error:
                return

    def music(self, name):
        if self.enabled and name in self.sounds and self.music_channel:
            try:
                self.music_channel.play(self.sounds[name], loops=-1)
            except pygame.error:
                return

    def stop_music(self):
        if self.enabled and self.music_channel:
            self.music_channel.stop()


# ---------------------------------------------------------------------------
# Input buffer
# ---------------------------------------------------------------------------
class InputBuffer:
    """60-frame direction/button buffer with KOF motion detection and negative edge."""

    def __init__(self, keys):
        self.keys = keys
        self.held = {k: False for k in keys}
        self.prev = self.held.copy()
        self.pressed = set()
        self.released = set()
        self.directions = []
        self.button_events = []
        self.frame = 0
        self.last_left = self.last_right = -999
        self.double_left = self.double_right = False
        self.down_buffer = -999
        self.up_press = -999
        self.charge_back = 0
        self.charge_down = 0
        self.last_motion = None
        self.recording = []
        self.playback = []
        self.playback_i = 0

    def update(self, facing, playback_controls=None):
        self.frame += 1
        self.prev = self.held.copy()
        if playback_controls is None:
            keys = pygame.key.get_pressed()
            for action, key in self.keys.items():
                self.held[action] = bool(keys[key])
        else:
            for action in self.held:
                self.held[action] = action in playback_controls
        self.pressed = {k for k, v in self.held.items() if v and not self.prev[k]}
        self.released = {k for k, v in self.held.items() if not v and self.prev[k]}
        self.double_left = self.double_right = False
        if "left" in self.pressed:
            self.double_left = self.frame - self.last_left <= DOUBLE_TAP
            self.last_left = self.frame
        if "right" in self.pressed:
            self.double_right = self.frame - self.last_right <= DOUBLE_TAP
            self.last_right = self.frame
        if self.held["down"] or "down" in self.pressed:
            self.down_buffer = self.frame
        if "up" in self.pressed:
            self.up_press = self.frame
        direction = self.direction(facing)
        self.directions.append((self.frame, direction))
        self.directions = self.directions[-60:]
        self.charge_back = self.charge_back + 1 if direction in (1, 4, 7) else max(0, self.charge_back - 2)
        self.charge_down = self.charge_down + 1 if direction in (1, 2, 3) else max(0, self.charge_down - 2)
        for b in ("lp", "hp", "lk", "hk"):
            if b in self.pressed:
                self.button_events.append((self.frame, b, "press"))
            if b in self.released:
                self.button_events.append((self.frame, b, "release"))
        self.button_events = self.button_events[-40:]

    def direction(self, facing):
        left, right, up, down = self.held["left"], self.held["right"], self.held["up"], self.held["down"]
        hor = 0
        if right and not left:
            hor = 1 if facing == 1 else -1
        elif left and not right:
            hor = -1 if facing == 1 else 1
        ver = 1 if up and not down else -1 if down and not up else 0
        return {(-1, -1): 1, (0, -1): 2, (1, -1): 3, (-1, 0): 4, (0, 0): 5, (1, 0): 6, (-1, 1): 7, (0, 1): 8, (1, 1): 9}[(hor, ver)]

    def attack_pressed(self):
        for b in ("hp", "hk", "lp", "lk"):
            if b in self.pressed:
                return b
        return None

    def max_pressed(self):
        return ("lp" in self.pressed and self.held["lk"]) or ("lk" in self.pressed and self.held["lp"])

    def roll_pressed(self):
        return self.max_pressed()

    def blowback_pressed(self):
        return ("hp" in self.pressed and self.held["hk"]) or ("hk" in self.pressed and self.held["hp"])

    def burst_pressed(self):
        return all(self.held[b] for b in ("lp", "lk", "hp", "hk")) and any(b in self.pressed for b in ("lp", "lk", "hp", "hk"))

    def forward_recent(self):
        return any(self.frame - f <= 3 and d in (3, 6, 9) for f, d in self.directions)

    def recent_arrows(self):
        out, last = [], None
        for _, d in self.directions:
            if d != last:
                out.append(d)
                last = d
        return out[-8:]

    def motion_for(self, buttons):
        for frame, button, edge in reversed(self.button_events):
            if button not in buttons or self.frame - frame > MOTION_LENIENCY:
                continue
            for name in ("qcf_qcf", "qcf_hcb", "qcb_hcf", "hcb_hcb", "360", "hcf", "hcb", "dp", "rdp", "qcf", "qcb"):
                if self.sequence(MOTIONS[name], frame):
                    key = (frame, button, edge, name)
                    if key != self.last_motion:
                        self.last_motion = key
                        return button, name
            if self.charge_back >= 45 and self.dir_near(frame, (3, 6, 9)):
                return button, "charge_bf"
            if self.charge_down >= 45 and self.dir_near(frame, (7, 8, 9)):
                return button, "charge_du"
        return None, None

    def dir_near(self, frame, dirs):
        return any(abs(f - frame) <= MOTION_LENIENCY and d in dirs for f, d in self.directions)

    def sequence(self, seq, button_frame):
        recent = [(f, d) for f, d in self.directions if button_frame - 60 <= f <= button_frame]
        comp, last = [], None
        for f, d in recent:
            if d != last and d != 5:
                comp.append((f, d))
                last = d
        idx, end = len(seq) - 1, None
        for f, d in reversed(comp):
            if d == seq[idx]:
                if end is None:
                    end = f
                idx -= 1
                if idx < 0:
                    return button_frame - end <= MOTION_LENIENCY
        return False


# ---------------------------------------------------------------------------
# Character classes
# ---------------------------------------------------------------------------
class Character:
    """Base class for roster members. Subclasses define feel and move data."""

    name = "BASE"
    colors = ((120, 120, 140), (220, 220, 220), (255, 220, 80))
    health = 1000
    walk = 4.0
    dash = 160
    jump = 1.0
    body = (58, 150)
    quote = "The code compiles. The opponent does not."
    stats = (3, 3, 3)
    archetype = "balanced"

    def __init__(self):
        self.stance = 0
        self.normals = self.make_normals()
        self.specials = self.make_specials()
        self.supers = self.make_supers()

    def make_normals(self):
        return {
            "stand": {
                "lp": self.n("s.LP", 3, 3, 7, 30, 52, 34, -95),
                "lk": self.n("s.LK", 4, 4, 8, 38, 62, 32, -52, "low"),
                "hp": self.n("s.HP", 7, 5, 14, 90, 78, 46, -100, "mid", launch=True),
                "hk": self.n("s.HK", 8, 6, 15, 110, 88, 40, -58, "low"),
                "close_hp": self.n("close HP", 5, 5, 12, 100, 64, 64, -88),
            },
            "crouch": {
                "lp": self.n("c.LP", 3, 3, 6, 25, 50, 28, -70),
                "lk": self.n("c.LK", 4, 4, 7, 32, 62, 24, -28, "low"),
                "hp": self.n("c.HP", 6, 5, 13, 82, 74, 58, -82, launch=True),
                "hk": self.n("c.HK", 8, 6, 15, 95, 94, 28, -25, "low"),
            },
            "jump": {
                "lp": self.n("j.LP", 3, 5, 5, 28, 48, 32, -82, "overhead"),
                "lk": self.n("j.LK", 4, 5, 6, 36, 58, 32, -55, "overhead"),
                "hp": self.n("j.HP", 6, 6, 9, 82, 74, 44, -85, "overhead"),
                "hk": self.n("j.HK", 7, 7, 10, 92, 86, 42, -58, "overhead"),
            },
        }

    def n(self, name, startup, active, recovery, damage, range_, height, y, level="mid", launch=False):
        return Move(name, "", (), "normal", damage, startup, active, recovery, 20 + damage // 8, 8 + active, level, 0, 0, range_, 1, launch)

    def make_specials(self):
        return []

    def make_supers(self):
        return []

    def passive(self, fighter):
        return None


class Ryujin(Character):
    name = "RYUJIN"
    colors = ((62, 92, 205), (52, 185, 255), (255, 225, 80))
    health, walk, dash, jump = 1000, 4.0, 160, 1.0
    body = (58, 150)
    quote = "Balance is not mercy. It is precision."
    stats = (3, 3, 3)

    def make_specials(self):
        return [
            Move("Hadoken", "qcf", ("lp", "hp"), "projectile", 65, 9, 6, 16, speed=8),
            Move("Shoryuken", "dp", ("lp", "hp"), "uppercut", 110, 2, 18, 22, hits=2, launch=True),
            Move("Tatsumaki", "qcb", ("lk", "hk"), "spin", 90, 5, 20, 13, hits=2),
        ]

    def make_supers(self):
        return [
            Move("Shinku Hadoken", "qcf_qcf", ("lp", "hp"), "super_projectile", 300, 7, 14, 24, cost=2, launch=True),
            Move("Denjin Hadoken", "qcf_qcf", ("lk", "hk"), "super_projectile", 360, 10, 12, 30, cost=3, unblockable=True),
        ]


class Kage(Character):
    name = "KAGE"
    colors = ((92, 38, 110), (230, 65, 105), (160, 80, 255))
    health, walk, dash, jump = 900, 5.5, 220, 1.08
    body = (54, 144)
    quote = "Pressure reveals every bug."
    stats = (5, 3, 2)
    archetype = "rush"

    def make_normals(self):
        data = super().make_normals()
        for group in data.values():
            for m in group.values():
                m.startup = max(2, m.startup - 1)
                m.damage = int(m.damage * 0.86)
                m.range = int(m.range * 0.88)
        data["crouch"]["lk"].startup = 2
        return data

    def make_specials(self):
        return [
            Move("Rekka Chain", "qcf", ("lp", "hp"), "rekka", 65, 4, 7, 9),
            Move("Shadow Kick", "qcf", ("lk", "hk"), "rush", 75, 5, 12, 12, level="low", range=96),
            Move("Demon Flip", "dp", ("lk", "hk"), "flip", 90, 5, 9, 14, level="overhead", launch=True),
        ]

    def make_supers(self):
        return [
            Move("Raging Storm", "qcf_hcb", ("lp", "hp"), "super_rush", 310, 4, 20, 30, cost=2, hits=3),
            Move("Shadow Dance", "qcb_hcf", ("lk", "hk"), "super_dash", 390, 3, 22, 36, cost=3, range=160, hits=4),
        ]


class Titan(Character):
    name = "TITAN"
    colors = ((132, 80, 44), (232, 126, 46), (255, 210, 90))
    health, walk, dash, jump = 1200, 2.8, 120, 0.88
    body = (72, 168)
    quote = "I only need one opening."
    stats = (1, 5, 4)
    archetype = "grappler"

    def make_normals(self):
        data = super().make_normals()
        for group in data.values():
            for m in group.values():
                m.startup += 3
                m.damage = int(m.damage * 1.35)
                m.range = int(m.range * 1.15)
                m.hitstun += 5
        return data

    def make_specials(self):
        return [
            Move("Power Bomb", "hcb", ("lp", "hp"), "grab", 180, 4, 5, 26, unblockable=True, range=82),
            Move("Bear Hug", "hcb", ("lk", "hk"), "aa_grab", 170, 5, 8, 24, unblockable=True, range=84, launch=True),
            Move("Charging Tackle", "charge_bf", ("lp", "hp"), "armor_rush", 130, 8, 16, 18, armor=True, range=105),
        ]

    def make_supers(self):
        return [
            Move("Ultimate Suplex", "hcb_hcb", ("lp", "hp"), "super_grab", 350, 3, 8, 35, cost=2, unblockable=True, range=100),
            Move("Titan Driver", "360", ("lp", "hp"), "super_grab", 450, 2, 8, 42, cost=3, unblockable=True, range=108),
        ]


class Blitz(Character):
    name = "BLITZ"
    colors = ((238, 202, 45), (255, 244, 120), (80, 220, 255))
    health, walk, dash, jump = 850, 6.0, 250, 1.14
    body = (50, 138)
    quote = "You blinked. I finished."
    stats = (5, 2, 2)
    archetype = "mixup"

    def make_normals(self):
        data = super().make_normals()
        for group in data.values():
            for m in group.values():
                m.startup = max(2, m.startup - 2)
                m.damage = int(m.damage * 0.74)
                if m.name.endswith("HP") or m.name.endswith("HK"):
                    m.hits = 2
        return data

    def make_specials(self):
        return [
            Move("Stance Change", "qcb", ("lp", "hp"), "stance", 0, 2, 1, 12),
            Move("Lightning Strike", "qcf", ("lp", "hp"), "projectile", 55, 5, 4, 12, speed=12),
            Move("Rising Knee", "dp", ("lk", "hk"), "uppercut", 80, 3, 12, 16, launch=True),
            Move("Teleport", "qcb", ("lk", "hk"), "teleport", 0, 2, 1, 14),
            Move("Dive/Sweep/Chop", "qcf", ("lk", "hk"), "stance_attack", 82, 4, 10, 12, level="low"),
        ]

    def make_supers(self):
        return [
            Move("Lightning Barrage", "qcf_qcf", ("lp", "hp"), "super_projectile", 280, 4, 14, 25, cost=2, hits=4),
            Move("Thunder God Dance", "qcb_hcf", ("lk", "hk"), "super_dash", 360, 3, 24, 34, cost=3, hits=5),
        ]


class Frost(Character):
    name = "FROST"
    colors = ((225, 240, 245), (82, 222, 255), (120, 150, 255))
    health, walk, dash, jump = 950, 3.5, 150, 0.96
    body = (56, 148)
    quote = "Distance is a weapon."
    stats = (2, 3, 5)
    archetype = "zoner"

    def make_normals(self):
        data = super().make_normals()
        for group in data.values():
            for m in group.values():
                m.startup += 2
                m.range = int(m.range * 1.35)
                m.damage = int(m.damage * 0.96)
        return data

    def make_specials(self):
        return [
            Move("Ice Shard", "qcf", ("lp", "hp"), "projectile", 70, 6, 5, 16, speed=11),
            Move("Ice Wall", "qcf", ("lk", "hk"), "wall", 55, 9, 45, 18, speed=3),
            Move("Frost Mine", "dp", ("lp", "hp"), "trap", 95, 8, 40, 18, launch=True),
            Move("Ice Slide", "qcb", ("lk", "hk"), "rush", 82, 5, 16, 15, level="low", range=102),
        ]

    def make_supers(self):
        return [
            Move("Blizzard", "qcf_qcf", ("lp", "hp"), "beam", 310, 8, 28, 30, cost=2, hits=4),
            Move("Absolute Zero", "qcb_hcf", ("lk", "hk"), "freeze_super", 380, 4, 20, 36, cost=3, unblockable=True),
        ]


class Omega(Character):
    name = "OMEGA"
    colors = ((185, 25, 32), (16, 16, 20), (255, 198, 54))
    health, walk, dash, jump = 1300, 4.5, 200, 1.05
    body = (66, 164)
    quote = "Your victory condition was removed."
    stats = (4, 5, 5)
    archetype = "boss"

    def make_normals(self):
        data = super().make_normals()
        for group in data.values():
            for m in group.values():
                m.startup = max(3, m.startup - 1)
                m.damage = int(m.damage * 1.25)
                m.range = int(m.range * 1.25)
        return data

    def make_specials(self):
        return [
            Move("Reflector", "qcb", ("lp", "hp"), "reflect", 0, 2, 12, 12),
            Move("Command Grab", "hcf", ("lk", "hk"), "grab", 190, 3, 6, 24, unblockable=True, range=92),
            Move("Teleport", "dp", ("lk", "hk"), "teleport", 0, 1, 1, 12),
            Move("Omega Beam", "qcf", ("lp", "hp"), "beam", 160, 12, 18, 20, hits=2),
        ]

    def make_supers(self):
        return [
            Move("Genocide Cutter", "qcf_hcb", ("lp", "hp"), "super_rush", 340, 3, 24, 34, cost=2, hits=4, launch=True),
            Move("World End", "qcb_hcf", ("lk", "hk"), "world_end", 500, 4, 26, 45, cost=3, unblockable=True),
        ]

    def passive(self, fighter):
        if pygame.time.get_ticks() % 30 == 0 and fighter.hp > 0:
            fighter.hp = min(fighter.max_hp, fighter.hp + 1)


ROSTER_CLASSES = [Ryujin, Kage, Titan, Blitz, Frost, Omega]


# ---------------------------------------------------------------------------
# Effects and stage drawing
# ---------------------------------------------------------------------------
class Particle:
    def __init__(self, x, y, color, vx=None, vy=None, life=24):
        self.x, self.y = float(x), float(y)
        a = random.random() * math.tau
        s = random.uniform(1.5, 5.5)
        self.vx = math.cos(a) * s if vx is None else vx
        self.vy = math.sin(a) * s if vy is None else vy
        self.life = life
        self.color = color

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.10
        self.life -= 1

    def draw(self, surf):
        if self.life > 0:
            pygame.draw.rect(surf, self.color, (int(self.x), int(self.y), 5, 5))


class Effects:
    def __init__(self):
        self.particles, self.blocks, self.arcs, self.blurs, self.punishes = [], [], [], [], []
        self.messages, self.afterimages, self.shards, self.confetti = [], [], [], []
        self.flash_color, self.flash_timer, self.freeze = None, 0, 0
        self.shake_amp, self.shake_timer = 0, 0
        self.ko_timer, self.combo_text, self.combo_timer = 0, "", 0
        self.counter_timer, self.perfect_timer, self.double_ko = 0, 0, False

    def spark(self, x, y):
        for _ in range(8):
            self.particles.append(Particle(x, y, (255, 235, 80)))
        self.particles = self.particles[-200:]

    def block(self, x, y):
        self.blocks.append([x, y, 8])

    def arc(self, x, y, facing):
        self.arcs.append([x, y, facing, 18])

    def blur(self, x, y, color):
        self.blurs.append([x, y, color, 16])

    def punish(self):
        self.punishes.append([W // 2, -30, 55])

    def message(self, text, color=WHITE, frames=90, y=230):
        self.messages.append([text, color, frames, y])

    def afterimage(self, rect, color):
        self.afterimages.append([rect.copy(), color, 22])

    def shatter_bar(self, x, y, color):
        for i in range(24):
            self.shards.append([x + i * 18, y, random.uniform(-2, 2), random.uniform(-5, -1), color, 55])

    def victory_confetti(self):
        for _ in range(80):
            self.confetti.append([random.randrange(W), random.randrange(-120, 0), random.choice((GOLD, BLUE, RED, PURPLE, WHITE)), random.uniform(1.5, 4), 160])
        self.confetti = self.confetti[-200:]

    def super_flash(self, color, freeze=15):
        self.flash_color, self.flash_timer, self.freeze = color, 18, freeze

    def shake(self, amp=8, frames=10):
        self.shake_amp, self.shake_timer = amp, frames

    def combo(self, hits, percent, counter=False):
        if hits >= 2:
            self.combo_text, self.combo_timer = f"{hits} HITS - {percent}% DAMAGE", 120
        if counter:
            self.counter_timer = 60

    def update(self):
        for p in self.particles:
            p.update()
        self.particles = [p for p in self.particles if p.life > 0]
        for group in (self.blocks, self.arcs, self.blurs, self.punishes):
            for item in group:
                item[-1] -= 1
                if group is self.punishes:
                    item[1] += 5
            group[:] = [item for item in group if item[-1] > 0]
        for msg in self.messages:
            msg[2] -= 1
        self.messages = [m for m in self.messages if m[2] > 0]
        for item in self.afterimages:
            item[2] -= 1
        self.afterimages = [a for a in self.afterimages if a[2] > 0]
        for sh in self.shards:
            sh[0] += sh[2]
            sh[1] += sh[3]
            sh[3] += .35
            sh[5] -= 1
        self.shards = [sh for sh in self.shards if sh[5] > 0]
        for c in self.confetti:
            c[1] += c[3]
            c[4] -= 1
        self.confetti = [c for c in self.confetti if c[4] > 0 and c[1] < H + 20]
        self.flash_timer = max(0, self.flash_timer - 1)
        self.freeze = max(0, self.freeze - 1)
        self.shake_timer = max(0, self.shake_timer - 1)
        self.ko_timer = max(0, self.ko_timer - 1)
        self.combo_timer = max(0, self.combo_timer - 1)
        self.counter_timer = max(0, self.counter_timer - 1)
        self.perfect_timer = max(0, self.perfect_timer - 1)

    def draw(self, surf, big, huge):
        for rect, color, life in self.afterimages:
            ghost = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            ghost.fill((*color, min(120, life * 6)))
            surf.blit(ghost, rect)
        for p in self.particles:
            p.draw(surf)
        for x, y, _ in self.blocks:
            pygame.draw.rect(surf, (80, 170, 255), (x - 18, y - 18, 36, 36), 3)
        for x, y, f, _ in self.arcs:
            pygame.draw.arc(surf, (255, 70, 45), (x - 50, y - 64, 100, 128), -1.2 if f > 0 else 1.6, 1.2 if f > 0 else 4.8, 6)
        for x, y, c, _ in self.blurs:
            pygame.draw.ellipse(surf, c, (x - 76, y - 32, 152, 64), 3)
        if self.flash_timer and self.flash_color:
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((*self.flash_color, 95))
            surf.blit(ov, (0, 0))
        if self.ko_timer:
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((180, 0, 0, 70))
            surf.blit(ov, (0, 0))
        if self.combo_timer:
            img = huge.render(self.combo_text, True, GOLD)
            scale = 0.8 + self.combo_timer / 300
            img = pygame.transform.smoothscale(img, (int(img.get_width() * scale), int(img.get_height() * scale)))
            img.set_alpha(min(255, self.combo_timer * 3))
            surf.blit(img, img.get_rect(center=(W // 2, 135)))
        if self.counter_timer:
            img = big.render("COUNTER", True, (255, 238, 50))
            img = pygame.transform.rotate(img, math.sin(self.counter_timer / 7) * 12)
            surf.blit(img, img.get_rect(center=(W // 2, 188)))
        for x, y, _ in self.punishes:
            img = big.render("PUNISH", True, (70, 170, 255))
            surf.blit(img, img.get_rect(center=(x, y)))
        for text, color, life, y in self.messages:
            img = huge.render(text, True, color)
            img.set_alpha(min(255, life * 4))
            surf.blit(img, img.get_rect(center=(W // 2, y)))
        for x, y, vx, vy, color, life in self.shards:
            pygame.draw.rect(surf, color, (int(x), int(y), 14, 7))
        for x, y, color, speed, life in self.confetti:
            pygame.draw.rect(surf, color, (int(x), int(y), 6, 10))
        if self.perfect_timer:
            img = huge.render("PERFECT", True, GOLD)
            surf.blit(img, img.get_rect(center=(W // 2, 250)))


class Stage:
    name = "Stage"
    music = "music_dojo"

    def __init__(self):
        self.t = 0
        self.bits = [(random.randrange(W), random.randrange(H)) for _ in range(55)]

    def draw_floor(self, s, color):
        pygame.draw.rect(s, color, (0, GROUND_Y, W, H - GROUND_Y))
        pygame.draw.line(s, WHITE, (0, GROUND_Y), (W, GROUND_Y), 4)


class Dojo(Stage):
    name, music = "Dojo", "music_dojo"

    def draw(self, s):
        self.t += 1
        s.fill((116, 78, 48))
        for y in range(GROUND_Y):
            c = 80 + y // 10
            pygame.draw.line(s, (c, 92, 55), (0, y), (W, y))
        for x in range(0, W, 70):
            pygame.draw.rect(s, (48, 95, 60), (x, 120, 28, 330))
        pygame.draw.rect(s, (145, 35, 28), (500, 145, 280, 24))
        pygame.draw.rect(s, (145, 35, 28), (530, 145, 28, 210))
        pygame.draw.rect(s, (145, 35, 28), (722, 145, 28, 210))
        self.draw_floor(s, (125, 72, 34))
        for x in range(0, W, 48):
            pygame.draw.line(s, (170, 100, 46), (x, GROUND_Y), (x + 30, H), 2)
        for i, (x, y) in enumerate(self.bits):
            x = (x + self.t // 2 + i * 3) % W
            y = (y + self.t + i) % GROUND_Y
            pygame.draw.rect(s, (220, 112, 42), (x, y, 8, 4))


class Rooftop(Stage):
    name, music = "Rooftop", "music_roof"

    def draw(self, s):
        self.t += 1
        s.fill((8, 12, 34))
        for x, y in self.bits:
            pygame.draw.circle(s, WHITE, (x, y // 2), 1)
        pygame.draw.circle(s, (235, 235, 220), (1030, 105), 54)
        for i in range(16):
            x, h = i * 85, 100 + (i % 4) * 35
            pygame.draw.rect(s, (16, 22, 45), (x, GROUND_Y - h, 70, h))
            for wy in range(GROUND_Y - h + 12, GROUND_Y - 10, 28):
                if (self.t // 20 + i + wy) % 3:
                    pygame.draw.rect(s, (240, 220, 75), (x + 14, wy, 12, 12))
        if (self.t // 25) % 2:
            pygame.draw.rect(s, (255, 50, 180), (190, 240, 120, 35))
            pygame.draw.rect(s, (50, 220, 255), (880, 210, 150, 35))
        self.draw_floor(s, (38, 40, 54))
        for x in range(0, W, 80):
            pygame.draw.line(s, (80, 85, 105), (x, GROUND_Y), (x + 45, H), 2)


class ThroneRoom(Stage):
    name, music = "Throne Room", "music_throne"

    def draw(self, s):
        self.t += 1
        s.fill((58, 54, 66))
        for x in (120, 310, 850, 1040):
            pygame.draw.rect(s, (155, 155, 165), (x, 100, 65, 360))
            pygame.draw.rect(s, (105, 105, 115), (x - 18, 90, 101, 28))
        pygame.draw.rect(s, (98, 42, 58), (555, 230, 170, 170))
        pygame.draw.polygon(s, GOLD, [(530, 240), (640, 130), (750, 240)])
        for x in (450, 790):
            h = 42 + math.sin(self.t / 8) * 12
            pygame.draw.polygon(s, (255, 90, 25), [(x, GROUND_Y - 35), (x + 25, GROUND_Y - 95 - h), (x + 50, GROUND_Y - 35)])
            pygame.draw.polygon(s, (255, 210, 60), [(x + 12, GROUND_Y - 35), (x + 25, GROUND_Y - 70 - h), (x + 38, GROUND_Y - 35)])
        self.draw_floor(s, (185, 185, 190))
        for y in range(GROUND_Y, H, 42):
            for x in range(0, W, 84):
                c = (210, 210, 215) if (x // 84 + y // 42) % 2 else (150, 150, 158)
                pygame.draw.rect(s, c, (x, y, 84, 42))


STAGES = [Dojo, Rooftop, ThroneRoom]


# ---------------------------------------------------------------------------
# Projectile/trap actors
# ---------------------------------------------------------------------------
class Projectile:
    def __init__(self, owner, x, y, speed, damage, color, kind="projectile", linger=80, unblockable=False):
        self.owner, self.x, self.y = owner, float(x), float(y)
        self.speed = speed * owner.facing
        self.damage, self.color, self.kind = damage, color, kind
        self.life, self.dead, self.trail = linger, False, []
        self.unblockable = unblockable
        self.w = W if kind in ("beam", "super_projectile") else 50 if kind != "trap" else 54
        self.h = 58 if kind in ("beam", "super_projectile") else 18 if kind != "trap" else 26

    def update(self):
        self.trail.append((self.x, self.y, 18))
        self.trail = [(x, y, a - 2) for x, y, a in self.trail if a > 2][-10:]
        if self.kind not in ("trap", "wall"):
            self.x += self.speed
        self.life -= 1
        if self.x < -100 or self.x > W + 100 or self.life <= 0:
            self.dead = True

    def hitbox(self):
        rect = pygame.Rect(int(self.x), int(self.y - self.h / 2), self.w, self.h)
        if self.speed < 0 and self.kind not in ("trap", "wall"):
            rect.right = int(self.x)
        return Hitbox(self.owner, rect, self.damage, 30, 14, 8, "mid", self.kind, self.kind in ("beam", "trap", "super_projectile"), self.unblockable, f"proj{id(self)}")

    def draw(self, s):
        for x, y, _ in self.trail:
            pygame.draw.rect(s, self.color, (int(x), int(y - self.h / 2), self.w, self.h), 1)
        pygame.draw.rect(s, self.color, self.hitbox().rect, border_radius=5)


# ---------------------------------------------------------------------------
# Fighter runtime
# ---------------------------------------------------------------------------
class Fighter:
    def __init__(self, char, controls, player, x):
        self.char, self.controls, self.player = char, controls, player
        self.x, self.y = float(x), float(GROUND_Y)
        self.max_hp = char.health
        self.hp = char.health
        self.power = 0
        self.vx = self.vy = 0.0
        self.facing = 1 if player == 1 else -1
        self.grounded, self.crouching, self.blocking = True, False, False
        self.state, self.timer, self.attack_elapsed = "idle", 0, 0
        self.attack, self.special_name = None, ""
        self.special_hits, self.hit_ids = [], set()
        self.hitstop = self.invuln = self.flash = self.max_timer = self.grab_freeze = 0
        self.guard_meter, self.burst_used, self.meter_lock = 100, False, 0
        self.roll_timer, self.blow_charge, self.down_timer = 0, 0, 0
        self.quick_rise, self.recovery_flash = False, 0
        self.full_jump_checked = False
        self.juggle, self.wall_bounce = 0, False
        self.ko = False
        self.took_damage = False
        self.stance = 0
        self.armor = 0

    @property
    def width(self):
        return self.char.body[0]

    @property
    def height(self):
        return int(self.char.body[1] * (0.6 if self.crouching else 1.0))

    def hurtbox(self):
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height), self.width, self.height)

    def pushbox(self):
        return pygame.Rect(int(self.x - self.width * .43), int(self.y - self.height + 10), int(self.width * .86), self.height - 10)

    def can_act(self):
        return self.state in ("idle", "walk", "run", "jump") and self.grab_freeze <= 0 and not self.ko

    def add_power(self, n):
        if self.meter_lock:
            return
        self.power = max(0, min(MAX_POWER, self.power + n))

    def spend(self, stocks):
        cost = max(0, stocks - (1 if self.max_timer else 0)) * STOCK
        if self.power >= cost:
            self.power -= cost
            return True
        return False

    def update(self, opponent, projectiles, effects, sounds, dummy_mode=None, training_meter="normal"):
        self.facing = 1 if opponent.x > self.x else -1
        self.char.passive(self)
        if training_meter == "refill":
            self.add_power(2)
        elif training_meter == "infinite":
            self.power = MAX_POWER
        if self.hitstop:
            self.hitstop -= 1
            return
        if self.grab_freeze:
            self.grab_freeze -= 1
            return
        self.flash, self.invuln, self.max_timer, self.armor = max(0, self.flash - 1), max(0, self.invuln - 1), max(0, self.max_timer - 1), max(0, self.armor - 1)
        self.meter_lock, self.recovery_flash = max(0, self.meter_lock - 1), max(0, self.recovery_flash - 1)
        if not self.blocking:
            self.guard_meter = min(100, self.guard_meter + 2)
        if self.roll_timer:
            self.roll_timer -= 1
            self.invuln = 1 if self.roll_timer >= 8 else 0
            self.x += self.facing * (10 if self.state == "roll_f" else -8)
            self.physics()
            return
        if dummy_mode:
            self.apply_dummy(dummy_mode, opponent)
        if self.state in ("hitstun", "blockstun"):
            self.timer -= 1
            if self.timer <= 0:
                self.state = "idle" if self.grounded else "jump"
        elif self.state in ("burst", "guard_crush", "knockdown"):
            self.timer -= 1
            if self.state == "knockdown":
                if any(b in self.controls.pressed for b in ("lp", "hp", "lk", "hk")):
                    self.timer = 0
                elif self.controls.held["down"]:
                    self.timer = min(80, self.timer + 1)
                if (self.controls.held["left"] or self.controls.held["right"]) and sum(self.controls.held[b] for b in ("lp", "lk", "hp", "hk")) >= 2:
                    self.roll_timer, self.invuln, self.state = 25, 18, "roll_b" if self.controls.held["left"] else "roll_f"
            if self.timer <= 0:
                self.state = "idle" if self.grounded else "jump"
        elif self.state in ("attack", "special"):
            self.attack_elapsed += 1
            if self.state == "attack" and self.controls.max_pressed() and self.power >= 2 * STOCK:
                self.power -= 2 * STOCK
                self.max_timer = 180
                self.attack_elapsed = self.attack.startup + self.attack.active + max(1, self.attack.recovery // 3)
                effects.super_flash(GOLD, 10)
                effects.message("QUICK MAX", GOLD, 70, 245)
                sounds.play("super")
            if self.state == "special":
                self.special_update(projectiles)
                self.try_super_cancel(projectiles, effects, sounds)
            if self.attack_elapsed >= self.attack.startup + self.attack.active + self.attack.recovery:
                self.end_attack()
        elif self.can_act() and not dummy_mode:
            self.read_movement()
            if self.try_burst(effects, sounds):
                self.physics()
                return
            if self.try_roll(effects, sounds):
                self.physics()
                return
            self.try_max(effects, sounds)
            if self.try_blowback(sounds):
                self.physics()
                return
            self.try_special(projectiles, effects, sounds)
            if self.can_act():
                self.read_normal(sounds)
        self.upgrade_jump()
        self.physics()
        if abs(self.vx) > 8 or self.state == "special":
            effects.afterimage(self.hurtbox(), self.char.colors[0])

    def try_burst(self, effects, sounds):
        if self.controls.burst_pressed() and not self.burst_used and self.power >= 3 * STOCK:
            self.power = 0
            self.meter_lock, self.burst_used, self.invuln = 120, True, 10
            self.state, self.timer = "burst", 18
            effects.super_flash((120, 255, 220), 8)
            effects.message("BURST", (120, 255, 220), 70, 240)
            sounds.play("super")
            return True
        return False

    def try_roll(self, effects, sounds):
        if self.blocking and self.controls.roll_pressed() and self.power >= STOCK:
            self.power -= STOCK
            forward = self.controls.held["right" if self.facing == 1 else "left"]
            back = self.controls.held["left" if self.facing == 1 else "right"]
            self.state = "roll_f" if forward and not back else "roll_b"
            self.roll_timer, self.invuln = 25, 18
            effects.blur(self.x, self.y - 70, (160, 220, 255))
            sounds.play("special")
            return True
        return False

    def try_blowback(self, sounds):
        if self.controls.blowback_pressed():
            move = Move("Blowback", "", ("hp", "hk"), "normal", 100, 15, 6, 18, 36, 18, "mid", range=105, launch=True, wall_bounce=True)
            if self.max_timer:
                move.name = "Shatter Strike"
                move.armor = True
                self.armor = 20
            self.start_move(move, sounds)
            sounds.play("hk")
            return True
        return False

    def try_super_cancel(self, projectiles, effects, sounds):
        button, motion = self.controls.motion_for(("lp", "hp", "lk", "hk"))
        if not button:
            return
        for move in self.char.supers:
            if move.motion == motion and button in move.buttons:
                extra = 1 if self.max_timer and self.attack and self.attack.cost == 0 else 0
                dream = self.attack and self.attack.cost == 2 and move.cost == 3
                if dream and self.power < 5 * STOCK:
                    return
                if self.spend(move.cost + extra):
                    if dream:
                        effects.super_flash((255, 80, 220), 18)
                        effects.message("DREAM CANCEL", (255, 120, 255), 90, 220)
                    self.start_move(move, sounds)
                    self.perform_special_start(move, projectiles, effects, sounds, button)
                return

    def apply_dummy(self, mode, opponent):
        self.blocking = mode in ("block", "random") and (mode == "block" or random.random() < 0.5)
        self.crouching = mode == "crouch"
        if mode == "jump" and self.grounded and random.random() < 0.02:
            self.vy = -14
            self.grounded = False
        if mode == "wakeup" and self.state == "idle" and abs(opponent.x - self.x) < 140 and random.random() < 0.03:
            self.start_move(Move("Dummy DP", "dp", ("hp",), "uppercut", 100, 2, 12, 20, launch=True), None)

    def read_movement(self):
        c = self.controls
        fkey = "right" if self.facing == 1 else "left"
        bkey = "left" if self.facing == 1 else "right"
        fdash = c.double_right if self.facing == 1 else c.double_left
        self.crouching = self.grounded and c.held["down"]
        self.blocking = c.held[bkey]
        if self.grounded and "up" in c.pressed:
            base = -math.sqrt(2 * GRAVITY * (280 if c.frame - c.down_buffer <= SUPER_JUMP_BUF else 100)) * self.char.jump
            self.vy, self.grounded, self.state, self.full_jump_checked = base, False, "jump", False
        if self.grounded and not self.crouching:
            if fdash:
                self.vx, self.state = self.facing * self.char.dash / 18, "run"
            elif c.held[fkey]:
                self.vx, self.state = self.facing * self.char.walk, "walk"
            elif c.held[bkey]:
                self.vx, self.state = -self.facing * self.char.walk, "walk"
            else:
                self.vx *= .72
                self.state = "idle"
        elif self.crouching:
            self.vx, self.state = 0, "idle"

    def upgrade_jump(self):
        c = self.controls
        if not self.grounded and not self.full_jump_checked and c.held["up"] and c.frame - c.up_press >= FULL_JUMP_HOLD:
            target = -math.sqrt(2 * GRAVITY * 200) * self.char.jump
            if self.vy < 0 and abs(self.vy) < abs(target):
                self.vy = target
            self.full_jump_checked = True

    def try_max(self, effects, sounds):
        if self.controls.max_pressed() and self.power >= 2 * STOCK:
            self.power -= 2 * STOCK
            self.max_timer = 180
            effects.super_flash(GOLD, 12)
            sounds.play("super")

    def read_normal(self, sounds):
        b = self.controls.attack_pressed()
        if not b:
            return
        group = "jump" if not self.grounded else "crouch" if self.crouching else "stand"
        move = self.char.normals[group][b]
        if group == "stand" and b == "hp":
            move = self.char.normals[group]["close_hp"] if random.random() < 0.25 else move
        self.start_move(move, sounds)
        sounds.play(b)

    def try_special(self, projectiles, effects, sounds):
        button, motion = self.controls.motion_for(("lp", "hp", "lk", "hk"))
        if not button:
            return
        pool = self.char.supers + self.char.specials
        for move in pool:
            if motion == move.motion and button in move.buttons and (move.cost == 0 or self.spend(move.cost)):
                if move.name == "Stance Change":
                    self.stance = 1 - self.stance
                if move.name == "Dive/Sweep/Chop":
                    if not self.grounded:
                        move = Move("Dive Kick", "qcf", ("lk", "hk"), "rush", 90, 3, 12, 10, level="overhead")
                    elif button == "hk":
                        move = Move("Overhead Chop", "qcf", ("hk",), "rush", 88, 4, 10, 12, level="overhead")
                    else:
                        move = Move("Sweep", "qcf", ("lk",), "rush", 78, 4, 12, 12, level="low")
                self.start_move(move, sounds)
                self.perform_special_start(move, projectiles, effects, sounds, button)
                self.add_power(5)
                return

    def start_move(self, move, sounds):
        self.attack, self.attack_elapsed, self.special_hits = move, 0, []
        self.state = "special" if move.kind != "normal" else "attack"
        self.special_name = move.name if self.state == "special" else ""

    def perform_special_start(self, move, projectiles, effects, sounds, button):
        ex = self.max_timer > 0
        dmg = int(move.damage * (1.2 if ex else 1.0))
        if ex and move.cost == 0 and self.power >= STOCK // 2:
            self.power -= STOCK // 2
            dmg = int(dmg * 1.15)
        sounds.play("super" if move.cost else "special")
        if move.cost:
            color = self.char.colors[1] if move.kind != "world_end" else (0, 0, 0)
            effects.super_flash(color, 15)
        if move.kind in ("projectile", "super_projectile"):
            speed = move.speed or (8 if button in ("lp", "lk") else 5)
            if ex:
                speed += 3
            kind = "super_projectile" if move.cost else "projectile"
            projectiles.append(Projectile(self, self.x + self.facing * 35, self.y - 92, speed, dmg, self.char.colors[2], kind, 36 if move.cost else 80, move.unblockable))
        elif move.kind in ("beam", "freeze_super", "world_end"):
            projectiles.append(Projectile(self, self.x + self.facing * 20, self.y - 92, 0, dmg, self.char.colors[2], "beam", 22, move.unblockable))
            if move.kind == "freeze_super":
                effects.freeze = 20
        elif move.kind in ("uppercut", "flip"):
            self.vy, self.vx, self.grounded, self.invuln = -11 * self.char.jump, self.facing * 3, False, 6
            if ex:
                self.invuln, move.hits = 10, max(move.hits, 4)
            effects.arc(self.x, self.y - 90, self.facing)
        elif move.kind in ("rush", "spin", "super_rush", "super_dash", "armor_rush"):
            self.vx = self.facing * (14 if move.kind.startswith("super") else 9)
            if move.kind == "armor_rush":
                self.armor = 18
            effects.blur(self.x, self.y - 80, self.char.colors[1])
        elif move.kind == "teleport":
            self.x = max(LEFT_WALL, min(RIGHT_WALL, self.x + self.facing * 240))
            self.invuln = 20
        elif move.kind in ("trap", "wall"):
            projectiles.append(Projectile(self, self.x + self.facing * 95, GROUND_Y - 20, 0 if move.kind == "trap" else 2, dmg, self.char.colors[1], move.kind, 180))

    def special_update(self, projectiles):
        if self.special_name in ("Shoryuken", "Rising Knee", "Flip Kick", "Dummy DP"):
            self.vy -= .05

    def end_attack(self):
        old = self.special_name
        self.state = "idle" if self.grounded else "jump"
        self.attack, self.attack_elapsed = None, 0
        if not old.startswith("Rekka"):
            self.special_name = ""

    def active_hitboxes(self):
        if self.state == "burst" and self.timer > 8:
            rect = self.hurtbox().inflate(170, 90)
            return [Hitbox(self, rect, 0, 20, 0, 16, "mid", "BURST", True, True, f"burst{id(self)}")]
        if self.state not in ("attack", "special") or not self.attack:
            return []
        m = self.attack
        if m.kind in ("projectile", "super_projectile", "beam", "trap", "wall", "teleport", "stance", "reflect", "freeze_super", "world_end"):
            return []
        if not (m.startup <= self.attack_elapsed < m.startup + m.active):
            return []
        slot = int((self.attack_elapsed - m.startup) / max(1, m.active / max(1, m.hits)))
        tag = f"{self.player}-{m.name}-{slot}-{id(m)}"
        if tag in self.special_hits:
            return []
        w, h = m.range, 56 if m.kind not in ("grab", "aa_grab", "super_grab") else 118
        yoff = -84 if m.level != "low" else -34
        rect = pygame.Rect(int(self.x + self.facing * (self.width / 2 + w / 2) - w / 2), int(self.y + yoff - h / 2), w, h)
        return [Hitbox(self, rect, m.damage // max(1, m.hits), m.hitstun, m.blockstun, 9 if m.damage >= 100 else 6, m.level, m.name, m.launch, m.unblockable or m.kind in ("grab", "aa_grab", "super_grab"), tag, m.wall_bounce)]

    def try_throw(self, opponent, sounds):
        fwd = self.controls.held["right" if self.facing == 1 else "left"]
        back = self.controls.held["left" if self.facing == 1 else "right"]
        close = abs(self.x - opponent.x) < 76 and self.grounded and opponent.grounded
        if close and ((self.player == 1 and "hp" in self.controls.pressed) or (self.player == 2 and "hk" in self.controls.pressed)) and (fwd or back):
            dmg = 150 if self.player == 1 else 140
            self.grab_freeze = opponent.grab_freeze = 20
            opponent.hp = max(0, opponent.hp - dmg)
            opponent.vx, opponent.vy, opponent.grounded = self.facing * (15 if self.player == 2 else 8), -7, False
            opponent.wall_bounce = self.player == 2
            opponent.flash = 3
            self.add_power(8)
            opponent.add_power(12)
            sounds.play("hp")

    def physics(self):
        self.x += self.vx
        self.y += self.vy
        if not self.grounded:
            self.vy += GRAVITY + self.juggle * .08
            self.vx *= .985
        elif self.state not in ("walk", "run", "special"):
            self.vx *= .78
        if self.y >= GROUND_Y:
            self.y, self.vy, self.grounded, self.juggle, self.wall_bounce = GROUND_Y, 0, True, 0, False
            if self.state == "jump":
                self.state = "idle"
        else:
            self.grounded = False
        if self.x <= LEFT_WALL or self.x >= RIGHT_WALL:
            if self.wall_bounce:
                self.vx, self.vy, self.wall_bounce = -self.vx * .65, min(self.vy, -7), False
            self.x = max(LEFT_WALL, min(RIGHT_WALL, self.x))

    def draw(self, s, font):
        bob = math.sin(pygame.time.get_ticks() / 150) * 3 if self.state == "idle" else 0
        body = self.hurtbox().move(0, int(bob))
        primary, secondary, accent = self.char.colors
        air_scale = max(.45, 1 - abs(GROUND_Y - self.y) / 420)
        pygame.draw.ellipse(s, (0, 0, 0), (body.centerx - int(38 * air_scale), GROUND_Y - 8, int(76 * air_scale), 16))
        fill = WHITE if self.flash else GOLD if self.max_timer and pygame.time.get_ticks() // 80 % 2 == 0 else primary
        if self.recovery_flash and self.recovery_flash // 5 % 2 == 0:
            fill = (90, 255, 120)
        pygame.draw.rect(s, WHITE if self.blocking else BLACK, body.inflate(6, 6), border_radius=5)
        pygame.draw.rect(s, fill, body, border_radius=5)
        pygame.draw.rect(s, secondary, (body.x + 6, body.y + 38, body.w - 12, 12), border_radius=3)
        pygame.draw.rect(s, (235, 178, 125), (body.centerx - 18, body.y - 30, 36, 28), border_radius=8)
        stance = 6 if self.char.name == "BLITZ" and self.stance else 0
        pygame.draw.rect(s, accent, (body.x - 5 - stance, GROUND_Y - 10, 30, 10), border_radius=3)
        pygame.draw.rect(s, accent, (body.right - 25 + stance, GROUND_Y - 10, 30, 10), border_radius=3)
        for hb in self.active_hitboxes():
            pygame.draw.rect(s, RED, hb.rect, 3)
        label = font.render(f"{self.char.name} {self.state.upper()}", True, WHITE)
        s.blit(label, label.get_rect(center=(body.centerx, body.y - 52)))


class ComboTracker:
    def __init__(self):
        self.hits = self.raw = self.scaled = self.timer = self.best = 0

    def tick(self):
        self.timer = max(0, self.timer - 1)
        if self.timer == 0:
            self.hits = self.raw = self.scaled = 0

    def register(self, damage):
        scale = [1.0, .8, .7, .6, .5][self.hits] if self.hits < 5 else .4
        dealt = max(1, int(damage * scale))
        self.hits += 1
        self.raw += damage
        self.scaled += dealt
        self.timer = 90
        self.best = max(self.best, self.hits)
        return dealt, int(self.scaled / max(1, self.raw) * 100)


def resolve_push(p1, p2):
    r1, r2 = p1.pushbox(), p2.pushbox()
    if r1.colliderect(r2):
        overlap = min(r1.right - r2.left, r2.right - r1.left) + 4
        if p1.x < p2.x:
            p1.x -= overlap / 2
            p2.x += overlap / 2
        else:
            p1.x += overlap / 2
            p2.x -= overlap / 2
    p1.x, p2.x = max(LEFT_WALL, min(RIGHT_WALL, p1.x)), max(LEFT_WALL, min(RIGHT_WALL, p2.x))


def apply_hit(hb, defender, combos, effects, sounds, training=None):
    a = hb.owner
    if defender.invuln or hb.tag in defender.hit_ids or not hb.rect.colliderect(defender.hurtbox()):
        return None
    if not defender.grounded and defender.juggle >= 3 and not hb.unblockable:
        return None
    if hb.name == "BURST":
        defender.vx, defender.vy, defender.grounded = hb.knockback * hb.owner.facing, -8, False
        defender.state, defender.timer = "hitstun", 20
        effects.message("BURST", (120, 255, 220), 50, 245)
        return "burst"
    defender.hit_ids.add(hb.tag)
    a.special_hits.append(hb.tag)
    if hb.name == "Reflector":
        return None
    low_ok = hb.level != "low" or defender.crouching
    overhead_ok = hb.level != "overhead" or not defender.crouching
    if defender.controls.forward_recent() and not hb.unblockable:
        defender.state, defender.timer = "idle", 0
        a.hitstop = 8
        effects.block(hb.rect.centerx, hb.rect.centery)
        effects.message("JUST DEFENSE", BLUE, 55, 235)
        sounds.play("block")
        return "parry"
    if defender.blocking and not hb.unblockable and low_ok and overhead_ok:
        chip = max(1, int(hb.damage * .15))
        defender.hp = max(0, defender.hp - chip)
        defender.state, defender.timer = "blockstun", hb.blockstun
        defender.vx = hb.knockback * .45 * a.facing
        guard_damage = 40 if hb.name in ("beam", "super_projectile") or (a.attack and a.attack.cost) else 25 if a.attack and a.attack.kind != "normal" else 20 if hb.damage >= 90 else 5 if hb.damage < 45 else 10
        defender.guard_meter = max(0, defender.guard_meter - guard_damage)
        if defender.guard_meter <= 0:
            defender.state, defender.timer, defender.guard_meter = "guard_crush", 60, 60
            effects.message("GUARD CRUSH", RED, 80, 240)
        defender.add_power(12)
        a.add_power(3)
        effects.block(hb.rect.centerx, hb.rect.centery)
        sounds.play("block")
        if training is not None:
            training["frame_adv"] = a.attack.recovery - defender.timer if a.attack else 0
        return "block"
    counter = defender.state in ("attack", "special") and defender.attack and defender.attack_elapsed < defender.attack.startup
    punish = defender.state in ("attack", "special") and defender.attack and defender.attack_elapsed > defender.attack.startup + defender.attack.active
    dmg = int(hb.damage * (1.25 if counter else 1) * (1.2 if a.max_timer else 1))
    dealt, pct = combos[a.player].register(dmg)
    defender.hp = max(0, defender.hp - dealt)
    defender.state, defender.timer = "hitstun", max(5, int((hb.hitstun - (combos[a.player].hits - 1) * 5) * (1.5 if counter else 1)))
    defender.vx, defender.flash, defender.took_damage = hb.knockback * a.facing, 3, True
    if hb.launch or hb.damage >= 90 or not defender.grounded:
        defender.vy, defender.grounded, defender.juggle = -8, False, defender.juggle + 1
        if defender.juggle >= 3:
            defender.state, defender.timer = "knockdown", 40
    defender.wall_bounce = hb.wall_bounce
    a.hitstop = defender.hitstop = 5 if hb.damage < 100 else 8
    a.add_power(8)
    defender.add_power(12)
    effects.spark(hb.rect.centerx, hb.rect.centery)
    effects.combo(combos[a.player].hits, pct, counter)
    if punish:
        effects.punish()
    if hb.damage >= 100:
        effects.shake()
    sounds.play("hit")
    if training is not None:
        training["last_damage"], training["combo_damage"], training["frame_adv"] = dealt, combos[a.player].scaled, defender.timer - (a.attack.recovery if a.attack else 0)
    return "hit"


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------
class TitleScreen:
    def __init__(self, sounds, unlocks=None):
        self.unlocks = unlocks or default_unlocks()
        self.options = ["ARCADE MODE", "VS MODE", "ONLINE MODE", "TRAINING", "TOURNAMENT", "REPLAY GALLERY", "ACHIEVEMENTS", "OPTIONS"]
        if self.unlocks.get("gallery"):
            self.options.append("GALLERY")
        if self.unlocks.get("boss_rush"):
            self.options.append("BOSS RUSH")
        self.options.append("QUIT")
        self.i, self.sounds = 0, sounds
        self.idle = 0
        self.bits = [[random.randrange(W), random.randrange(H), random.uniform(.6, 1.8)] for _ in range(90)]

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_UP, pygame.K_w):
                self.i = (self.i - 1) % len(self.options)
                self.sounds.play("cursor")
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                self.i = (self.i + 1) % len(self.options)
                self.sounds.play("cursor")
            elif e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_f):
                self.sounds.play("confirm")
                return self.options[self.i]
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        self.idle += 1
        s.fill((12, 14, 24))
        for y in range(H):
            pygame.draw.line(s, (12, 14, 24 + y // 18), (0, y), (W, y))
        for n, bit in enumerate(self.bits):
            bit[1] -= bit[2]
            bit[0] += math.sin((bit[1] + n) * .02)
            if bit[1] < -10:
                bit[0], bit[1] = random.randrange(W), H + 10
            pygame.draw.rect(s, (220, 140, 55), (int(bit[0]), int(bit[1]), 3, 7))
        for x, col in ((160, (35, 30, 45)), (1040, (45, 25, 25))):
            pygame.draw.ellipse(s, (0, 0, 0), (x - 70, 500, 140, 22))
            pygame.draw.rect(s, col, (x - 35, 340 + int(math.sin(self.idle / 20) * 4), 70, 160), border_radius=8)
            pygame.draw.circle(s, col, (x, 315), 30)
        for off, col in ((4, (90, 62, 12)), (0, GOLD)):
            title = huge.render("KING OF CODEX", True, col)
            s.blit(title, title.get_rect(center=(W // 2 + off, 105 + off)))
        sub = big.render("A KOF-STYLE FIGHTING GAME", True, WHITE)
        s.blit(sub, sub.get_rect(center=(W // 2, 158)))
        for n, opt in enumerate(self.options):
            rect = pygame.Rect(W // 2 - 190, 210 + n * 42, 380, 34)
            pygame.draw.rect(s, (35, 40, 58), rect, border_radius=6)
            pygame.draw.rect(s, GOLD if n == self.i else WHITE, rect, 3 if n == self.i else 1, border_radius=6)
            img = small.render(opt, True, GOLD if n == self.i else WHITE)
            s.blit(img, img.get_rect(center=rect.center))
        if (self.idle // 30) % 2:
            s.blit(big.render("PRESS START", True, GOLD), (W // 2 - 115, H - 88))
        s.blit(small.render("WASD/F or arrows/Enter to navigate", True, WHITE), (W // 2 - 150, H - 55))
        s.blit(small.render(VERSION, True, WHITE), (W - 82, H - 28))


class CharacterSelect:
    def __init__(self, mode, sounds, p1_prev=0, p2_prev=1):
        self.mode, self.sounds = mode, sounds
        self.p1, self.p2 = p1_prev if isinstance(p1_prev, int) else p1_prev[0], p2_prev if isinstance(p2_prev, int) else p2_prev[0]
        self.p1_team = [] if isinstance(p1_prev, int) else list(p1_prev[:3])
        self.p2_team = [] if isinstance(p2_prev, int) else list(p2_prev[:3])
        self.p1_order, self.p2_order = 0, 0
        self.p1_sel = False
        self.p2_sel = mode in ("ARCADE MODE", "TRAINING")
        if self.p2_sel:
            self.p2_team = [i for i in range(6) if i != self.p1][:3]
        self.timer = 60 * FPS
        self.embers = [[random.randrange(W), random.randrange(H), random.uniform(1, 3)] for _ in range(70)]

    def event(self, e):
        if e.type != pygame.KEYDOWN:
            return None
        if e.key == pygame.K_ESCAPE:
            return "menu"
        if not self.p1_sel:
            if e.key in (pygame.K_a, pygame.K_LEFT):
                self.p1 = (self.p1 - 1) % 6
                self.sounds.play("cursor")
            elif e.key in (pygame.K_d, pygame.K_RIGHT):
                self.p1 = (self.p1 + 1) % 6
                self.sounds.play("cursor")
            elif e.key in (pygame.K_w, pygame.K_s):
                self.p1 = (self.p1 + 3) % 6
                self.sounds.play("cursor")
            elif e.key == pygame.K_f:
                if self.p1 not in self.p1_team:
                    self.p1_team.append(self.p1)
                if len(self.p1_team) >= 3:
                    self.p1_sel = True
                self.sounds.play("confirm")
            elif e.key == pygame.K_q and self.p1_team:
                self.p1_order = (self.p1_order - 1) % len(self.p1_team)
                self.p1_team[self.p1_order - 1], self.p1_team[self.p1_order] = self.p1_team[self.p1_order], self.p1_team[self.p1_order - 1]
            elif e.key == pygame.K_e and len(self.p1_team) > 1:
                j = (self.p1_order + 1) % len(self.p1_team)
                self.p1_team[self.p1_order], self.p1_team[j] = self.p1_team[j], self.p1_team[self.p1_order]
                self.p1_order = j
        elif not self.p2_sel:
            if e.key == pygame.K_LEFT:
                self.p2 = (self.p2 - 1) % 6
                self.sounds.play("cursor")
            elif e.key == pygame.K_RIGHT:
                self.p2 = (self.p2 + 1) % 6
                self.sounds.play("cursor")
            elif e.key in (pygame.K_UP, pygame.K_DOWN):
                self.p2 = (self.p2 + 3) % 6
                self.sounds.play("cursor")
            elif e.key == pygame.K_KP1:
                if self.p2 not in self.p2_team:
                    self.p2_team.append(self.p2)
                if len(self.p2_team) >= 3:
                    self.p2_sel = True
                self.sounds.play("confirm")
            elif e.key == pygame.K_KP7 and self.p2_team:
                self.p2_order = (self.p2_order - 1) % len(self.p2_team)
                self.p2_team[self.p2_order - 1], self.p2_team[self.p2_order] = self.p2_team[self.p2_order], self.p2_team[self.p2_order - 1]
            elif e.key == pygame.K_KP9 and len(self.p2_team) > 1:
                j = (self.p2_order + 1) % len(self.p2_team)
                self.p2_team[self.p2_order], self.p2_team[j] = self.p2_team[j], self.p2_team[self.p2_order]
                self.p2_order = j
        elif e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_KP_ENTER):
            return self.result()
        return None

    def result(self):
        while len(self.p1_team) < 3:
            pick = random.randrange(6)
            if pick not in self.p1_team:
                self.p1_team.append(pick)
        while len(self.p2_team) < 3:
            pick = random.randrange(6)
            if pick not in self.p2_team:
                self.p2_team.append(pick)
        p2 = self.p2_team[0]
        if self.mode in ("ARCADE MODE", "TRAINING"):
            p2 = 1 if self.p1 != 1 else 2
        return {"mode": self.mode, "p1": self.p1_team[0], "p2": p2, "p1_team": self.p1_team[:3], "p2_team": self.p2_team[:3]}

    def update(self):
        self.timer -= 1
        for e in self.embers:
            e[1] += e[2]
            e[0] += math.sin(e[1] * .02)
            if e[1] > H:
                e[0], e[1] = random.randrange(W), -10
        if self.timer <= 0:
            if not self.p1_sel:
                while len(self.p1_team) < 3:
                    pick = random.randrange(6)
                    if pick not in self.p1_team:
                        self.p1_team.append(pick)
                self.p1_sel = True
            if not self.p2_sel:
                while len(self.p2_team) < 3:
                    pick = random.randrange(6)
                    if pick not in self.p2_team:
                        self.p2_team.append(pick)
                self.p2_sel = True
            return self.result()
        return None

    def draw_portrait(self, s, idx, rect, selected, fonts):
        small, big, huge = fonts
        ch = ROSTER_CLASSES[idx]()
        scale = 1.07 if selected else 1.0
        r = pygame.Rect(0, 0, int(rect.w * scale), int(rect.h * scale))
        r.center = rect.center
        pygame.draw.rect(s, (28, 31, 45), r, border_radius=8)
        pygame.draw.rect(s, ch.colors[2] if selected else ch.colors[1], r, 4 if selected else 2, border_radius=8)
        pygame.draw.rect(s, ch.colors[0], (r.centerx - 45, r.y + 42, 90, 88), border_radius=8)
        pygame.draw.rect(s, ch.colors[1], (r.centerx - 35, r.y + 72, 70, 18), border_radius=5)
        pygame.draw.circle(s, (235, 178, 125), (r.centerx, r.y + 34), 24)
        name = big.render(ch.name, True, WHITE)
        s.blit(name, name.get_rect(center=(r.centerx, r.bottom + 25)))
        for n, (label, val) in enumerate(zip(("SPD", "POW", "RNG"), ch.stats)):
            y = r.bottom + 55 + n * 18
            s.blit(small.render(label, True, WHITE), (r.x + 5, y - 4))
            for b in range(5):
                pygame.draw.rect(s, ch.colors[2] if b < val else (50, 52, 65), (r.x + 48 + b * 18, y, 14, 8))

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((10, 10, 18))
        for y in range(H):
            pygame.draw.line(s, (10 + y // 70, 10, 20 + y // 20), (0, y), (W, y))
        for x, y, _ in self.embers:
            pygame.draw.rect(s, (210, 84, 34), (int(x), int(y), 4, 8))
        pulse = 1 + math.sin(pygame.time.get_ticks() / 250) * .04
        title = huge.render("SELECT YOUR FIGHTER", True, GOLD)
        title = pygame.transform.smoothscale(title, (int(title.get_width() * pulse), int(title.get_height() * pulse)))
        s.blit(title, title.get_rect(center=(W // 2, 48)))
        positions = [(85, 140), (295, 140), (505, 140), (695, 140), (905, 140), (1115, 140)]
        for i, pos in enumerate(positions):
            selected = i in (self.p1, self.p2) or i in self.p1_team or i in self.p2_team
            self.draw_portrait(s, i, pygame.Rect(pos[0] - 85, pos[1], 170, 170), selected, fonts)
        p1ch, p2ch = ROSTER_CLASSES[self.p1](), ROSTER_CLASSES[self.p2]()
        s.blit(big.render(f"P1 TEAM {len(self.p1_team)}/3" if not self.p1_sel else "P1 READY", True, p1ch.colors[2]), (70, 86))
        s.blit(big.render(f"P2 TEAM {len(self.p2_team)}/3" if not self.p2_sel else "P2 READY", True, p2ch.colors[2]), (W - 320, 86))
        self.draw_team_slots(s, fonts, self.p1_team, 65, 610, "P1")
        self.draw_team_slots(s, fonts, self.p2_team, W - 395, 610, "P2")
        vs = huge.render("VS", True, GOLD if pygame.time.get_ticks() // 220 % 2 else WHITE)
        s.blit(vs, vs.get_rect(center=(W // 2, 520)))
        if self.p1_sel and self.p2_sel:
            s.blit(big.render("PRESS START", True, WHITE), (W // 2 - 115, 610))
        s.blit(big.render(str(max(0, self.timer // FPS)), True, WHITE), (W // 2 - 20, 90))
        s.blit(small.render("Pick 3 in order. P1 F confirm, Q/E reorder. P2 KP1 confirm, KP7/KP9 reorder.", True, WHITE), (W // 2 - 310, H - 28))

    def draw_team_slots(self, s, fonts, team, x, y, label):
        small, big, _ = fonts
        s.blit(small.render(label, True, WHITE), (x, y - 22))
        for i in range(3):
            rect = pygame.Rect(x + i * 108, y, 96, 54)
            pygame.draw.rect(s, (30, 32, 45), rect, border_radius=5)
            pygame.draw.rect(s, GOLD, rect, 2, border_radius=5)
            txt = "EMPTY"
            if i < len(team):
                txt = f"{i+1}. {ROSTER_CLASSES[team[i]].name}"
            s.blit(small.render(txt, True, WHITE), (rect.x + 6, rect.y + 17))


class AiBrain:
    def __init__(self, fighter, level=2):
        self.f, self.level, self.cool = fighter, level, 0

    def update(self, opponent, projectiles):
        c = self.f.controls
        c.held = {k: False for k in c.held}
        c.pressed, c.released = set(), set()
        self.cool = max(0, self.cool - 1)
        dist = abs(opponent.x - self.f.x)
        toward = "right" if opponent.x > self.f.x else "left"
        away = "left" if toward == "right" else "right"
        if any(p.owner is not self.f and abs(p.x - self.f.x) < 240 for p in projectiles):
            c.held[away] = True
        elif self.cool == 0:
            if dist > 220:
                c.held[toward] = True
                if random.random() < .04 * self.level:
                    self.fake_motion("qcf", "lp")
            elif dist < 80 and random.random() < .35:
                b = random.choice(("lp", "lk", "hp", "hk"))
                c.pressed.add(b)
                c.held[b] = True
                self.cool = 14
            elif random.random() < .08 * self.level:
                self.fake_motion(random.choice(("qcf", "dp", "qcb")), random.choice(("lp", "hp", "lk", "hk")))
                self.cool = 34
            else:
                c.held[toward] = True
        c.frame += 1
        c.directions.append((c.frame, c.direction(self.f.facing)))
        c.directions = c.directions[-60:]
        for b in c.pressed:
            c.button_events.append((c.frame, b, "press"))

    def fake_motion(self, motion, button):
        c = self.f.controls
        seq = MOTIONS.get(motion, [2, 3, 6])
        base = c.frame - len(seq)
        for i, d in enumerate(seq):
            c.directions.append((base + i, d))
        c.pressed.add(button)
        c.held[button] = True
        c.button_events.append((c.frame, button, "press"))


class FightState:
    def __init__(self, selection, sounds, arcade_index=0, score=0, continues=3, prev_wins=None, achievements=None, settings=None, online=None):
        self.mode, self.sounds = selection["mode"], sounds
        self.achievements, self.settings, self.online = achievements, settings or DEFAULT_SETTINGS.copy(), online
        self.p1_team = selection.get("p1_team", [selection["p1"], (selection["p1"] + 1) % 6, (selection["p1"] + 2) % 6])[:3]
        self.p2_team = selection.get("p2_team", [selection["p2"], (selection["p2"] + 1) % 6, (selection["p2"] + 2) % 6])[:3]
        self.p1_index, self.p2_index = self.p1_team[0], self.p2_team[0]
        if self.mode == "ARCADE MODE":
            order = [i for i in range(5) if i != self.p1_index] + [5]
            boss = order[min(arcade_index, len(order) - 1)]
            self.p2_team = [boss, (boss + 1) % 6, 5 if boss != 5 else 4]
            self.p2_index = self.p2_team[0]
        self.arcade_index, self.score, self.continues = arcade_index, score, continues
        self.stage = random.choice(STAGES)()
        self.sounds.music("music_boss" if self.p2_index == 5 and self.mode == "ARCADE MODE" else self.stage.music)
        self.p1_slot = self.p2_slot = 0
        self.team_power = {1: 0, 2: 0}
        self.p1_input, self.p2_input = InputBuffer(P1_KEYS), InputBuffer(P2_KEYS)
        self.p1 = Fighter(ROSTER_CLASSES[self.p1_index](), self.p1_input, 1, 330)
        self.p2 = Fighter(ROSTER_CLASSES[self.p2_index](), self.p2_input, 2, 950)
        self.ai = AiBrain(self.p2, 3 if self.p2_index == 5 else 2) if self.mode == "ARCADE MODE" else None
        self.effects, self.projectiles = Effects(), []
        self.combos = {1: ComboTracker(), 2: ComboTracker()}
        self.timer, self.intro, self.ko_timer = float(self.settings.get("timer", ROUND_TIME) or 9999), 120, 0
        self.round, self.wins = 1, prev_wins or {1: 0, 2: 0}
        self.winner = None
        self.transition, self.transition_timer, self.defeated_side = "", 0, 0
        self.paused, self.pause_i = False, 0
        self.show_inputs = False
        self.training = {"dummy": "stand", "meter": "normal", "last_damage": 0, "combo_damage": 0, "frame_adv": 0, "record": [], "play": False, "rec": False}
        self.training.update({"frame_overlay": False, "hitboxes": False, "trial": 0, "trial_time": 0, "reaction": 0, "slots": [[] for _ in range(5)]})
        self.frame_no = 0
        self.replay = ReplayRecorder(selection, random.randrange(999999))
        sounds.play("round")

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.paused = not getattr(self, "paused", False)
                return None
            if getattr(self, "paused", False):
                return self.pause_event(e)
            if e.key == pygame.K_TAB:
                self.show_inputs = not self.show_inputs
            if self.mode == "TRAINING":
                self.training_keys(e.key)
            if self.winner and self.mode == "VS MODE":
                if e.key == pygame.K_1:
                    return {"rematch": True}
                if e.key == pygame.K_2:
                    return {"select": True}
                if e.key == pygame.K_3:
                    return "menu"
        return None

    def pause_event(self, e):
        opts = ["Resume", "Restart Match", "Character Select", "Main Menu"]
        self.pause_i = getattr(self, "pause_i", 0)
        if e.key in (pygame.K_UP, pygame.K_w):
            self.pause_i = (self.pause_i - 1) % len(opts)
        elif e.key in (pygame.K_DOWN, pygame.K_s):
            self.pause_i = (self.pause_i + 1) % len(opts)
        elif e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_f):
            choice = opts[self.pause_i]
            if choice == "Resume":
                self.paused = False
            elif choice == "Restart Match":
                return {"rematch": True}
            elif choice == "Character Select":
                return {"select": True}
            else:
                return "menu"
        return None

    def training_keys(self, key):
        modes = {pygame.K_1: "stand", pygame.K_2: "crouch", pygame.K_3: "jump", pygame.K_4: "block", pygame.K_5: "random", pygame.K_6: "wakeup"}
        meters = {pygame.K_8: "normal", pygame.K_9: "refill", pygame.K_0: "infinite"}
        if key in modes:
            self.training["dummy"] = modes[key]
        if key in meters:
            self.training["meter"] = meters[key]
        if key == pygame.K_7:
            self.training["rec"] = not self.training["rec"]
            self.training["record"] = []
            self.training["play"] = not self.training["rec"]
        if key == pygame.K_r:
            self.p1.x, self.p2.x, self.p1.y, self.p2.y = 420, 860, GROUND_Y, GROUND_Y
        if key == pygame.K_F1:
            self.training["frame_overlay"] = not self.training["frame_overlay"]
        if key == pygame.K_F2:
            self.training["hitboxes"] = not self.training["hitboxes"]

    def update(self):
        self.frame_no += 1
        if getattr(self, "paused", False):
            return None
        if self.ai:
            self.ai.update(self.p1, self.projectiles)
        else:
            self.p2_input.update(self.p2.facing)
        self.p1_input.update(self.p1.facing)
        if self.online:
            self.online.send_input(self.frame_no, self.p1_input)
            remote, rollback = self.online.remote_for(self.frame_no)
            if rollback:
                self.effects.message("ROLLBACK", BLUE, 35, 300)
        slow_skip = self.effects.ko_timer and pygame.time.get_ticks() % 2 == 0
        if self.effects.freeze or slow_skip:
            self.effects.update()
            return None
        if self.transition_timer:
            self.transition_timer -= 1
            if self.transition_timer == 90:
                self.spawn_next()
            if self.transition_timer <= 0:
                self.transition = ""
                self.intro = 45
            self.effects.update()
            return None
        if self.intro:
            self.intro -= 1
            self.effects.update()
            return None
        if self.winner:
            self.ko_timer -= 1
            self.effects.update()
            if self.mode == "ARCADE MODE" and self.ko_timer <= 0:
                return self.arcade_after()
            return None
        self.replay.capture(self)
        self.timer -= 1 / FPS
        self.p1.try_throw(self.p2, self.sounds)
        self.p2.try_throw(self.p1, self.sounds)
        dummy = self.training["dummy"] if self.mode == "TRAINING" else None
        meter = self.training["meter"] if self.mode == "TRAINING" else "normal"
        self.p1.update(self.p2, self.projectiles, self.effects, self.sounds, training_meter=meter)
        self.p2.update(self.p1, self.projectiles, self.effects, self.sounds, dummy_mode=dummy, training_meter=meter)
        resolve_push(self.p1, self.p2)
        for pr in self.projectiles:
            pr.update()
        for hb in self.p1.active_hitboxes():
            apply_hit(hb, self.p2, self.combos, self.effects, self.sounds, self.training if self.mode == "TRAINING" else None)
        for hb in self.p2.active_hitboxes():
            apply_hit(hb, self.p1, self.combos, self.effects, self.sounds, self.training if self.mode == "TRAINING" else None)
        for pr in self.projectiles:
            target = self.p2 if pr.owner is self.p1 else self.p1
            if apply_hit(pr.hitbox(), target, self.combos, self.effects, self.sounds, self.training if self.mode == "TRAINING" else None):
                pr.dead = True
        self.projectiles = [p for p in self.projectiles if not p.dead]
        self.combos[1].tick()
        self.combos[2].tick()
        if self.achievements:
            if self.combos[1].best >= 5:
                self.achievements.award("combo_novice", self.combos[1].best)
            if self.combos[1].best >= 15:
                self.achievements.award("combo_master", self.combos[1].best)
            if self.p1.max_timer and self.combos[1].best >= 10:
                self.achievements.award("max_maniac", self.combos[1].best)
        if self.mode == "TRAINING":
            self.p1.hp, self.p2.hp = self.p1.max_hp, self.p2.max_hp
            self.training["trial_time"] += 1
        elif self.p1.hp <= 0 or self.p2.hp <= 0 or self.timer <= 0:
            self.end_round()
        self.team_power[1], self.team_power[2] = self.p1.power, self.p2.power
        self.effects.update()
        return None

    def end_round(self):
        double = self.p1.hp <= 0 and self.p2.hp <= 0
        win = 0 if double else 1 if self.p1.hp > self.p2.hp else 2
        if double:
            self.p1_slot += 1
            self.p2_slot += 1
            self.effects.message("DOUBLE K.O.", PURPLE, 100, 230)
        elif win == 1:
            self.p2_slot += 1
            heal = int(self.p1.max_hp * .25)
            old = self.p1.hp
            self.p1.hp = min(self.p1.max_hp, self.p1.hp + heal)
            if self.p1.hp > old:
                self.p1.recovery_flash = 60
                self.effects.message("+25% RECOVERY", (90, 255, 120), 80, 170)
            self.effects.message("CHARACTER DEFEATED", RED, 85, 230)
        elif win == 2:
            self.p1_slot += 1
            heal = int(self.p2.max_hp * .25)
            old = self.p2.hp
            self.p2.hp = min(self.p2.max_hp, self.p2.hp + heal)
            if self.p2.hp > old:
                self.p2.recovery_flash = 60
                self.effects.message("+25% RECOVERY", (90, 255, 120), 80, 170)
            self.effects.message("CHARACTER DEFEATED", RED, 85, 230)
        if self.p1_slot >= 3 or self.p2_slot >= 3 or double and (self.p1_slot >= 3 or self.p2_slot >= 3):
            team_win = 0 if self.p1_slot >= 3 and self.p2_slot >= 3 else 2 if self.p1_slot >= 3 else 1
            self.finish_team_match(team_win, double)
            return
        self.defeated_side = 2 if win == 1 else 1 if win == 2 else 0
        self.transition, self.transition_timer = "next", 120
        self.projectiles.clear()
        self.effects.shatter_bar(40 if self.defeated_side == 1 else W - 500, 34, RED)
        self.sounds.play("ko")
        return
        # Legacy round logic is retained below for historical flow but is not used in team mode.
        if win:
            self.wins[win] += 1
            if not (self.p2.took_damage if win == 1 else self.p1.took_damage):
                self.effects.perfect_timer = 120
                self.sounds.play("perfect")
        if self.wins[1] >= 2 or self.wins[2] >= 2 or double:
            self.winner = "DOUBLE K.O." if double else f"P{win}"
            self.ko_timer, self.effects.ko_timer, self.effects.double_ko = 150, 60, double
            self.effects.shake(10, 30)
            self.sounds.play("ko")
            self.score += int(max(0, self.timer) * 10 + max(self.p1.hp, 0) // 2 + self.combos[1].best * 100)
        else:
            self.new_round()

    def finish_team_match(self, team_win, double=False):
        self.winner = "DOUBLE K.O." if double or team_win == 0 else f"P{team_win}"
        self.ko_timer, self.effects.ko_timer = 150, 60
        self.effects.message("TEAM VICTORY" if team_win else "DOUBLE K.O.", GOLD if team_win else PURPLE, 130, 220)
        self.effects.victory_confetti()
        self.effects.shake(10, 30)
        self.sounds.play("ko")
        self.score += int(max(0, self.timer) * 10 + max(self.p1.hp, 0) // 2 + self.combos[1].best * 100)
        self.replay.save(self.winner)
        if self.achievements and team_win == 1:
            self.achievements.award("first_victory")
            if self.p1.hp <= self.p1.max_hp * .1:
                self.achievements.award("comeback")
            if self.p1_slot == 0:
                self.achievements.award("team_player")
            if self.p2_index == 5 and self.mode == "ARCADE MODE" and self.continues >= 3:
                self.achievements.award("boss_slayer")

    def spawn_next(self):
        self.team_power[1], self.team_power[2] = self.p1.power, self.p2.power
        if self.defeated_side in (1, 0) and self.p1_slot < 3:
            self.p1 = Fighter(ROSTER_CLASSES[self.p1_team[self.p1_slot]](), self.p1_input, 1, -80)
            self.p1.power = self.team_power[1]
            self.p1.vx = 16
        if self.defeated_side in (2, 0) and self.p2_slot < 3:
            self.p2 = Fighter(ROSTER_CLASSES[self.p2_team[self.p2_slot]](), self.p2_input, 2, W + 80)
            self.p2.power = self.team_power[2]
            self.p2.vx = -16
            self.ai = AiBrain(self.p2, 3 if self.p2_team[self.p2_slot] == 5 else 2) if self.mode == "ARCADE MODE" else None
        self.effects.message("NEW CHALLENGER", GOLD, 90, 230)
        self.combos = {1: ComboTracker(), 2: ComboTracker()}

    def new_round(self):
        next_round = self.round + 1
        p1i, p2i = self.p1_index, self.p2_index
        self.__init__({"mode": self.mode, "p1": p1i, "p2": p2i}, self.sounds, self.arcade_index, self.score, self.continues, self.wins)
        self.round = min(3, next_round)

    def arcade_after(self):
        if self.winner == "P1":
            if self.arcade_index >= 5:
                return {"ending": True, "score": self.score, "char": self.p1_index}
            return {"arcade_next": self.arcade_index + 1, "score": self.score, "continues": self.continues}
        self.continues -= 1
        if self.continues < 0:
            return {"gameover": True, "score": self.score}
        return {"continue": True, "continues": self.continues, "score": self.score, "arcade_index": self.arcade_index}

    def draw(self, s, fonts):
        small, big, huge = fonts
        self.stage.draw(s)
        if self.settings.get("low_spec"):
            self.effects.particles = self.effects.particles[-40:]
            self.effects.confetti = self.effects.confetti[-40:]
        for pr in self.projectiles:
            pr.draw(s)
        self.p1.draw(s, small)
        self.p2.draw(s, small)
        self.effects.draw(s, big, huge)
        frame = s.copy()
        shake = random.randint(-self.effects.shake_amp, self.effects.shake_amp) if self.effects.shake_timer else 0
        s.fill(BLACK)
        if self.effects.flash_timer:
            z = 1.08
            zoomed = pygame.transform.smoothscale(frame, (int(W * z), int(H * z)))
            s.blit(zoomed, zoomed.get_rect(center=(W // 2 + shake, H // 2)))
        else:
            s.blit(frame, (shake, 0))
        draw_hud(s, fonts, self)
        if self.mode == "TRAINING":
            draw_training(s, fonts, self)
            if self.training.get("hitboxes"):
                draw_hitbox_viewer(s, self)


def draw_bar(s, x, y, w, h, ratio, rev=False):
    pygame.draw.rect(s, BLACK, (x - 3, y - 3, w + 6, h + 6), border_radius=4)
    fill = int(w * max(0, min(1, ratio)))
    c = (60, 220, 85) if ratio > .55 else (245, 215, 65) if ratio > .25 else RED
    pygame.draw.rect(s, c, (x + (w - fill if rev else 0), y, fill, h), border_radius=3)
    pygame.draw.rect(s, WHITE, (x, y, w, h), 2, border_radius=4)


def draw_power(s, x, y, p, max_t, rev=False):
    for i in range(MAX_STOCKS):
        px = x + ((MAX_STOCKS - 1 - i) if rev else i) * 42
        pygame.draw.rect(s, (20, 26, 42), (px, y, 34, 13), border_radius=2)
        fill = max(0, min(STOCK, p - i * STOCK))
        pygame.draw.rect(s, BLUE, (px, y, int(34 * fill / STOCK), 13), border_radius=2)
        pygame.draw.rect(s, WHITE, (px, y, 34, 13), 1, border_radius=2)
    if max_t:
        w = int(206 * max_t / 180)
        pygame.draw.rect(s, GOLD, (x + (206 - w if rev else 0), y + 18, w, 7), border_radius=3)


def draw_hud(s, fonts, f):
    small, big, huge = fonts
    draw_bar(s, 40, 34, 460, 28, f.p1.hp / f.p1.max_hp)
    draw_bar(s, W - 500, 34, 460, 28, f.p2.hp / f.p2.max_hp, True)
    draw_power(s, 40, 70, f.p1.power, f.p1.max_timer)
    draw_power(s, W - 246, 70, f.p2.power, f.p2.max_timer, True)
    s.blit(small.render(f"{f.p1.char.name} {f.p1.hp:04d}", True, WHITE), (40, 95))
    t = small.render(f"{f.p2.char.name} {f.p2.hp:04d}", True, WHITE)
    s.blit(t, t.get_rect(topright=(W - 40, 95)))
    s.blit(big.render(str(max(0, int(f.timer))), True, GOLD), (W // 2 - 30, 28))
    s.blit(small.render(f"{f.wins[1]} - {f.wins[2]}", True, WHITE), (W // 2 - 15, 90))
    draw_team_hud(s, small, f.p1_team, f.p1_slot, 40, 112)
    draw_team_hud(s, small, f.p2_team, f.p2_slot, W - 382, 112)
    pygame.draw.rect(s, RED, (40, 130, int(160 * f.p1.guard_meter / 100), 6))
    pygame.draw.rect(s, RED, (W - 200, 130, int(160 * f.p2.guard_meter / 100), 6))
    if f.intro:
        label = ("FINAL ROUND" if f.round >= 3 else f"ROUND {f.round}") if f.intro > 60 else ("FIGHT!" if f.intro // 6 % 2 == 0 else "")
        if label:
            for off, col in ((8, (80, 50, 20)), (4, (130, 90, 35)), (0, GOLD)):
                img = huge.render(label, True, col)
                s.blit(img, img.get_rect(center=(W // 2 + off, H // 2 - 80 + off)))
    if f.winner:
        label = "DOUBLE K.O." if f.winner == "DOUBLE K.O." else "K.O."
        col = PURPLE if f.winner == "DOUBLE K.O." else RED
        scale = 1 + (150 - f.ko_timer) / 45
        img = huge.render(label, True, col)
        img = pygame.transform.smoothscale(img, (int(img.get_width() * scale), int(img.get_height() * scale)))
        s.blit(img, img.get_rect(center=(W // 2, H // 2 - 40)))
    if f.show_inputs:
        draw_inputs(s, small, f.p1.controls, 40, H - 42)
        draw_inputs(s, small, f.p2.controls, W - 310, H - 42)
    if f.transition_timer:
        s.blit(huge.render("NEW CHALLENGER", True, GOLD), huge.render("NEW CHALLENGER", True, GOLD).get_rect(center=(W // 2, 235)))
    if getattr(f, "paused", False):
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 165))
        s.blit(ov, (0, 0))
        opts = ["Resume", "Restart Match", "Character Select", "Main Menu"]
        s.blit(huge.render("PAUSED", True, GOLD), (W // 2 - 120, 170))
        for i, opt in enumerate(opts):
            col = GOLD if i == f.pause_i else WHITE
            s.blit(big.render(opt, True, col), (W // 2 - 130, 270 + i * 52))


def draw_team_hud(s, font, team, active, x, y):
    for i in range(3):
        color = (65, 65, 75)
        name = "---"
        if i < len(team):
            ch = ROSTER_CLASSES[team[i]]()
            color, name = ch.colors[0], ch.name[:5]
        rect = pygame.Rect(x + i * 112, y, 102, 16)
        pygame.draw.rect(s, color if i >= active else (35, 35, 42), rect, border_radius=3)
        pygame.draw.rect(s, GOLD if i == active else WHITE, rect, 1, border_radius=3)
        s.blit(font.render(f"{i+1}:{name}", True, WHITE), (rect.x + 4, rect.y - 1))


def draw_inputs(s, font, c, x, y):
    pygame.draw.rect(s, (20, 22, 30), (x - 8, y - 8, 274, 34), border_radius=5)
    for i, d in enumerate(c.recent_arrows()):
        s.blit(font.render(ARROWS[d], True, WHITE), (x + i * 32, y))


def draw_training(s, fonts, f):
    small, big, _ = fonts
    t = f.training
    pygame.draw.rect(s, (12, 14, 22), (35, 135, 360, 210), border_radius=6)
    move = f.p1.attack.name if f.p1.attack else "-"
    adv = t["frame_adv"]
    safe_col = (90, 255, 120) if adv >= -2 else (245, 220, 70) if adv >= -6 else (255, 95, 95)
    lines = [
        f"Dummy: {t['dummy']}  Meter: {t['meter']}",
        f"Last input: {ARROWS[f.p1.controls.direction(f.p1.facing)]} {','.join(sorted(f.p1.controls.pressed)) or '-'}",
        f"Last dmg: {t['last_damage']}  Combo dmg: {t['combo_damage']}",
        f"Frame advantage: {t['frame_adv']:+d}",
        f"Last move: {move}",
        f"TRIAL {t['trial']+1}/10: LP > LP > Special",
        "F1 data  F2 boxes  1-7 dummy  8-0 meter  R reset",
    ]
    for i, line in enumerate(lines):
        col = (90, 255, 120) if "Frame" in line and t["frame_adv"] >= 0 else (255, 95, 95) if "Frame" in line else WHITE
        if "Frame" in line:
            col = safe_col
        s.blit(small.render(line, True, col), (48, 150 + i * 26))
    if t.get("frame_overlay") and f.p1.attack:
        m = f.p1.attack
        box = pygame.Rect(W - 330, 140, 285, 115)
        pygame.draw.rect(s, (10, 14, 24), box, border_radius=6)
        for i, line in enumerate((f"Startup {m.startup}", f"Active {m.active}", f"Recovery {m.recovery}", f"Advantage {adv:+d}")):
            s.blit(small.render(line, True, safe_col if "Advantage" in line else WHITE), (box.x + 18, box.y + 16 + i * 24))


def draw_hitbox_viewer(s, f):
    for fighter in (f.p1, f.p2):
        pygame.draw.rect(s, (70, 150, 255), fighter.hurtbox(), 2)
        pygame.draw.rect(s, (70, 255, 120), fighter.pushbox(), 2)
        throwbox = fighter.hurtbox().inflate(60, 20)
        pygame.draw.rect(s, (255, 235, 70), throwbox, 1)
        for hb in fighter.active_hitboxes():
            pygame.draw.rect(s, RED, hb.rect, 3)


class ResultScreen:
    def __init__(self, kind, data, sounds):
        self.kind, self.data, self.sounds = kind, data, sounds
        sounds.stop_music()
        sounds.play("victory" if kind in ("winner", "ending") else "continue")

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if self.kind == "winner":
                if e.key == pygame.K_1:
                    return "rematch"
                if e.key == pygame.K_2:
                    return "select"
                if e.key == pygame.K_3:
                    return "menu"
            if e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_f):
                return "menu" if self.kind in ("ending", "gameover") else "continue"
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((12, 12, 20))
        if self.kind == "ending":
            ch = ROSTER_CLASSES[self.data["char"]]()
            pygame.draw.rect(s, ch.colors[0], (W // 2 - 100, 130, 200, 260), border_radius=10)
            s.blit(huge.render("ARCADE CLEAR", True, GOLD), (W // 2 - 245, 40))
            text = f"{ch.name} restores order to the code arena. Score: {self.data['score']}  Rank: {rank_for(self.data['score'])}"
            s.blit(big.render(text, True, WHITE), (130, 430))
            s.blit(big.render("OMEGA unlocked for VS. S rank unlocks Gallery.", True, GOLD), (170, 500))
        elif self.kind == "gameover":
            s.blit(huge.render("GAME OVER", True, RED), (W // 2 - 190, 180))
            s.blit(big.render(f"Score: {self.data['score']}", True, WHITE), (W // 2 - 85, 300))
        elif self.kind == "continue":
            s.blit(huge.render("CONTINUE?", True, PURPLE), (W // 2 - 180, 160))
            s.blit(big.render(f"Continues left: {self.data['continues']}", True, WHITE), (W // 2 - 125, 270))
        else:
            ch = ROSTER_CLASSES[self.data["winner"]]()
            s.blit(huge.render(f"{ch.name} WINS", True, ch.colors[2]), (W // 2 - 220, 80))
            s.blit(big.render(ch.quote, True, WHITE), (130, 250))
            s.blit(big.render("1 REMATCH   2 CHARACTER SELECT   3 MAIN MENU", True, GOLD), (170, 590))
        s.blit(small.render("Press confirm", True, WHITE), (W // 2 - 60, H - 45))


def rank_for(score):
    return "S" if score >= 100000 else "A" if score >= 80000 else "B" if score >= 60000 else "C" if score >= 40000 else "D" if score >= 20000 else "E"


class OptionsScreen:
    def __init__(self, settings, sounds):
        self.settings, self.sounds, self.i = settings, sounds, 0
        self.items = ["difficulty", "timer", "rounds", "volume", "input_display", "low_spec", "frame_skip", "fps_counter", "button_config", "back"]

    def event(self, e):
        if e.type != pygame.KEYDOWN:
            return None
        if e.key in (pygame.K_UP, pygame.K_w):
            self.i = (self.i - 1) % len(self.items)
            self.sounds.play("cursor")
        elif e.key in (pygame.K_DOWN, pygame.K_s):
            self.i = (self.i + 1) % len(self.items)
            self.sounds.play("cursor")
        elif e.key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d, pygame.K_RETURN, pygame.K_f):
            item = self.items[self.i]
            if item == "back":
                save_json(SETTINGS_FILE, self.settings)
                return "menu"
            if item == "difficulty":
                vals = ["Easy", "Normal", "Hard"]
                self.settings[item] = vals[(vals.index(self.settings[item]) + 1) % len(vals)]
            elif item == "timer":
                vals = [60, 99, 0]
                self.settings[item] = vals[(vals.index(self.settings[item]) + 1) % len(vals)]
            elif item == "rounds":
                vals = [1, 3, 5]
                self.settings[item] = vals[(vals.index(self.settings[item]) + 1) % len(vals)]
            elif item == "volume":
                self.settings[item] = (self.settings[item] + 1) % 11
                self.sounds.set_volume(self.settings[item])
            elif item in ("input_display", "low_spec", "frame_skip", "fps_counter"):
                self.settings[item] = not self.settings[item]
            elif item == "button_config":
                self.settings[item] = "Default mapping active"
            self.sounds.play("confirm")
        elif e.key == pygame.K_ESCAPE:
            save_json(SETTINGS_FILE, self.settings)
            return "menu"
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((12, 14, 24))
        s.blit(huge.render("OPTIONS", True, GOLD), (W // 2 - 130, 85))
        for n, item in enumerate(self.items):
            val = self.settings.get(item, "")
            if item == "timer" and val == 0:
                val = "Infinite"
            label = item.replace("_", " ").upper() if item != "back" else "BACK"
            text = f"{label}: {val}" if item not in ("back", "button_config") else label
            if item == "button_config":
                text = "BUTTON CONFIG: Default remap stub"
            col = GOLD if n == self.i else WHITE
            s.blit(big.render(text, True, col), (W // 2 - 230, 205 + n * 54))


class GalleryScreen:
    def __init__(self):
        self.i = 0

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_LEFT, pygame.K_a):
                self.i = (self.i - 1) % 6
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                self.i = (self.i + 1) % 6
            elif e.key in (pygame.K_ESCAPE, pygame.K_RETURN):
                return "menu"
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        ch = ROSTER_CLASSES[self.i]()
        s.fill((12, 12, 20))
        s.blit(huge.render("GALLERY", True, GOLD), (W // 2 - 130, 55))
        pygame.draw.rect(s, ch.colors[0], (W // 2 - 130, 155, 260, 330), border_radius=12)
        pygame.draw.rect(s, ch.colors[1], (W // 2 - 90, 265, 180, 35), border_radius=6)
        s.blit(huge.render(ch.name, True, ch.colors[2]), (W // 2 - 130, 510))
        s.blit(big.render(f"SPD {ch.stats[0]}  POW {ch.stats[1]}  RNG {ch.stats[2]}", True, WHITE), (W // 2 - 180, 580))
        s.blit(small.render("Left/Right browse, Enter/Esc back", True, WHITE), (W // 2 - 140, H - 45))


class AchievementScreen:
    def __init__(self, achievements):
        self.achievements = achievements
        self.i = 0

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_ESCAPE, pygame.K_RETURN):
                return "menu"
            if e.key in (pygame.K_DOWN, pygame.K_s):
                self.i = min(len(ACHIEVEMENT_DEFS) - 1, self.i + 1)
            if e.key in (pygame.K_UP, pygame.K_w):
                self.i = max(0, self.i - 1)
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((12, 12, 22))
        s.blit(huge.render("ACHIEVEMENTS", True, GOLD), (W // 2 - 210, 45))
        for n, (key, (name, desc, target)) in enumerate(ACHIEVEMENT_DEFS.items()):
            y = 145 + n * 44 - self.i * 10
            if 120 < y < H - 50:
                unlocked = self.achievements.unlocked.get(key, False)
                col = GOLD if unlocked else (120, 125, 140)
                s.blit(big.render(("✓ " if unlocked else "□ ") + name, True, col), (120, y))
                prog = self.achievements.progress.get(key, 0)
                s.blit(small.render(f"{desc}  {prog}/{target}", True, WHITE), (450, y + 10))


class ReplayGallery:
    def __init__(self):
        self.files = sorted(REPLAY_DIR.glob("*.kcr"), key=lambda p: p.stat().st_mtime, reverse=True)
        self.i = 0

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_ESCAPE,):
                return "menu"
            if e.key in (pygame.K_UP, pygame.K_w):
                self.i = max(0, self.i - 1)
            if e.key in (pygame.K_DOWN, pygame.K_s):
                self.i = min(max(0, len(self.files) - 1), self.i + 1)
            if e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_f) and self.files:
                return {"replay": self.files[self.i]}
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((8, 10, 18))
        s.blit(huge.render("REPLAY GALLERY", True, GOLD), (W // 2 - 245, 50))
        if not self.files:
            s.blit(big.render("No replays saved yet.", True, WHITE), (W // 2 - 160, 260))
        for n, path in enumerate(self.files[:10]):
            try:
                data = load_json(path, {})
                label = f"{data.get('date','?')}  Winner: {data.get('winner','?')}  Frames: {len(data.get('frames',[]))}"
            except OSError:
                label = path.name
            col = GOLD if n == self.i else WHITE
            s.blit(big.render(label, True, col), (120, 150 + n * 48))
        s.blit(small.render("Enter play replay  Esc back", True, WHITE), (W // 2 - 110, H - 45))


class ReplayPlayer:
    def __init__(self, path):
        self.path = path
        self.data = load_json(path, {"frames": [], "selection": {"p1_team": [0, 1, 2], "p2_team": [3, 4, 5]}})
        self.frame = 0
        self.speed_i = 0
        self.speeds = [1, 2, 4, 8]
        self.paused = False
        self.stage = Dojo()

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                return "menu"
            if e.key == pygame.K_SPACE:
                self.paused = not self.paused
            if e.key == pygame.K_RIGHT:
                self.speed_i = min(3, self.speed_i + 1)
            if e.key == pygame.K_LEFT:
                self.speed_i = max(0, self.speed_i - 1)
                self.frame = max(0, self.frame - 60)
            if e.key == pygame.K_r:
                self.frame = 0
            if e.key == pygame.K_f:
                self.frame = min(len(self.data.get("frames", [])) - 1, self.frame + 600)
        return None

    def update(self):
        if not self.paused:
            self.frame = min(max(0, len(self.data.get("frames", [])) - 1), self.frame + self.speeds[self.speed_i])

    def draw(self, s, fonts):
        small, big, huge = fonts
        self.stage.draw(s)
        frames = self.data.get("frames", [])
        if frames:
            st = frames[min(self.frame, len(frames) - 1)].get("state", [330, GROUND_Y, 1000, 950, GROUND_Y, 1000])
            pygame.draw.rect(s, BLUE, (st[0] - 30, st[1] - 145, 60, 145), border_radius=6)
            pygame.draw.rect(s, RED, (st[3] - 30, st[4] - 145, 60, 145), border_radius=6)
            draw_bar(s, 40, 34, 460, 28, st[2] / 1300)
            draw_bar(s, W - 500, 34, 460, 28, st[5] / 1300, True)
        s.blit(big.render(f"REPLAY {self.frame}/{max(0, len(frames)-1)}  {self.speeds[self.speed_i]}x", True, GOLD), (60, 640))
        s.blit(small.render("Space pause  Left/Right speed/rewind  R restart  F skip  Esc back", True, WHITE), (60, 685))


class OnlineScreen:
    def __init__(self, sounds):
        self.sounds = sounds
        self.options = ["HOST MATCH", "JOIN MATCH", "SPECTATE", "BACK"]
        self.i = 0
        self.mode = "menu"
        self.ip_text = ""
        self.session = None
        self.chatting = False
        self.chat = ""

    def event(self, e):
        if e.type != pygame.KEYDOWN:
            return None
        if self.mode == "join_ip":
            if e.key == pygame.K_RETURN:
                self.session = NetworkSession(False, self.ip_text or "127.0.0.1")
                self.session.start_join()
                self.mode = "connected"
            elif e.key == pygame.K_BACKSPACE:
                self.ip_text = self.ip_text[:-1]
            elif e.unicode and (e.unicode.isdigit() or e.unicode == "."):
                self.ip_text += e.unicode
            return None
        if self.mode == "connected":
            if self.chatting:
                if e.key == pygame.K_RETURN:
                    if self.session:
                        self.session.send_chat(self.chat)
                    self.chat, self.chatting = "", False
                elif e.key == pygame.K_BACKSPACE:
                    self.chat = self.chat[:-1]
                elif e.unicode:
                    self.chat += e.unicode
            elif e.key == pygame.K_t:
                self.chatting = True
            elif e.key == pygame.K_g and self.session:
                self.session.send_chat("GG")
            elif e.key == pygame.K_RETURN and self.session and self.session.status == "OPPONENT FOUND":
                return {"online_select": self.session}
            elif e.key == pygame.K_ESCAPE:
                if self.session:
                    self.session.close()
                return "menu"
            return None
        if e.key in (pygame.K_UP, pygame.K_w):
            self.i = (self.i - 1) % len(self.options)
        elif e.key in (pygame.K_DOWN, pygame.K_s):
            self.i = (self.i + 1) % len(self.options)
        elif e.key in (pygame.K_RETURN, pygame.K_f):
            opt = self.options[self.i]
            if opt == "BACK":
                return "menu"
            if opt == "JOIN MATCH":
                self.mode = "join_ip"
            else:
                self.session = NetworkSession(True)
                self.session.start_host()
                self.mode = "connected"
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((10, 12, 22))
        s.blit(huge.render("ONLINE MODE", True, GOLD), (W // 2 - 190, 55))
        if self.mode == "join_ip":
            s.blit(big.render("Enter host IP:", True, WHITE), (W // 2 - 160, 250))
            s.blit(big.render(self.ip_text + "_", True, GOLD), (W // 2 - 160, 315))
            return
        if self.mode == "connected":
            sess = self.session
            status = sess.status if sess else "Offline"
            s.blit(big.render(status + "." * ((pygame.time.get_ticks() // 350) % 4), True, GOLD), (W // 2 - 210, 170))
            s.blit(big.render(f"Local IP: {local_ip()}", True, WHITE), (W // 2 - 170, 235))
            if sess:
                col = (80, 255, 120) if sess.ping_ms < 50 else GOLD if sess.ping_ms < 100 else RED
                s.blit(small.render(f"Ping {sess.ping_ms}ms", True, col), (W - 130, 20))
                for i, pl in enumerate(sess.players):
                    s.blit(big.render(f"{pl['name']} - {pl['status']} {'READY' if pl['ready'] else ''}", True, WHITE), (120, 330 + i * 45))
                for i, msg in enumerate(sess.messages[-5:]):
                    s.blit(small.render(msg[0], True, WHITE), (45, H - 160 + i * 24))
                if self.chatting:
                    s.blit(small.render("CHAT: " + self.chat + "_", True, GOLD), (45, H - 35))
            s.blit(small.render("Enter when opponent found, T chat, G quick GG, Esc back", True, WHITE), (W // 2 - 245, H - 45))
            return
        for n, opt in enumerate(self.options):
            col = GOLD if n == self.i else WHITE
            s.blit(big.render(opt, True, col), (W // 2 - 145, 220 + n * 58))


class TournamentScreen:
    def __init__(self):
        self.players = [f"PLAYER {i+1}" for i in range(8)]
        self.i, self.phase = 0, "register"
        self.bracket = [(0, 1), (2, 3), (4, 5), (6, 7)]

    def event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                return "menu"
            if e.key in (pygame.K_RETURN, pygame.K_f):
                self.phase = "bracket" if self.phase == "register" else "ready"
            if e.key in (pygame.K_UP, pygame.K_w):
                self.i = max(0, self.i - 1)
            if e.key in (pygame.K_DOWN, pygame.K_s):
                self.i = min(7, self.i + 1)
            if self.phase == "ready" and e.key == pygame.K_SPACE:
                return {"tournament_match": True}
        return None

    def draw(self, s, fonts):
        small, big, huge = fonts
        s.fill((12, 14, 22))
        s.blit(huge.render("TOURNAMENT", True, GOLD), (W // 2 - 190, 45))
        if self.phase == "register":
            for i, name in enumerate(self.players):
                col = GOLD if i == self.i else WHITE
                s.blit(big.render(name, True, col), (170, 150 + i * 48))
            s.blit(small.render("Enter to lock registration", True, WHITE), (W // 2 - 120, H - 45))
        else:
            for n, (a, b) in enumerate(self.bracket):
                y = 170 + n * 100
                pygame.draw.rect(s, (30, 34, 48), (130, y, 280, 52), border_radius=6)
                pygame.draw.rect(s, (30, 34, 48), (520, y, 280, 52), border_radius=6)
                s.blit(big.render(self.players[a], True, WHITE), (150, y + 10))
                s.blit(big.render(self.players[b], True, WHITE), (540, y + 10))
                pygame.draw.line(s, GOLD, (410, y + 26), (520, y + 26), 3)
            s.blit(big.render("PLAYER 1 VS PLAYER 2 - GET READY!", True, GOLD), (W // 2 - 320, 590))


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------
def main():
    pygame.mixer.pre_init(44100, -16, 1, 512)
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("KING OF CODEX - STEP 3")
    clock = pygame.time.Clock()
    fonts = (pygame.font.Font(None, 24), pygame.font.Font(None, 54), pygame.font.Font(None, 86))
    sounds = SoundManager()
    settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    unlocks = load_json(SAVE_FILE, default_unlocks())
    achievements = AchievementManager()
    sounds.set_volume(settings["volume"])
    pygame.display.set_caption("KING OF CODEX - STEP 4 TEAM BATTLE")
    state, screen_obj = "title", TitleScreen(sounds, unlocks)
    last_selection = {"mode": "VS MODE", "p1": 0, "p2": 1, "p1_team": [0, 1, 2], "p2_team": [3, 4, 5]}
    arcade_meta = {"arcade_index": 0, "score": 0, "continues": 3}
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
                continue
            if e.type == pygame.KEYDOWN and e.key == pygame.K_F12:
                settings["fps_counter"] = not settings.get("fps_counter", False)
            if state == "title":
                choice = screen_obj.event(e)
                if choice == "QUIT":
                    running = False
                elif choice == "OPTIONS":
                    state, screen_obj = "options", OptionsScreen(settings, sounds)
                elif choice == "GALLERY":
                    state, screen_obj = "gallery", GalleryScreen()
                elif choice == "ACHIEVEMENTS":
                    state, screen_obj = "achievements", AchievementScreen(achievements)
                elif choice == "REPLAY GALLERY":
                    state, screen_obj = "replays", ReplayGallery()
                elif choice == "ONLINE MODE":
                    state, screen_obj = "online", OnlineScreen(sounds)
                elif choice == "TOURNAMENT":
                    state, screen_obj = "tournament", TournamentScreen()
                elif choice == "BOSS RUSH":
                    sel = {"mode": "ARCADE MODE", "p1": 0, "p2": 5, "p1_team": [0, 1, 2], "p2_team": [5, 4, 3]}
                    state, screen_obj = "fight", FightState(sel, sounds, achievements=achievements, settings=settings)
                elif choice:
                    state = "select"
                    screen_obj = CharacterSelect(choice, sounds, last_selection["p1_team"], last_selection["p2_team"])
            elif state == "options":
                res = screen_obj.event(e)
                if res == "menu":
                    save_json(SETTINGS_FILE, settings)
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
            elif state == "gallery":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
            elif state == "achievements":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
            elif state == "replays":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif isinstance(res, dict) and "replay" in res:
                    state, screen_obj = "replay_play", ReplayPlayer(res["replay"])
            elif state == "replay_play":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "replays", ReplayGallery()
            elif state == "online":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif isinstance(res, dict) and "online_select" in res:
                    state, screen_obj = "select", CharacterSelect("ONLINE MODE", sounds, last_selection["p1_team"], last_selection["p2_team"])
                    screen_obj.online_session = res["online_select"]
            elif state == "tournament":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif isinstance(res, dict):
                    sel = {"mode": "VS MODE", "p1": 0, "p2": 1, "p1_team": [0, 1, 2], "p2_team": [3, 4, 5]}
                    state, screen_obj = "fight", FightState(sel, sounds, achievements=achievements, settings=settings)
            elif state == "select":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif isinstance(res, dict):
                    last_selection = res.copy()
                    arcade_meta = {"arcade_index": 0, "score": 0, "continues": 3}
                    fight = FightState(res, sounds, achievements=achievements, settings=settings, online=getattr(screen_obj, "online_session", None))
                    fight.show_inputs = settings["input_display"]
                    if settings["timer"] == 0:
                        fight.timer = 9999
                    else:
                        fight.timer = settings["timer"]
                    state, screen_obj = "fight", fight
            elif state == "fight":
                res = screen_obj.event(e)
                if res == "menu":
                    sounds.stop_music()
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif isinstance(res, dict):
                    if "rematch" in res:
                        state, screen_obj = "fight", FightState(last_selection, sounds, achievements=achievements, settings=settings)
                    elif "select" in res:
                        state, screen_obj = "select", CharacterSelect("VS MODE", sounds, last_selection["p1_team"], last_selection["p2_team"])
            elif state == "result":
                res = screen_obj.event(e)
                if res == "menu":
                    state, screen_obj = "title", TitleScreen(sounds, unlocks)
                elif res == "continue":
                    sel = {"mode": "ARCADE MODE", "p1": last_selection["p1"], "p2": 1, "p1_team": last_selection["p1_team"], "p2_team": [1, 2, 3]}
                    state, screen_obj = "fight", FightState(sel, sounds, achievements=achievements, settings=settings, **arcade_meta)
                elif res == "rematch":
                    state, screen_obj = "fight", FightState(last_selection, sounds, achievements=achievements, settings=settings)
                elif res == "select":
                    state, screen_obj = "select", CharacterSelect("VS MODE", sounds, last_selection["p1_team"], last_selection["p2_team"])
        if not running:
            break
        if state == "select":
            res = screen_obj.update()
            if isinstance(res, dict):
                last_selection = res.copy()
                state, screen_obj = "fight", FightState(res, sounds, achievements=achievements, settings=settings, online=getattr(screen_obj, "online_session", None))
        elif state == "fight":
            res = screen_obj.update()
            if isinstance(res, dict):
                sounds.stop_music()
                if "arcade_next" in res:
                    arcade_meta = {"arcade_index": res["arcade_next"], "score": res["score"], "continues": res["continues"]}
                    screen_obj = ResultScreen("continue", {"continues": res["continues"], "score": res["score"]}, sounds)
                    state = "result"
                elif "ending" in res:
                    unlocks["omega_vs"] = True
                    if arcade_meta.get("continues", 3) >= 3:
                        unlocks["alt_colors"] = True
                    if rank_for(res["score"]) == "S":
                        unlocks["gallery"] = True
                    if res["char"] not in unlocks["clears"]:
                        unlocks["clears"].append(res["char"])
                    if len(set(unlocks["clears"])) >= 6:
                        unlocks["boss_rush"] = True
                    save_json(SAVE_FILE, unlocks)
                    screen_obj, state = ResultScreen("ending", res, sounds), "result"
                elif "gameover" in res:
                    screen_obj, state = ResultScreen("gameover", res, sounds), "result"
                elif "continue" in res:
                    arcade_meta = {"arcade_index": res["arcade_index"], "score": res["score"], "continues": res["continues"]}
                    screen_obj, state = ResultScreen("continue", res, sounds), "result"
            elif screen_obj.winner and screen_obj.mode == "VS MODE" and screen_obj.ko_timer <= 0:
                win_idx = screen_obj.p1_team[0] if screen_obj.winner == "P1" else screen_obj.p2_team[0]
                screen_obj, state = ResultScreen("winner", {"winner": win_idx}, sounds), "result"
        screen_obj.draw(screen, fonts)
        if state == "replay_play":
            screen_obj.update()
        achievements.draw(screen, fonts[0])
        if settings.get("fps_counter"):
            fps = clock.get_fps()
            screen.blit(fonts[0].render(f"{fps:05.1f} FPS  {1000/max(1,fps):04.1f}ms", True, WHITE), (12, 12))
        pygame.display.flip()
        clock.tick(FPS)
    pygame.quit()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except pygame.error as exc:
        print(f"Pygame error: {exc}", file=sys.stderr)
        raise
