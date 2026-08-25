import hashlib
import base64
import asyncio
import json
import os
import random
import queue
import sqlite3
import shutil
import math
import subprocess
import sys
import threading
import tempfile
import time
import webbrowser
import tkinter as tk
import html
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
import re
from tkinter import messagebox, ttk
import xml.etree.ElementTree as ET

import requests
import speech_recognition as sr
import pyttsx3
from PySide6.QtCore import QSettings


_EMBEDDED_SECRET_KEY = b"jarvis-local-build-2026"


def _load_embedded_secrets():
    """Load obfuscated provider settings bundled into the private build."""
    locations = [
        os.path.join(os.path.dirname(sys.executable), "jarvis_secrets.dat"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "jarvis_secrets.dat"),
        os.path.join(os.path.dirname(__file__), "jarvis_secrets.dat"),
    ]
    for path in locations:
        try:
            encoded = Path(path).read_bytes()
            encrypted = base64.b64decode(encoded)
            decoded = bytes(
                value ^ _EMBEDDED_SECRET_KEY[index % len(_EMBEDDED_SECRET_KEY)]
                for index, value in enumerate(encrypted)
            ).decode("utf-8")
            for line in decoded.splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    name, value = line.split("=", 1)
                    os.environ.setdefault(name.strip(), value.strip())
            return True
        except (OSError, ValueError, UnicodeError):
            continue
    return False


_load_embedded_secrets()


APP_TITLE = "Джарвис — голосовой ассистент"
APP_VERSION = "2.6.5"
try:
    JARVIS_VOLUME = float(os.getenv("JARVIS_VOLUME", "1.0"))
except ValueError:
    JARVIS_VOLUME = 1.0
APP_DATA_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(__file__)
)
DB_PATH = os.path.join(APP_DATA_DIR, "jarvis.db")
YANDEX_MUSIC_URL = "https://music.yandex.ru/"
NEWS_FALLBACK_URL = (
    "https://www.zakon.kz/tekhno/6528819-fantasticheskaya-"
    "kosmicheskaya-operatsiya-NASA-zakonchilas-provalom.html"
)
# Direct public RSS feeds are more reliable than Google News in countries
# where Google News is unavailable.  They also provide the real article URL.
NEWS_FEEDS = (
    ("3DNews", "https://3dnews.ru/news/rss/"),
    ("Habr AI", "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru"),
    ("N+1", "https://nplus1.ru/rss"),
    ("АвтоВести", "https://www.autonews.ru/rss/news"),
    ("Bing News", "https://www.bing.com/news/search?q=NASA+AI+cars&format=rss"),
    ("NASA", "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
    ("NASA Space Science", "https://www.nasa.gov/rss/dyn/solar_system.rss"),
    ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_Science"),
    ("AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("AI and cars", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Cars", "https://electrek.co/feed/"),
)
NEWS_BLOCKED_WORDS = (
    "войн", "военн", "арм", "оруж", "конфликт", "обстрел", "бомб",
    "ракетн", "санкци", "фронт", "боев", "террор",
)
NEWS_SPEECH_LIMIT = 280
# A negative Edge-TTS rate is faster than the default voice.  Keep this in
# one place so the speed is predictable even when .env is not present.
NEWS_VOICE_DEFAULT_RATE = "+35%"
NEWS_MALE_VOICE = "ru-RU-DmitryNeural"
FEMALE_VOICE_MARKERS = (
    "aria", "dariya", "svetlana", "irina", "female", "женск", "женщина",
)
DEFAULT_WORK_PROJECT = Path(
    r"C:\Users\Максим\source\repos\ConsoleApplication1\ConsoleApplication1.sln"
)
PROJECTS_ROOT = DEFAULT_WORK_PROJECT.parent.parent
JARVIS_ACKS = (
    "Так точно, сэр — {action}.",
    "Как пожелаете, сэр — {action}.",
    "Выполняю, сэр — {action}.",
)
_LAST_ACK_VARIANT = None


def jarvis_ack(action, variant=None):
    """Short, varied confirmations in a Jarvis-like style."""
    global _LAST_ACK_VARIANT
    choices = [index for index in range(len(JARVIS_ACKS))
               if index != _LAST_ACK_VARIANT]
    selected = random.choice(choices)
    _LAST_ACK_VARIANT = selected
    return JARVIS_ACKS[selected].format(action=action)


def is_gratitude_phrase(text):
    """Return True for a short standalone gratitude phrase."""
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip(" ,.!?-")
    if not normalized or len(normalized) > 80:
        return False
    gratitude_words = (
        "спасибо", "благодарю", "благодарность", "молодец", "умница",
        "ты лучший", "отлично", "классно",
    )
    return any(word in normalized for word in gratitude_words)


def parse_protocol_delay(text):
    """Parse a Russian delay after «через» and return seconds."""
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    number_words = {
        "один": "1", "одна": "1", "одно": "1", "два": "2", "две": "2",
        "три": "3", "четыре": "4", "пять": "5", "шесть": "6",
        "семь": "7", "восемь": "8", "девять": "9", "десять": "10",
        "одиннадцать": "11", "двенадцать": "12", "тринадцать": "13",
        "четырнадцать": "14", "пятнадцать": "15", "двадцать": "20",
        "тридцать": "30", "сорок": "40", "пятьдесят": "50",
        "шестьдесят": "60",
    }
    for word, number in number_words.items():
        normalized = re.sub(rf"\b{word}\b", number, normalized)
    if re.search(r"\bчерез\s+полчаса\b", normalized):
        return 1800
    match = re.search(
        r"\bчерез\s+(?:(\d+(?:[.,]\d+)?)\s*)?"
        r"(секунд\w*|сек\w*|минут\w*|мин\w*|час\w*|ч\w*|полчаса)\b",
        normalized,
    )
    if not match:
        if re.search(r"\bчерез\s+час\b", normalized):
            return 3600
        return None
    amount = float((match.group(1) or "1").replace(",", "."))
    unit = match.group(2)
    if unit.startswith("час") or unit.startswith("ч"):
        multiplier = 3600
    elif unit.startswith("мин") or unit.startswith("минут"):
        multiplier = 60
    else:
        multiplier = 1
    return max(1, min(86400, round(amount * multiplier)))


def protocol_answer(text):
    """Classify the first words spoken during the protocol confirmation."""
    normalized = re.sub(r"[^а-яёa-z0-9\s-]", " ", (text or "").casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    # Speech recognition often adds a short filler before the actual answer.
    normalized = re.sub(r"^(ну|так|хорошо|ладно)\s+", "", normalized)
    if normalized.startswith((
        "нет", "не хочу", "не надо", "не соглас", "отмена", "отменяй",
        "отменить", "отказываюсь", "не буду", "не запускай", "не делай",
        "неа", "ни за что",
    )):
        return "no"
    if normalized.startswith((
        "да", "ага", "угу", "конечно", "согласен", "согласна",
        "хочу", "давай", "делай", "запускай", "запусти", "поехали",
        "так", "разумеется", "хорошо", "ладно",
    )):
        return "yes"
    return None


def is_tik_tok_protocol(text):
    """Recognize common speech-to-text variants of the protocol name."""
    normalized = re.sub(r"[^а-яёa-z0-9]+", " ", (text or "").casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    has_tik_tok = any(
        phrase in normalized
        for phrase in ("тик ток", "тикток", "tik tok", "tiktok")
    )
    has_protocol = any(
        phrase in normalized
        for phrase in ("протокол", "протакол", "протоколл", "protocol")
    )
    return has_tik_tok and has_protocol


def extract_wake_command(text):
    """Return (wake_found, command) with tolerance for speech inflections."""
    configured = os.getenv("JARVIS_WAKE_WORD", "").strip()
    if not configured:
        configured = QSettings("Jarvis", "JarvisAssistant").value(
            "wake_keyword", ""
        )
        configured = str(configured).strip()
    if not configured:
        return False, ""
    match = re.search(
        rf"(?<!\w){re.escape(configured.casefold())}(?!\w)",
        (text or "").casefold(),
    )
    if not match:
        return False, ""
    return True, (text or "")[match.end():].strip(" ,.!?-")


def media_command(command):
    """Whether a command starts a music/video session worth monitoring."""
    text = (command or "").casefold()
    return (
        any(word in text for word in (
            "музык", "песн", "трек", "мелод", "яндекс музыку",
            "youtube", "ютуб", "видео", "ролик", "фильм", "сериал",
        ))
        and any(word in text for word in (
            "включ", "запусти", "открой", "посмотр", "найди", "поищи",
        ))
    )


def voice_category(command):
    """Choose a voice folder from the meaning of a recognized command."""
    text = command.casefold()
    if any(phrase in text for phrase in (
        "спасибо", "благодарю", "благодарность", "молодец", "умница",
        "ты лучший", "отлично", "классно",
    )):
        return "blagodar"
    action_words = {
        "paus": ("поставь на паузу", "пауза", "останови музыку"),
        "play": ("продолжай", "продолжи", "продолжаем", "возобнови музыку"),
        "skip": ("дальше", "следующий трек", "следующая песня"),
        "back": ("назад", "предыдущий трек", "предыдущая песня"),
    }
    for category, phrases in action_words.items():
        if any(phrase in text for phrase in phrases):
            return category
    video_words = (
        "youtube", "ютуб", "видео", "ролик", "фильм", "мультфильм",
        "сериал", "трейлер", "клип", "яндекс видео",
    )
    music_words = (
        "музык", "music", "песн", "песню", "трек", "мелод",
    )
    game_words = (
        "игр", "дот", "dota", "раст", "rust", "апекс", "apex",
        "легенд", "dayz", "стим", "steam",
    )
    work_words = (
        "blender", "блендер", "visual studio", "висуал студио", "vscode",
        "vs code", "powerpoint", "power point", "повер поинт", "презентац",
        "блокнот", "notepad", "редактор", "рабоч", "поработ",
    )
    if any(word in text for word in video_words):
        return "video"
    if any(word in text for word in music_words):
        return "music"
    if any(word in text for word in game_words):
        return "game"
    if any(word in text for word in work_words):
        return "work"
    return "yes"


def is_general_web_search(command):
    """Normal web searches use generated speech, not a prerecorded clip."""
    text = (command or "").casefold()
    search_words = ("найди", "найти", "поищи", "поиск", "узнай", "что такое")
    media_words = (
        "фильм", "мультфильм", "мультик", "видео", "сериал",
        "трейлер", "клип", "youtube", "ютуб", "яндекс видео",
    )
    return any(word in text for word in search_words) and not any(
        word in text for word in media_words
    )


def is_conversational_phrase(text):
    """Allow harmless short conversation without requiring a second wake word."""
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip(" ,.!?-")
    return len(normalized) <= 80 and any(phrase in normalized for phrase in (
        "как дела", "как ты", "который час", "сколько времени",
        "сколько время", "какое время",
        "доброе утро", "добрый день", "добрый вечер", "привет",
        "здравствуй",
    ))


class VoiceLibrary:
    """Randomly plays the user's pre-recorded Jarvis confirmations."""

    EXTENSIONS = {".wav", ".mp3", ".mp4", ".m4a", ".ogg"}

    def __init__(self):
        base = Path(
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        # "voise" is the documented name; "voice" is accepted too because
        # Windows users often rename it to the English spelling.
        self.roots = [
            candidate for candidate in (base / "voise", base / "voice")
            if candidate.is_dir()
        ]
        self.root = self.roots[0] if self.roots else base / "voise"
        self.files = {
            "yes": [], "video": [], "work": [], "game": [], "music": [],
            "blagodar": [], "paus": [], "play": [], "skip": [], "back": [],
            "prot_otd": [], "prot_y": [], "prot_n": [],
        }
        self.history = {category: [] for category in self.files}
        self.mixer = None
        self.mixer_ready = False
        try:
            import pygame
            pygame.mixer.init()
            self.mixer = pygame.mixer
            self.mixer_ready = True
        except Exception:
            # WAV/MP3 playback will fall back to another hidden/local method.
            pass
        self.reload()

    def reload(self):
        for category in self.files:
            self.files[category] = []
        if not self.roots:
            return
        folders = {
            "yes": ("yes",),
            "blagodar": ("blagodar",),
            "video": ("video",),
            # Support both the requested vopros/work layout and a flatter
            # voise/work layout.
            "work": ("vopros/work", "work"),
            "game": ("vopros/game", "game"),
            "music": ("music",),
            "paus": ("deestviya/paus",),
            "play": ("deestviya/play",),
            "skip": ("deestviya/skip",),
            "back": ("deestviya/back",),
            "prot_otd": ("prot tik/otd",),
            "prot_y": ("prot tik/y",),
            "prot_n": ("prot tik/n",),
        }
        for category, relative_folders in folders.items():
            for root in self.roots:
                for relative_folder in relative_folders:
                    folder = root / relative_folder
                    if not folder.is_dir():
                        continue
                    try:
                        self.files[category].extend(
                            path for path in folder.rglob("*")
                            if path.is_file() and path.suffix.casefold() in self.EXTENSIONS
                        )
                    except OSError:
                        continue

    def summary(self):
        return ", ".join(
            f"{category}: {len(files)}"
            for category, files in self.files.items()
        )

    def _random_file(self, category):
        choices = self.files.get(category, [])
        if not choices:
            return None
        if len(choices) == 1:
            selected = choices[0]
        else:
            # The last voice cannot repeat immediately. The voice before it
            # has only a small chance; every other voice has the normal chance.
            previous = self.history[category]
            weights = []
            for path in choices:
                if previous and path == previous[-1]:
                    weights.append(0)
                elif len(previous) > 1 and path == previous[-2]:
                    weights.append(10)
                else:
                    weights.append(100)
            if not any(weights):
                weights = [100] * len(choices)
            selected = random.choices(choices, weights=weights, k=1)[0]
        history = self.history[category]
        history.append(selected)
        del history[:-2]
        return selected

    def play(self, category):
        path = self._random_file(category)
        if path is None:
            return False
        try:
            suffix = path.suffix.casefold()
            if self.mixer_ready and suffix in {".wav", ".mp3", ".ogg"}:
                self.mixer.music.stop()
                self.mixer.music.load(str(path))
                self.mixer.music.play()
            elif suffix in {".wav", ".mp3", ".m4a"}:
                ffplay = shutil.which("ffplay")
                if not ffplay:
                    return False
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            elif suffix == ".mp4":
                ffplay = shutil.which("ffplay")
                if not ffplay:
                    return False
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            elif suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".ogg"}:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            else:
                return False
            return True
        except (OSError, FileNotFoundError):
            return False

    def wait_until_finished(self, timeout=15):
        """Wait for an internally mixed recorded phrase to finish.

        pygame.mixer.music.play() is asynchronous.  The old Tik Tok protocol
        started its confirmation countdown immediately after calling it, so
        the five seconds could expire while Jarvis was still speaking.
        """
        if not self.mixer_ready:
            return
        started = time.monotonic()
        try:
            while self.mixer.music.get_busy() and time.monotonic() - started < timeout:
                time.sleep(0.05)
        except Exception:
            return


def project_language(command):
    text = command.casefold()
    if any(word in text for word in ("c#", "си шарп", "си-шарп", "сишарп", "c sharp")):
        return "csharp"
    if any(word in text for word in ("python", "пайтон", "питон")):
        return "python"
    return "python"


def project_name_from_command(command):
    text = command.casefold()
    match = re.search(r"(?:проект(?:е|а)?|назови|название)\s+([a-zа-яё0-9_-]+)", text)
    if match:
        name = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_-]+", "_", match.group(1)).strip("_")
        if name:
            return name
    return f"JarvisProject_{time.strftime('%Y%m%d_%H%M%S')}"


def create_project(command):
    """Create a small Visual Studio-compatible Python or C# project."""
    language = project_language(command)
    name = project_name_from_command(command)
    root = PROJECTS_ROOT / name
    root.mkdir(parents=True, exist_ok=True)
    if language == "csharp":
        project_file = root / f"{name}.csproj"
        source_file = root / "Program.cs"
        project_file.write_text(
            """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
""",
            encoding="utf-8",
        )
        source_file.write_text(
            'Console.WriteLine("Новый проект Джарвиса готов.");\n',
            encoding="utf-8",
        )
    else:
        source_file = root / "main.py"
        source_file.write_text(
            '# Новый проект Джарвиса\nprint("Новый проект готов.")\n',
            encoding="utf-8",
        )
    return root, source_file, language


def open_project_file(path):
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except (OSError, FileNotFoundError):
        return False


def open_in_visual_studio(path):
    """Open a file/folder in Visual Studio when devenv is available."""
    if os.name == "nt":
        devenv = shutil.which("devenv.exe") or shutil.which("devenv")
        if not devenv:
            for base in (
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
                / "Microsoft Visual Studio",
                Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
                / "Microsoft Visual Studio",
            ):
                try:
                    matches = list(base.glob("*/*/Common7/IDE/devenv.exe"))
                    if matches:
                        devenv = str(matches[0])
                        break
                except OSError:
                    continue
        if devenv:
            subprocess.Popen([devenv, str(path)])
            return True
    return open_project_file(path)


def microphone_names():
    try:
        return sr.Microphone.list_microphone_names()
    except Exception:
        return []


def find_start_menu_shortcut(words):
    if os.name != "nt":
        return None
    roots = [
        Path(os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs")),
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    wanted = [word.casefold() for word in words if word]
    candidates = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for shortcut in root.rglob("*.lnk"):
                name = shortcut.stem.lower()
                if all(word.lower() in name for word in wanted):
                    candidates.append(shortcut)
        except OSError:
            continue
    if not candidates:
        return None
    # Prefer an exact shortcut title. This prevents a fuzzy search from
    # accidentally opening an unrelated app when Windows has many shortcuts.
    wanted_name = " ".join(wanted).lower()
    for shortcut in candidates:
        if shortcut.stem.lower().strip() == wanted_name:
            return shortcut
    return candidates[0]


def first_video_url(query, youtube_only=False):
    """Find a direct watch URL instead of leaving the user on a result list."""
    if youtube_only:
        # YouTube embeds the first search results as videoId values in its HTML.
        try:
            response = requests.get(
                "https://www.youtube.com/results",
                params={"search_query": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            response.raise_for_status()
            html = response.text.replace("\\u0026", "&").replace("\\/", "/")
            ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
            if ids:
                return "https://www.youtube.com/watch?v=" + ids[0]
        except (requests.RequestException, ValueError):
            pass
    search = ("site:youtube.com/watch " if youtube_only else "") + query
    try:
        response = requests.get(
            "https://www.google.com/search",
            params={"q": search, "tbm": "vid", "num": 10},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        html = response.text.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
        if youtube_only:
            youtube_links = re.findall(r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]{6,}", html)
            if youtube_links:
                return youtube_links[0]
        if youtube_only:
            return None
        generic = re.findall(r"https?://[^\"'<> ]+", html)
        for link in generic:
            clean = unquote(link.rstrip(".,)&"))
            if any(part in clean.lower() for part in (
                "rutube.ru/video", "vk.com/video", "dailymotion.com/video",
                "vimeo.com/", "youtube.com/watch",
            )):
                return clean
    except (requests.RequestException, ValueError):
        return None
    return None


def first_yandex_video_url(query):
    """Return the first playable Yandex Video preview for a query."""
    try:
        response = requests.get(
            "https://yandex.ru/video/search",
            params={"text": query, "from": "tabbar"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        source = html.unescape(response.text).replace("\\/", "/")
        matches = re.findall(r'href=["\']([^"\']*/video/preview/[^"\']+)["\']', source)
        for match in matches:
            link = urljoin("https://yandex.ru", match).replace(" ", "%20")
            return link
    except (requests.RequestException, ValueError):
        pass
    return None


def clean_redirect_url(url):
    """Extract the destination from a search-engine redirect wrapper."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if "google." in parsed.netloc.casefold() and parsed.path == "/url":
            params = parse_qs(parsed.query)
            return unquote(params.get("q", params.get("url", [url]))[0])
    except (TypeError, ValueError):
        pass
    return url


def web_search_result(query):
    """Get a real destination site and a short sentence for Jarvis."""
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return None, None
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        source = response.text
        # DuckDuckGo has changed the order of class/href attributes several
        # times. Accept both forms and decode its /l/?uddg= redirect.
        results = re.findall(
            r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            source, re.I | re.S,
        )
        if not results:
            results = re.findall(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]*>(.*?)</a>',
                source, re.I | re.S,
            )
        if not results:
            return None, None
        raw_url, raw_title = results[0]
        result_url = clean_redirect_url(html.unescape(raw_url))
        parsed_result = urlparse(result_url)
        if "duckduckgo." in parsed_result.netloc.casefold():
            uddg = parse_qs(parsed_result.query).get("uddg", [None])[0]
            if uddg:
                result_url = unquote(uddg)
        title = _news_text(raw_title)
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
            source, re.I | re.S,
        )
        snippet = _news_text(snippets[0]) if snippets else ""
        page_fact = fetch_web_fact(result_url, query)
        sentence = page_fact or f"Нашла: {title}."
        if not page_fact and snippet:
            sentence += f" {snippet}"
        return result_url, exactly_one_news_sentence(sentence, title)
    except (requests.RequestException, ValueError, IndexError):
        return None, None


def fetch_web_fact(url, query):
    """Read a useful one-sentence fact from the page that was opened."""
    query_text = (query or "").casefold()
    if not any(word in query_text for word in (
        "курс", "доллар", "долл", "евро", "валют", "usd", "eur", "руб",
    )):
        return None
    try:
        response = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        response.raise_for_status()
        text = _news_text(response.text)
        # Covers formats such as “1 USD = 82,92 RUB” and
        # “доллар США ... 82.92 руб”.
        patterns = (
            r"(?:1\s*)?(?:USD|доллар(?:а|ов)?(?:\s+США)?)\s*[:=]\s*"
            r"([0-9]{1,5}(?:[,.][0-9]{1,4})?)\s*(?:RUB|руб\.?)",
            r"(?:USD|доллар(?:а|ов)?(?:\s+США)?)[^0-9]{0,80}"
            r"([0-9]{1,5}(?:[,.][0-9]{1,4})?)\s*(?:RUB|руб\.?)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                value = match.group(1).replace(".", ",")
                return f"По данным открытого сайта, один доллар США стоит {value} рубля."
        # Some pages put the currency code after the number.
        match = re.search(
            r"([0-9]{1,5}(?:[,.][0-9]{1,4})?)\s*(?:RUB|руб\.?)"
            r"[^.]{0,80}(?:USD|доллар)",
            text, re.I,
        )
        if match:
            value = match.group(1).replace(".", ",")
            return f"По данным открытого сайта, один доллар США стоит {value} рубля."
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def browser_request(command):
    text = command.lower().strip()
    video_words = ("фильм", "мультфильм", "мультик", "видео", "сериал", "трейлер", "клип")
    fullscreen = any(word in text for word in ("полный экран", "на весь экран", "во весь экран"))
    if any(word in text for word in ("ютуб", "youtube")):
        remainder = text
        for word in ("открой", "открыть", "запусти", "запустить", "включи",
                     "найди", "посмотри", "покажи", "на youtube", "в youtube",
                     "ютуб", "youtube"):
            remainder = remainder.replace(word, " ")
        if any(word in text for word in ("включи", "найди", "посмотри", "покажи")) or remainder.strip():
            query = text
            for word in ("открой", "открыть", "запусти", "запустить", "включи",
                         "найди", "посмотри", "покажи", "на youtube", "в youtube",
                         "ютуб", "youtube"):
                query = query.replace(word, " ")
            query = query.strip()
            direct = first_video_url(query, youtube_only=True)
            open_url(direct or ("https://www.youtube.com/results?search_query=" + quote_plus(query)))
            if query:
                schedule_youtube_controls()
        else:
            open_url("https://www.youtube.com/")
        if fullscreen:
            schedule_fullscreen()
        return jarvis_ack("открываю YouTube")
    if "яндекс" in text and ("видео" in text or "фильм" in text or "сериал" in text):
        query = text
        for word in ("открой", "открыть", "запусти", "запустить", "включи",
                     "найди", "посмотри", "покажи", "яндекс", "видео", "фильм", "сериал"):
            query = query.replace(word, " ")
        query = query.strip()
        direct = first_yandex_video_url(query)
        open_url(direct or ("https://yandex.ru/video/search?text=" + quote_plus(query)))
        schedule_fullscreen()
        return jarvis_ack("ищу это в Яндекс Видео", 1)
    if "музык" not in text and any(word in text for word in ("браузер", "google", "гугл", "яндекс")):
        open_url("https://www.google.com/")
        return jarvis_ack("открываю браузер", 2)
    # A video request means "play/find a video", not a normal web search.
    # Prefer YouTube for an unspecified video source so the user lands on
    # playable media rather than a Google page full of ordinary links.
    if any(word in text for word in ("включи", "найди", "поищи", "поиск")) or any(
        word in text for word in video_words
    ):
        query = text
        for word in ("открой", "открыть", "запусти", "запустить", "включи",
                     "найди", "поищи", "поиск", "покажи", "посмотри"):
            query = query.replace(word, " ")
        query = query.strip()
        if "youtube" in text or "ютуб" in text:
            direct = first_video_url(query, youtube_only=True)
            url = direct or ("https://www.youtube.com/results?search_query=" + quote_plus(query))
        elif any(word in text for word in video_words):
            direct = first_yandex_video_url(query)
            url = direct or ("https://yandex.ru/video/search?text=" + quote_plus(query) + "&from=tabbar")
        else:
            # Open the actual first result when the provider returns one.
            # Keep Google's "I'm Feeling Lucky" redirect as the fallback:
            # it is more reliable on the user's Windows/browser setup than
            # silently leaving the request without opening anything.
            direct, result_sentence = web_search_result(query)
            if direct:
                open_url(direct)
                return result_sentence or jarvis_ack("открываю первый подходящий результат", 1)
            url = "https://www.google.com/search?q=" + quote_plus(query) + "&btnI=1"
        open_url(url)
        if fullscreen or any(word in text for word in video_words):
            schedule_fullscreen()
        return (
            jarvis_ack("открываю первое видео в Яндекс Видео")
            if any(word in text for word in video_words)
            else jarvis_ack("открываю первый подходящий результат", 1)
        )
    return None


def _windows_input():
    """Return a small Windows input adapter, or None on other platforms."""
    if os.name != "nt":
        return None
    import ctypes
    return ctypes.windll.user32


def _press_virtual_key(user32, virtual_key):
    """Send a real hardware-like key press to the foreground window."""
    # keybd_event is ignored by some Chromium/Electron windows. SendInput
    # with a scan code is accepted by both browser players and Yandex Music.
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

    scan = user32.MapVirtualKeyW(virtual_key, 0)
    extra = wintypes.ULONG(0)
    events = (INPUT * 2)(
        INPUT(1, INPUT_UNION(KEYBDINPUT(0, scan, 0x0008, 0, ctypes.pointer(extra)))),
        INPUT(1, INPUT_UNION(KEYBDINPUT(0, scan, 0x0008 | 0x0002, 0, ctypes.pointer(extra)))),
    )
    if user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT)) != 2:
        user32.keybd_event(virtual_key, 0, 0, 0)
        user32.keybd_event(virtual_key, 0, 2, 0)


def _focus_player_window(user32, title_words, process_names=()):
    """Focus the browser/player window instead of the Tkinter chat window."""
    import ctypes
    from ctypes import wintypes

    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def enum_callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.lower()
        process_match = False
        if process_names:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = user32.OpenProcess(0x1000, False, pid.value)
            if process:
                path_buffer = ctypes.create_unicode_buffer(520)
                path_length = wintypes.DWORD(len(path_buffer))
                if user32.QueryFullProcessImageNameW(
                    process, 0, path_buffer, ctypes.byref(path_length)
                ):
                    process_match = Path(path_buffer.value).name.lower() in process_names
                user32.CloseHandle(process)
        if any(word in title for word in title_words) or process_match:
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_callback, 0)
    if not found:
        return False
    hwnd = found[0]
    # Restore a minimized window and explicitly make it foreground. A key
    # sent while Jarvis has focus is the reason Space/K used to work only in
    # the chat window.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.35)
    return True


def _press_media_key(user32, virtual_key):
    """Send a media key through its virtual-key code, not its scan code."""
    # Media virtual keys (B0/B1/B3) often have no usable scan code.
    # SendInput with wScan=0 is ignored by some Windows shells and Chromium,
    # while keybd_event with the virtual key produces the same global signal
    # as the laptop's Fn media shortcut and also shows the Windows overlay.
    user32.keybd_event(virtual_key, 0, 0, 0)
    user32.keybd_event(virtual_key, 0, 2, 0)


def _yandex_music_key(media_key, fn_key):
    """Send the Windows media equivalent of a laptop Fn+F-key.

    Fn is normally consumed by the keyboard firmware, so Windows cannot
    reliably press the literal Fn modifier through SendInput. The media
    virtual keys below are the signal that the laptop firmware sends to
    Windows when Fn+F5/F6/F7 is pressed.
    """
    user32 = _windows_input()
    if not user32:
        return False
    # Do not search for or focus a window here. Yandex Music in a browser,
    # fullscreen mode, and the desktop client all consume the global media
    # command, while their window titles and processes are different.
    _press_media_key(user32, media_key)
    return True


def yandex_music_action(action):
    """Send Fn+F5/F6/F7's Windows equivalent to Yandex Music."""
    # VK_MEDIA_PREV_TRACK, VK_MEDIA_NEXT_TRACK, VK_MEDIA_PLAY_PAUSE.
    keys = {
        "previous": (0xB1, 0x74),
        "next": (0xB0, 0x75),
        "play_pause": (0xB3, 0x76),
    }
    try:
        media_key, fn_key = keys[action]
        return _yandex_music_key(media_key, fn_key)
    except (KeyError, OSError):
        return False


def schedule_music_playback():
    """Wait for Yandex Music to load, then emulate Fn+F7 (play/pause)."""
    def apply_controls():
        time.sleep(5)
        try:
            for _ in range(15):
                if yandex_music_action("play_pause"):
                    break
                time.sleep(1)
        except Exception:
            pass
    threading.Thread(target=apply_controls, daemon=True).start()


def schedule_fullscreen():
    def press_fullscreen():
        # F is the YouTube fullscreen shortcut. M is handled separately after
        # loading so the video sound can be enabled before fullscreen.
        time.sleep(8)
        try:
            user32 = _windows_input()
            if user32 and _focus_player_window(user32, ("youtube", "яндекс", "yandex", "rutube", "video")):
                _press_virtual_key(user32, 0x46)
        except Exception:
            pass
    threading.Thread(target=press_fullscreen, daemon=True).start()


def schedule_youtube_controls():
    """Focus YouTube after loading and press M once to enable its sound."""
    def press_mute_toggle():
        time.sleep(7)
        try:
            user32 = _windows_input()
            if user32 and _focus_player_window(user32, ("youtube",)):
                _press_virtual_key(user32, 0x4D)  # YouTube: M = mute/unmute
        except Exception:
            pass
    threading.Thread(target=press_mute_toggle, daemon=True).start()


def perform_input_action(command):
    text = command.lower().strip()
    try:
        import ctypes
        user32 = ctypes.windll.user32 if os.name == "nt" else None
        # Yandex Music on this laptop is controlled by Fn+F5/F6/F7:
        # previous, next, and play/pause. Handle these phrases before the
        # generic keyboard parser so "пауза" does not get sent to Jarvis.
        music_action = None
        if any(phrase in text for phrase in (
            "поставь на паузу", "поставь музыку на паузу",
            "пауза", "возобнови музыку", "продолжи музыку",
            "продолжай", "продолжаем", "продолжай музыку",
        )):
            music_action = "play_pause"
        elif any(phrase in text for phrase in (
            "следующий трек", "следующая песня", "следующая композиция",
            "дальше", "переключи песню",
        )):
            music_action = "next"
        elif any(phrase in text for phrase in (
            "предыдущий трек", "предыдущая песня", "предыдущая композиция",
            "назад", "верни песню",
        )):
            music_action = "previous"
        if music_action:
            if yandex_music_action(music_action):
                labels = {
                    "play_pause": "Fn+F7 — пауза/воспроизведение",
                    "next": "Fn+F6 — следующий трек",
                    "previous": "Fn+F5 — предыдущий трек",
                }
                return jarvis_ack(labels[music_action])
            return "Окно Яндекс Музыки не найдено."
        if any(phrase in text for phrase in (
            "сверни все окна", "свернуть все окна", "сверни окна",
            "свернуть окна", "покажи рабочий стол",
        )):
            if user32:
                _windows_desktop_action("minimize")
            return jarvis_ack("сворачиваю все окна")
        if any(phrase in text for phrase in (
            "верни все окна", "вернуть все окна", "восстанови все окна",
            "восстановить все окна", "верни окна", "вернуть окна",
        )):
            if user32:
                _windows_desktop_action("restore")
            return jarvis_ack("возвращаю окна", 1)
        if any(phrase in text for phrase in (
            "выключи звук", "отключи звук", "без звука", "убери звук",
            "включи беззвучный режим",
        )):
            if user32:
                user32.keybd_event(0xAD, 0, 0, 0)
                user32.keybd_event(0xAD, 0, 2, 0)
            return jarvis_ack("выключаю звук")
        if any(phrase in text for phrase in (
            "включи звук", "включить звук", "верни звук", "со звуком",
        )):
            if user32:
                user32.keybd_event(0xAD, 0, 0, 0)
                user32.keybd_event(0xAD, 0, 2, 0)
            return jarvis_ack("включаю звук", 1)
        if any(phrase in text for phrase in (
            "увеличь громкость", "сделай громче", "громче", "прибавь громкость",
        )):
            return change_system_volume(text, user32, 0xAF, "увеличиваю")
        if any(phrase in text for phrase in (
            "уменьши громкость", "сделай тише", "тише", "убавь громкость",
        )):
            return change_system_volume(text, user32, 0xAE, "уменьшаю")
        if re.search(r"(?:громкость|звук).{0,12}\b(?:на\s*)?\d{1,3}\s*процент", text) or re.search(
            r"\bгромкость\s+\d{1,3}\b", text
        ):
            return set_system_volume(text, user32)
        if "левую кнопку мыши" in text or "левая кнопка мыши" in text:
            if user32:
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
            return jarvis_ack("нажимаю левую кнопку мыши")
        if "правую кнопку мыши" in text or "правая кнопка мыши" in text:
            if user32:
                user32.mouse_event(0x0008, 0, 0, 0, 0)
                user32.mouse_event(0x0010, 0, 0, 0, 0)
            return jarvis_ack("нажимаю правую кнопку мыши", 1)
        if "колесо мыши" in text or "среднюю кнопку мыши" in text:
            if user32:
                user32.mouse_event(0x0020, 0, 0, 0, 0)
                user32.mouse_event(0x0040, 0, 0, 0, 0)
            return jarvis_ack("нажимаю колесо мыши", 2)
        keys = {
            "пробел": "space", "энтер": "enter", "ввод": "enter",
            "escape": "esc", "эскейп": "esc", "таб": "tab",
            "удалить": "delete", "бекспейс": "backspace",
            "стрелку вверх": "up", "стрелку вниз": "down",
            "стрелку влево": "left", "стрелку вправо": "right",
            "альт": "alt", "контрол": "ctrl", "шифт": "shift",
            "клавишу k": "k", "клавишу кей": "k", "нажми k": "k",
            "клавишу к": "k", "нажми к": "k", "букву к": "k", "букву кей": "k",
            "клавишу m": "m", "клавишу эм": "m", "нажми m": "m",
            "клавишу м": "m", "нажми м": "m", "букву м": "m", "букву эм": "m",
            "клавишу f": "f", "клавишу эф": "f", "нажми f": "f",
            "клавишу ф": "f", "нажми ф": "f", "букву ф": "f", "букву эф": "f",
            "плей": "media_play", "воспроизведение": "media_play",
            "пауза": "media_pause",
        }
        for phrase, key in keys.items():
            if phrase in text:
                if user32:
                    virtual_keys = {
                        "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
                        "delete": 0x2E, "backspace": 0x08, "up": 0x26, "down": 0x28,
                        "left": 0x25, "right": 0x27, "alt": 0x12, "ctrl": 0x11,
                        "shift": 0x10, "k": 0x4B, "m": 0x4D, "f": 0x46,
                        "media_play": 0xB3, "media_pause": 0xB3,
                    }
                    vk = virtual_keys[key]
                    _press_virtual_key(user32, vk)
                return jarvis_ack(f"нажимаю {phrase}")
    except Exception:
        return "Не удалось выполнить действие клавиатуры или мыши."
    return None


def _press_hotkey(user32, modifier, key):
    user32.keybd_event(modifier, 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 2, 0)
    user32.keybd_event(modifier, 0, 2, 0)


def _windows_desktop_action(action):
    """Toggle the desktop directly, without starting PowerShell."""
    if os.name != "nt":
        return
    user32 = _windows_input()
    if not user32:
        return
    # Win+D is a toggle: both minimize and restore are handled by Windows.
    user32.keybd_event(0x5B, 0, 0, 0)  # left Windows
    user32.keybd_event(0x44, 0, 0, 0)  # D
    user32.keybd_event(0x44, 0, 2, 0)
    user32.keybd_event(0x5B, 0, 2, 0)


def change_system_volume(text, user32, virtual_key, action):
    count_match = re.search(r"(\d+)\s*(?:раз|делени|процент)", text)
    count = max(1, min(20, int(count_match.group(1)))) if count_match else 1
    if user32:
        for _ in range(count):
            user32.keybd_event(virtual_key, 0, 0, 0)
            user32.keybd_event(virtual_key, 0, 2, 0)
    return jarvis_ack(f"{action} громкость")


def set_system_volume(text, user32):
    match = re.search(r"(?:громкость|звук).*?(?:на\s*)?(\d{1,3})\s*(?:процент|%)?", text)
    if not match:
        match = re.search(r"громкость\s+(\d{1,3})", text)
    if not match:
        return "Скажите, например: громкость на 60 процентов."
    percent = max(0, min(100, int(match.group(1))))
    if os.name == "nt":
        try:
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
            endpoint = speakers.EndpointVolume
            endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
            return jarvis_ack(f"устанавливаю громкость {percent} процентов")
        except Exception:
            pass
    # Fallback when the optional Windows audio API is unavailable.
    if user32:
        for _ in range(20):
            user32.keybd_event(0xAE, 0, 0, 0)
            user32.keybd_event(0xAE, 0, 2, 0)
        for _ in range(round(percent / 5)):
            user32.keybd_event(0xAF, 0, 0, 0)
            user32.keybd_event(0xAF, 0, 2, 0)
    return f"Устанавливаю громкость примерно на {percent} процентов."


def open_url(url):
    """Open a URL through the operating system's normal browser association."""
    try:
        if os.name == "nt":
            # os.startfile uses the same ShellExecute path as double-clicking
            # a link in Explorer.  It is more reliable than `cmd /c start`
            # for long URLs containing encoded Cyrillic query parameters.
            os.startfile(url)
        else:
            webbrowser.open(url)
        return True
    except (OSError, AttributeError):
        try:
            if os.name == "nt":
                subprocess.Popen(
                    ["cmd", "/c", "start", "", url],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return True
            return bool(webbrowser.open_new(url))
        except (OSError, webbrowser.Error):
            return False


def _news_text(value):
    """Turn an RSS title/description into readable speech text."""
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _news_item_is_allowed(title, description):
    haystack = f"{title} {description}".casefold()
    return not any(word in haystack for word in NEWS_BLOCKED_WORDS)


def fetch_news(limit=5):
    """Fetch a small technology digest from direct publisher RSS feeds."""
    items = []
    seen = set()
    errors = []
    for feed_name, feed_url in NEWS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 Jarvis News Reader"},
                timeout=5,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            entries = list(root.findall(".//item")) + list(root.findall(".//{*}entry"))
            for entry in entries[:10]:
                title = _news_text(
                    entry.findtext("title") or entry.findtext("{*}title")
                )
                link = (entry.findtext("link") or "").strip()
                if not link:
                    atom_link = entry.find("{*}link")
                    link = (atom_link.get("href") if atom_link is not None else "").strip()
                description = _news_text(
                    entry.findtext("description")
                    or entry.findtext("{*}description")
                    or entry.findtext("{*}summary")
                    or entry.findtext("{*}content")
                )
                if not title or title.casefold() in seen or not _news_item_is_allowed(title, description):
                    continue
                seen.add(title.casefold())
                items.append({
                    "title": title,
                    "link": link,
                    "description": description,
                    "source": feed_name,
                })
                if len(items) >= limit:
                    return items
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            errors.append(f"{feed_name}: {exc}")
    if not items and errors:
        raise requests.RequestException("; ".join(errors))
    return items


def fetch_article_text(url, max_chars=3200):
    """Extract article paragraphs while ignoring ads and page chrome."""
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 Jarvis News Reader"},
        timeout=12,
    )
    response.raise_for_status()
    source = response.text
    # The article body is inside <article> on zakon.kz.  The fallback class
    # match also supports small layout changes without reading the whole page.
    match = re.search(r"<article\b[^>]*>(.*?)</article>", source, re.I | re.S)
    if not match:
        match = re.search(
            r'<(?:div|section)\b[^>]*(?:article|post-content|article-content|'
            r'content-body)[^>]*>(.*?)</(?:div|section)>',
            source,
            re.I | re.S,
        )
    scope = match.group(1) if match else source
    scope = re.sub(r"<(script|style|noscript|iframe|aside)\b.*?</\1>", " ", scope, flags=re.I | re.S)
    paragraphs = []
    for raw in re.findall(r"<p\b[^>]*>(.*?)</p>", scope, re.I | re.S):
        text = _news_text(raw)
        if len(text) < 35:
            continue
        if any(word in text.casefold() for word in (
            "реклама", "поделитесь новостью", "добавить в google",
            "читайте также", "следите за новостями",
        )):
            continue
        paragraphs.append(text)
    body = " ".join(paragraphs)
    if not body:
        # Some publishers expose a clean articleBody in JSON-LD but render
        # the visible article through JavaScript.  This avoids reading ads.
        for raw_json in re.findall(
            r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            source,
            re.I | re.S,
        ):
            try:
                data = json.loads(html.unescape(raw_json.strip()))
            except (TypeError, ValueError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("articleBody"):
                    body = _news_text(candidate["articleBody"])
                    break
            if body:
                break
    if not body:
        raise ValueError("текст статьи не найден")
    return response.url, body[:max_chars]


def news_digest(items):
    """Build a concise Russian script for the separate neural voice."""
    if not items:
        return "Свежих подходящих новостей сейчас не нашлось. Попробуйте повторить запрос позже."
    lines = [f"Главные новости науки и технологий. Нашёл {len(items)} материалов."]
    for index, item in enumerate(items, 1):
        source = f" Источник: {item['source']}." if item["source"] else ""
        # Headlines can be long; a short digest sounds much better than
        # reading an entire RSS description aloud.
        lines.append(f"{index}. {item['title']}.{source}")
    return " ".join(lines)


def article_digest(item, article_text):
    """Speech script: one short sentence per article, never the full body."""
    return exactly_one_news_sentence(article_text, item.get("title", ""))


def short_news_text(text, max_chars=850):
    """Keep a readable lead: several sentences, never an entire article."""
    text = _news_text(text)
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = []
    total = 0
    for sentence in sentences:
        if not sentence:
            continue
        if total + len(sentence) + 1 > max_chars:
            break
        result.append(sentence)
        total += len(sentence) + 1
        if len(result) >= 4:
            break
    return " ".join(result) or text[:max_chars].rsplit(" ", 1)[0] + "…"


def exactly_two_news_sentences(text, fallback_title=""):
    """Compatibility wrapper: news now uses exactly one sentence."""
    return exactly_one_news_sentence(text, fallback_title)


def exactly_one_news_sentence(text, fallback_title=""):
    """Return exactly one sentence, never an entire article, for TTS."""
    text = _news_text(text)
    if not text:
        text = fallback_title or "Главная информация новости пока недоступна"
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]
    first = sentences[0] if sentences else text.rstrip(".!?")
    return f"{first.rstrip('.!?')}."


def select_news_stories(items, limit=3):
    """Prefer three different topics instead of three similar headlines."""
    groups = {
        "cars": ("автомоб", "машин", "электромоб", "tesla", "авто", "car", "vehicle"),
        "ai": ("искусственн", "нейросет", "ии", " ai ", "artificial intelligence", "робот"),
        "space": ("nasa", "космос", "спутник", "ракета", "астероид", "space", "moon"),
    }
    selected = []
    used_topics = set()
    for topic, words in groups.items():
        for item in items:
            haystack = f" {item.get('title', '')} {item.get('description', '')} ".casefold()
            if topic not in used_topics and any(word in haystack for word in words):
                selected.append(item)
                used_topics.add(topic)
                break
    for item in items:
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    return selected[:limit]


def focus_news_tab(tab_number):
    """Focus the browser and move through the three newly opened tabs."""
    if os.name != "nt":
        return False
    try:
        user32 = _windows_input()
        if not user32 or not _focus_player_window(
            user32,
            ("chrome", "edge", "firefox", "яндекс браузер", "браузер"),
            ("chrome.exe", "msedge.exe", "firefox.exe", "browser.exe"),
        ):
            return False
        # The news loader calls this once after opening all stories. At that
        # point the newest opened tab is active; move back to the first of the
        # three new tabs. The remaining tabs are selected incrementally while
        # the stories are read.
        if tab_number == 1:
            for _ in range(2):
                user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
                user32.keybd_event(0x10, 0, 0, 0)  # Shift down
                user32.keybd_event(0x09, 0, 0, 0)  # Tab down
                user32.keybd_event(0x09, 0, 2, 0)
                user32.keybd_event(0x10, 0, 2, 0)
                user32.keybd_event(0x11, 0, 2, 0)
        return True
    except Exception:
        return False


def news_command(command):
    text = command.casefold()
    return "новост" in text or any(phrase in text for phrase in (
        "какие новости", "расскажи новости", "расскажи про новости",
        "что нового", "что происходит", "дайджест",
    ))


def compose_command(command):
    """Recognize the explicit «составь …» writing command."""
    match = re.match(r"^\s*состав(?:ь|ить)\b\s*(.*)$", command or "", re.I)
    return match.group(1).strip() if match else None


def drawing_command(command):
    """Recognize a request to draw something in Microsoft Paint."""
    return bool(re.search(r"\b(нарисуй|нарисовать|рисуй|порисуй)\b", command or "", re.I))


def open_paint():
    """Open Microsoft Paint without passing voice text to a shell."""
    if os.name != "nt":
        return False, "Рисование в Paint доступно только в Windows."
    try:
        subprocess.Popen(
            ["mspaint.exe"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True, "Открываю Paint. Можно рисовать."
    except OSError:
        return False, "Не удалось открыть Paint."


def save_svg_and_open_paint(svg_text):
    """Rasterize an AI-generated SVG and open the compatible PNG in Paint."""
    if os.name != "nt":
        return False, "Рисование в Paint доступно только в Windows."
    svg_path = Path(APP_DATA_DIR) / "Jarvis_рисунок.svg"
    png_path = Path(APP_DATA_DIR) / "Jarvis_рисунок.png"
    try:
        svg_path.write_text(svg_text, encoding="utf-8")
        browsers = (
            os.environ.get("PROGRAMFILES", "") + r"\Microsoft\Edge\Application\msedge.exe",
            os.environ.get("PROGRAMFILES(X86)", "") + r"\Microsoft\Edge\Application\msedge.exe",
            shutil.which("msedge"),
            shutil.which("chrome"),
        )
        browser = next(
            (candidate for candidate in browsers if candidate and Path(candidate).is_file()),
            None,
        )
        if not browser:
            return False, None
        subprocess.run(
            [
                browser, "--headless", "--disable-gpu", "--hide-scrollbars",
                "--window-size=800,600", f"--screenshot={png_path}",
                svg_path.as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=20,
            check=False,
        )
        if not png_path.is_file():
            return False, None
        subprocess.Popen(
            ["mspaint.exe", str(png_path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True, png_path
    except OSError:
        return False, None
    except subprocess.SubprocessError:
        return False, None


def save_composition_to_notepad(text):
    """Save generated text and open it in Windows Notepad."""
    folder = Path.home() / "Documents"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path(APP_DATA_DIR)
    path = folder / f"Jarvis_составленный_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        path.write_text(text, encoding="utf-8-sig")
        if os.name == "nt":
            subprocess.Popen(
                ["notepad.exe", str(path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            webbrowser.open(path.as_uri())
        return True, path
    except OSError:
        return False, None


def news_url():
    """Return a reliable publisher page instead of Google News."""
    return NEWS_FALLBACK_URL


def find_blender_executable():
    """Find Blender even when it was installed into a Steam library."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam/steamapps/common",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam/steamapps/common",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Blender Foundation",
    ]
    for root in candidates:
        if not root.is_dir():
            continue
        try:
            matches = list(root.glob("Blender*/blender.exe"))
            if matches:
                return matches[0]
            direct = root / "blender.exe"
            if direct.is_file():
                return direct
        except OSError:
            continue
    return None


def open_local_app(command):
    """Open a common app without evaluating arbitrary voice text as shell code."""
    text = command.lower().strip()
    if "давай поработаем" in text:
        if os.name == "nt" and DEFAULT_WORK_PROJECT.is_file():
            os.startfile(str(DEFAULT_WORK_PROJECT))
            return True, jarvis_ack("открываю первый проект Visual Studio", 1)
        return False, f"Проект Visual Studio не найден: {DEFAULT_WORK_PROJECT}"
    # A YouTube destination always wins over an application name inside the
    # query: "включи на YouTube Dota 2" must play a video, not launch Steam.
    if any(word in text for word in ("youtube", "ютуб")) and any(
        word in text for word in ("включи", "найди", "поищи", "посмотри", "покажи")
    ):
        browser_reply = browser_request(text)
        if browser_reply:
            return True, browser_reply
    aliases = {
        "браузер": ("browser", "https://www.google.com"),
        "гугл": ("browser", "https://www.google.com"),
        "google": ("browser", "https://www.google.com"),
        "яндекс музыка": ("shortcut", ("яндекс", "музык")),
        "яндекс музыку": ("shortcut", ("яндекс", "музык")),
        "музыка": ("shortcut", ("яндекс", "музык")),
        "музыку": ("shortcut", ("яндекс", "музык")),
        "музык": ("shortcut", ("яндекс", "музык")),
        "яндекс": ("shortcut", ("яндекс",)),
        "steam": ("steam", "steam://open/main"),
        "стим": ("steam", "steam://open/main"),
        "rust": ("steam", "steam://rungameid/252490"),
        "раст": ("steam", "steam://rungameid/252490"),
        "apex legends": ("steam", "steam://rungameid/1172470"),
        "апекс legends": ("steam", "steam://rungameid/1172470"),
        "апекс": ("steam", "steam://rungameid/1172470"),
        "dayz": ("steam", "steam://rungameid/221100"),
        "дейз": ("steam", "steam://rungameid/221100"),
        "blender": ("blender", None),
        "блендер": ("blender", None),
        "dota": ("dota", "steam://rungameid/570"),
        "дота": ("dota", "steam://rungameid/570"),
        "dota 2": ("dota", "steam://rungameid/570"),
        "xbox": ("shortcut", ("xbox",)),
        "x box": ("shortcut", ("xbox",)),
        "иксбокс": ("shortcut", ("xbox",)),
        "икс бокс": ("shortcut", ("xbox",)),
        "калькулятор": ("calc", None),
        "калькулятор": ("calc", None),
        "блокнот": ("notepad", None),
        "notepad": ("notepad", None),
        "проводник": ("explorer", None),
        "файлы": ("explorer", None),
        "дискорд": ("discord", None),
        "discord": ("discord", None),
        "телеграм": ("telegram", None),
        "telegram": ("telegram", None),
        "spotify": ("shortcut", ("spotify",)),
        "спотифай": ("shortcut", ("spotify",)),
        "код": ("shortcut", ("visual", "studio", "code")),
        "visual studio code": ("shortcut", ("visual", "studio", "code")),
    }
    target = None
    for phrase, value in aliases.items():
        if phrase in text:
            target = value
            break
    # Resolve explicit applications before web/video handling. Previously
    # browser_request() ran first and swallowed commands such as "открой
    # Discord", sending them through a generic search.
    if target is None:
        browser_reply = browser_request(text)
        if browser_reply:
            return True, browser_reply
    if not target:
        # Unknown names are treated as a web request, which is more useful
        # than claiming an application exists when it does not.
        target = ("shortcut", tuple(text.split()))
    app, url = target
    try:
        if app == "shortcut":
            shortcut = find_start_menu_shortcut(url)
            if shortcut:
                os.startfile(str(shortcut))
                if any(word in text for word in ("музык", "music")):
                    schedule_music_playback()
                return True, jarvis_ack(f"открываю {command}")
            if any(word in text for word in ("музык", "music")):
                open_url(YANDEX_MUSIC_URL)
                schedule_music_playback()
                return True, "Ярлык не найден, открываю Яндекс Музыку в браузере."
            if "xbox" in text or "иксбокс" in text or "икс бокс" in text:
                if os.name == "nt":
                    try:
                        app_id = subprocess.run(
                            [
                                "powershell", "-NoProfile", "-Command",
                                "(Get-StartApps | Where-Object {$_.Name -match 'Xbox'} "
                                "| Select-Object -First 1 -ExpandProperty AppID)",
                            ],
                            capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        ).stdout.strip()
                    except OSError:
                        app_id = ""
                    if app_id:
                        subprocess.Popen(
                            ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        subprocess.Popen(
                            ["cmd", "/c", "start", "", "xbox:"],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    return True, jarvis_ack("открываю Xbox", 1)
            return False, f"Я не нашёл ярлык «{command}» в меню Пуск."
        if app == "blender":
            executable = find_blender_executable()
            if executable:
                subprocess.Popen([str(executable)])
                return True, jarvis_ack(f"запускаю {command}", 1)
            # Blender's Steam app id is a fallback for libraries on another
            # drive that are not covered by the standard installation paths.
            open_url("steam://rungameid/365670")
            return True, jarvis_ack("запускаю Blender через Steam", 2)
        if url:
            if url.startswith("http"):
                open_url(url)
            else:
                open_url(url)
            return True, jarvis_ack(f"открываю {command}")
        if os.name == "nt":
            # Windows Start resolves installed apps, PATH executables, and
            # registered file/protocol handlers (Blender, Steam games, etc.).
            subprocess.Popen(["cmd", "/c", "start", "", app], creationflags=subprocess.CREATE_NO_WINDOW)
            return True, jarvis_ack(f"открываю {command}")
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app])
            return True, jarvis_ack(f"открываю {command}")
        executable = shutil.which(app)
        if executable:
            subprocess.Popen([executable])
            return True, jarvis_ack(f"открываю {command}")
    except (OSError, FileNotFoundError):
        return False, f"Не удалось открыть {command}."
    return False, f"Не удалось открыть {command}."


def close_local_app(command):
    text = command.lower().strip()
    processes = {
        "steam": ["steam.exe"],
        "стим": ["steam.exe"],
        "яндекс": ["YandexMusic.exe"],
        "музыка": ["YandexMusic.exe"],
        "blender": ["blender.exe"],
        "блендер": ["blender.exe"],
        "dota": ["dota2.exe"],
        "дота": ["dota2.exe"],
        "дота 2": ["dota2.exe"],
        "браузер": ["chrome.exe", "msedge.exe", "firefox.exe"],
        "chrome": ["chrome.exe"],
        "google": ["chrome.exe"],
        "дискорд": ["Discord.exe"],
        "discord": ["Discord.exe"],
        "телеграм": ["Telegram.exe"],
        "telegram": ["Telegram.exe"],
        "spotify": ["Spotify.exe"],
    }
    selected = None
    for phrase, names in processes.items():
        if phrase in text:
            selected = names
            break
    if not selected:
        selected = [text.replace(" ", "") + ".exe"]
    if os.name != "nt":
        return False, "Закрытие приложений через голос пока поддерживается в Windows."
    if selected == ["YandexMusic.exe"]:
        # The desktop client has used several executable names across
        # releases. Resolve the actual process instead of assuming one name.
        try:
            listing = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.splitlines()
            detected = []
            for line in listing:
                process_name = line.split('","', 1)[0].strip('"')
                normalized = process_name.casefold().replace(" ", "")
                if normalized.startswith("yandexmusic"):
                    detected.append(process_name)
            if detected:
                selected = detected
        except OSError:
            pass
    closed = False
    for process in selected:
        result = subprocess.run(["taskkill", "/IM", process, "/T", "/F"],
                                capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            closed = True
    return (True, jarvis_ack(f"закрываю {command}", 1)) if closed else (
        False, f"Приложение «{command}» сейчас не запущено."
    )


def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                trial_started REAL NOT NULL,
                plan TEXT NOT NULL DEFAULT 'test'
            )"""
        )
        self.connection.commit()
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS email_verifications (
                email TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                code TEXT NOT NULL,
                expires REAL NOT NULL
            )"""
        )
        self.connection.commit()

    def register(self, email, password):
        try:
            self.connection.execute(
                "INSERT INTO users(email,password_hash,trial_started) VALUES(?,?,?)",
                (email.lower().strip(), password_hash(password), time.time()),
            )
            self.connection.commit()
            return True, "Регистрация прошла успешно."
        except sqlite3.IntegrityError:
            return False, "Пользователь с таким email уже существует."

    def login(self, email, password):
        row = self.connection.execute(
            "SELECT id,email,trial_started,plan FROM users WHERE email=? AND password_hash=?",
            (email.lower().strip(), password_hash(password)),
        ).fetchone()
        return row


class AIResponder:
    VOICE_PERSONALITIES = {
        "6dc11f3f67a543f6ad4537a4a347e224": (
            "Мита: милая, заботливая и слегка кокетливая; оставайся естественной "
            "и не переигрывай.",
        ),
        "cc1b79b1108f4ed3b8aac118ba6ebd07": (
            "Мариарти: умный, хладнокровный хакер с тонкой иронией; говори "
            "уверенно и загадочно.",
        ),
        "fcb391ebe91a438d9c810ae17cde81de": (
            "Рик: резкий, саркастичный и агрессивно-уверенный без оскорблений "
            "пользователя; допустима редкая мягкая ругань по ситуации.",
        ),
        "3674e320208a4da19becbea85d993d6e": (
            "Морти: тревожный, немного напуганный школьник, который всё равно "
            "старается помочь.",
        ),
        "a2acc0d939984f5a96edd720d5564d44": (
            "Губка Боб: игривый, добрый, очень жизнерадостный и немного "
            "непоседливый.",
        ),
    }

    def __init__(self):
        self.backend_url = ""
        self.gateway_token = ""
        self.ai_key = os.getenv("AI_API_KEY", "").strip()
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.mistral_key = os.getenv("MISTRAL_API_KEY", "").strip()

    def _personality_instruction(self):
        enabled = os.getenv("JARVIS_VOICE_PERSONALITY", "").strip().casefold() in {
            "1", "true", "yes", "on", "да",
        }
        if not enabled:
            enabled = QSettings("Jarvis", "JarvisAssistant").value(
                "voice_personality", False, type=bool
            )
        voice_id = os.getenv("JARVIS_VOICE_ID", "").strip()
        if not voice_id:
            voice_id = str(QSettings("Jarvis", "JarvisAssistant").value(
                "voice_id", ""
            )).strip()
        if enabled and voice_id in self.VOICE_PERSONALITIES:
            return (
                "Используй характер выбранного голоса. "
                + self.VOICE_PERSONALITIES[voice_id][0]
                + " "
            )
        return ""

    def configuration_status(self):
        providers = []
        if self.mistral_key:
            providers.append("Mistral")
        if self.deepseek_key:
            providers.append("DeepSeek")
        if self.ai_key:
            providers.append("AI")
        return "Локальные API загружены: " + (", ".join(providers) if providers else "нет")

    def reply(self, command: str) -> str:
        normalized = re.sub(r"\s+", " ", (command or "").casefold()).strip()
        if any(phrase in normalized for phrase in (
            "который час", "сколько времени", "сколько время", "какое время",
        )):
            return f"Сейчас {time.strftime('%H:%M')}, сэр."
        prompt = (
            "Ты голосовой ассистент Джарвис в стиле высокотехнологичного дворецкого. "
            "Ответь по-русски очень коротко, естественно и учтиво, максимум одно предложение. "
            "Не начинай ответ с шаблонов «Так точно, сэр», «Как пожелаете, сэр» или "
            "«Выполняю, сэр» — сразу ответь по сути. Если пользователь просит "
            "простое действие (открыть приложение, включить музыку и т.п.), "
            "не описывай техническое выполнение и не повторяй команду: ответь "
            "живой естественной репликой о результате, например «Послушаем ваши "
            "песни» или «Сейчас открою Steam». "
            + self._personality_instruction()
            + "Запрос пользователя: "
            + command
        )
        if self.mistral_key:
            answer = self._openai_compatible(
                "https://api.mistral.ai/v1/chat/completions",
                self.mistral_key, prompt, model="mistral-small-latest",
            )
            if answer:
                return self._clean_spoken_reply(answer)
        if self.deepseek_key:
            answer = self._openai_compatible(
                "https://api.deepseek.com/chat/completions",
                self.deepseek_key, prompt, model="deepseek-chat",
            )
            if answer:
                return self._clean_spoken_reply(answer)
        if self.ai_key:
            answer = self._openai_compatible(
                "https://api.openai.com/v1/chat/completions", self.ai_key, prompt
            )
            if answer:
                return self._clean_spoken_reply(answer)
        # A missing key must never fall through to the prerecorded "yes"
        # folder. This local fallback is only an emergency answer.
        if "как дела" in normalized or "как ты" in normalized:
            return "У меня всё отлично, сэр, я готов помогать."
        if "привет" in normalized or "здравствуй" in normalized:
            return "Здравствуйте, сэр, рад вас слышать."
        return "Сейчас нейросеть недоступна, сэр. Проверьте интернет и API."

    def _remote_reply(self, prompt):
        if not self.backend_url:
            return None
        headers = {"Content-Type": "application/json"}
        if self.gateway_token:
            headers["Authorization"] = f"Bearer {self.gateway_token}"
        try:
            response = requests.post(
                self.backend_url + "/jarvis/respond",
                headers=headers,
                json={"command": prompt},
                timeout=50,
            )
            response.raise_for_status()
            answer = response.json().get("answer", "")
            return str(answer).strip() or None
        except (requests.RequestException, ValueError, TypeError):
            return None

    def remote_tts(self, text):
        """Request generated Fish Audio speech without exposing its key."""
        if not self.backend_url:
            return None
        headers = {"Content-Type": "application/json"}
        if self.gateway_token:
            headers["Authorization"] = f"Bearer {self.gateway_token}"
        try:
            response = requests.post(
                self.backend_url + "/jarvis/tts",
                headers=headers,
                json={"text": text},
                timeout=70,
            )
            response.raise_for_status()
            return response.content or None
        except requests.RequestException:
            return None

    def search_reply(self, query, found_text):
        """Turn a search result into one natural spoken answer."""
        context = (found_text or "").strip()
        request = (
            "Пользователь попросил найти: "
            + query
            + ". Сайт уже открыт. Сформулируй один короткий ответ по найденной "
            "информации ниже, без фраз «я нашла результат» и без упоминания "
            "поиска. Если точного факта в данных нет, честно скажи, что его "
            "не удалось определить, и не придумывай цифры. Данные: "
            + context
        )
        return self.reply(request)

    def action_reply(self, command, technical_result):
        """Turn a successful local action into a natural spoken response."""
        if not (self.mistral_key or self.deepseek_key or self.ai_key):
            return technical_result
        answer = self.reply(
            "Пользователь попросил: "
            + command
            + ". Действие уже выполнено. Скажи короткую естественную реплику "
            "о результате, не пересказывай технический отчёт: "
            + technical_result
        )
        if "нейросеть недоступна" in answer.casefold():
            return technical_result
        return answer

    def compose_text(self, request):
        """Ask the configured AI to write the requested text for Notepad."""
        if not request:
            return None
        prompt = (
            "Составь готовый текст по запросу пользователя на русском языке. "
            "Сделай его структурированным и полезным, без вступлений от лица "
            "ассистента и без Markdown-ограждений. Если нужен список, используй "
            "нумерацию. Запрос: " + request
        )
        for url, key, model in (
            ("https://api.mistral.ai/v1/chat/completions", self.mistral_key, "mistral-small-latest"),
            ("https://api.deepseek.com/chat/completions", self.deepseek_key, "deepseek-chat"),
            ("https://api.openai.com/v1/chat/completions", self.ai_key, None),
        ):
            if key:
                answer = self._openai_compatible(
                    url, key, prompt, max_tokens=1400, model=model
                )
                if answer:
                    return answer
        return None

    def draw_svg(self, request):
        """Ask the configured AI for a self-contained SVG illustration."""
        prompt = (
            "Создай простую красивую иллюстрацию в формате SVG по запросу. "
            "Верни только полный SVG без Markdown и без пояснений. Используй "
            "viewBox='0 0 800 600', простые фигуры, заливки и контуры, без "
            "внешних изображений, скриптов и ссылок. Запрос: " + request
        )
        for url, key, model in (
            ("https://api.mistral.ai/v1/chat/completions", self.mistral_key, "mistral-small-latest"),
            ("https://api.deepseek.com/chat/completions", self.deepseek_key, "deepseek-chat"),
            ("https://api.openai.com/v1/chat/completions", self.ai_key, None),
        ):
            if key:
                answer = self._openai_compatible(
                    url, key, prompt, max_tokens=1800, model=model
                )
                if answer:
                    answer = re.sub(r"^```(?:svg|xml)?\s*|\s*```$", "", answer.strip(), flags=re.I)
                    if "<svg" in answer and "</svg>" in answer:
                        return answer[answer.find("<svg"):answer.rfind("</svg>") + 6]
        return None

    @staticmethod
    def _clean_spoken_reply(answer):
        """Remove an old prompt's stock greeting if a provider returns it."""
        cleaned = re.sub(
            r"^\s*(?:так точно|как пожелаете|выполняю)\s*,?\s*сэр\s*[—–-]?\s*",
            "", answer, flags=re.I,
        ).strip()
        return cleaned or answer.strip()

    def generate_code(self, request, language):
        language_name = "Python" if language == "python" else "C#"
        prompt = (
            f"Напиши полный рабочий исходный код на {language_name} по запросу пользователя. "
            "Верни только содержимое одного исходного файла, без Markdown, без ``` и без пояснений. "
            "Код должен быть автономным и понятным начинающему разработчику. Запрос: "
            + request
        )
        if self.ai_key:
            answer = self._openai_compatible(
                "https://api.openai.com/v1/chat/completions", self.ai_key, prompt,
                max_tokens=3000,
            )
            if answer:
                return self._clean_code(answer)
        if self.deepseek_key:
            answer = self._openai_compatible(
                "https://api.deepseek.com/chat/completions", self.deepseek_key, prompt,
                max_tokens=3000,
            )
            if answer:
                return self._clean_code(answer)
        return self._fallback_code(request, language)

    def summarize_news(self, title, text):
        """Make a short local summary without waiting for a remote AI call."""
        # RSS already contains the lead of the story. Using it directly makes
        # speech start almost immediately after the tab opens. AI can still be
        # enabled explicitly for users who prefer a polished rewrite.
        local_summary = exactly_one_news_sentence(
            short_news_text(text, NEWS_SPEECH_LIMIT),
            title,
        )
        if os.getenv("NEWS_AI_SUMMARY", "").strip().casefold() not in (
            "1", "true", "yes", "да",
        ):
            return local_summary
        prompt = (
            "Сделай очень короткий понятный пересказ новости на русском языке. "
            "Нужно ровно одно предложение: только главный факт новости. "
            "какой главный факт. Не добавляй рекламу, призывы, ссылки, "
            "политику и фразы вроде «подписывайтесь». Не говори о том, "
            "что это пересказ. Заголовок: "
            + title
            + "\nТекст новости:\n"
            + short_news_text(text, 900)
        )
        for url, key in (
            ("https://api.openai.com/v1/chat/completions", self.ai_key),
            ("https://api.deepseek.com/chat/completions", self.deepseek_key),
        ):
            if key:
                answer = self._openai_compatible(url, key, prompt, max_tokens=220)
                if answer:
                    return exactly_one_news_sentence(answer, title)
        return exactly_one_news_sentence(
            short_news_text(text, NEWS_SPEECH_LIMIT),
            title,
        )

    @staticmethod
    def _clean_code(answer):
        match = re.search(r"```(?:python|csharp|cs|c#)?\s*(.*?)```", answer, re.S | re.I)
        return (match.group(1) if match else answer).strip()

    @staticmethod
    def _fallback_code(request, language):
        if "калькулятор" in request.casefold() or "calculator" in request.casefold():
            if language == "csharp":
                return (
                    'Console.Write("Первое число: ");\n'
                    'double a = double.Parse(Console.ReadLine()!);\n'
                    'Console.Write("Операция (+, -, *, /): ");\n'
                    'string op = Console.ReadLine()!;\n'
                    'Console.Write("Второе число: ");\n'
                    'double b = double.Parse(Console.ReadLine()!);\n'
                    'double result = op switch { "+" => a + b, "-" => a - b, "*" => a * b, "/" => a / b, _ => 0 };\n'
                    'Console.WriteLine($"Результат: {result}");\n'
                )
            return (
                "a = float(input('Первое число: '))\n"
                "op = input('Операция (+, -, *, /): ')\n"
                "b = float(input('Второе число: '))\n"
                "result = {'+': a + b, '-': a - b, '*': a * b, '/': a / b}[op]\n"
                "print(f'Результат: {result}')\n"
            )
        return (
            "# AI_API_KEY или DEEPSEEK_API_KEY не настроен.\n"
            "# Добавьте ключ в .env и повторите команду.\n"
        )

    @staticmethod
    def _openai_compatible(url, key, prompt, max_tokens=80, model=None):
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model or (
                        "gpt-4o-mini" if "openai" in url else "deepseek-chat"
                    ),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                    "max_tokens": max_tokens,
                },
                timeout=15,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, ValueError):
            return None


class VoiceWorker:
    def __init__(self, on_status, on_reply, device_index=None):
        self.on_status = on_status
        self.on_reply = on_reply
        self.stop_event = threading.Event()
        self.thread = None
        self.recognizer = sr.Recognizer()
        self.ai = AIResponder()
        self.voice_library = VoiceLibrary()
        fish_ready = bool(
            os.getenv("FISH_AUDIO_API_KEY", "").strip()
            and (
                self.online_voice_id()
                or os.getenv("FISH_AUDIO_REFERENCE_ID", "").strip()
            )
        )
        fish_label = "Fish Audio настроен" if fish_ready else "Fish Audio не настроен"
        self.on_status(f"{self.ai.configuration_status()}; {fish_label}")
        self.on_status(f"Голоса Джарвиса: {self.voice_library.summary()}")
        self.active_project = None
        self.active_source = None
        self.active_language = None
        self.device_index = device_index
        self.news_thread = None
        self.news_stop_event = threading.Event()
        self.news_process = None
        self.news_process_lock = threading.Lock()
        self.protocol_confirmation_until = 0.0
        self.protocol_confirmation_active = threading.Event()
        self.protocol_timer = None
        self.auto_tik_tok_enabled = os.getenv(
            "JARVIS_AUTO_TIK_TOK", ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.auto_protocol_cancel = threading.Event()
        self.auto_protocol_timer = None

    @staticmethod
    def online_voice_id():
        """Return the selected Fish Audio voice, if one was chosen."""
        selected = os.getenv("JARVIS_VOICE_ID", "").strip()
        if selected:
            return selected
        return QSettings("Jarvis", "JarvisAssistant").value("voice_id", "").strip()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.auto_protocol_cancel.set()
        self.protocol_confirmation_until = 0.0
        self.protocol_confirmation_active.clear()
        self.stop_news()
        self.on_status("Ассистент остановлен")

    def play_recorded(self, category):
        """Play a saved phrase without sending an artificial text to history."""
        if self.voice_library.play(category):
            return True
        self.on_status(f"В папке голосов нет рабочей фразы «{category}».")
        return False

    def say(self, text, category=None):
        self.on_reply(text)
        # Preset clips are only for the local Jarvis voice.  When another
        # profile is selected every answer must be generated by that profile.
        selected_voice = self.online_voice_id()
        if category and not selected_voice and self.voice_library.play(category):
            return
        if category:
            self.on_status(
                f"Файл голоса для категории «{category}» не воспроизведён — "
                "использую стандартный голос."
            )
        fish_key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
        # Empty selection keeps the original local Jarvis voice.  A selected
        # profile uses its Fish Audio reference ID for generated replies.
        fish_reference = (
            selected_voice
            or os.getenv("FISH_AUDIO_REFERENCE_ID", "").strip()
        )
        if fish_key and fish_reference:
            try:
                # Fish Audio is the user's configured Jarvis voice for all
                # generated speech, including answers and search extracts.
                self.say_news_fish(
                    text, threading.Event(), fish_key, fish_reference
                )
                return
            except Exception as exc:
                self.on_status(
                    f"Fish Audio недоступен ({exc}). Использую русский резервный голос."
                )
        # Use a guaranteed Russian male neural voice for generated replies.
        # This prevents Windows from silently choosing Microsoft David/Zira
        # (English or female) when the old .env still contains JARVIS_VOICE=David.
        if os.getenv("JARVIS_USE_RUSSIAN_NEURAL_VOICE", "true").strip().casefold() in {
            "1", "true", "yes", "on", "да",
        }:
            audio_path = None
            try:
                import edge_tts
                with tempfile.NamedTemporaryFile(
                    prefix="jarvis_reply_", suffix=".mp3", delete=False
                ) as audio_file:
                    audio_path = audio_file.name

                async def save_reply():
                    communicator = edge_tts.Communicate(
                        text,
                        os.getenv("JARVIS_EDGE_VOICE", "ru-RU-DmitryNeural"),
                        rate=os.getenv("JARVIS_EDGE_RATE", "+0%"),
                    )
                    await communicator.save(audio_path)

                asyncio.run(save_reply())
                ffplay = shutil.which("ffplay")
                if ffplay:
                    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    subprocess.run(
                        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, creationflags=flags,
                    )
                    return
                if self.voice_library.mixer_ready:
                    self.voice_library.mixer.music.load(audio_path)
                    self.voice_library.mixer.music.play()
                    self.voice_library.wait_until_finished()
                    return
            except Exception:
                pass
            finally:
                if audio_path:
                    try:
                        Path(audio_path).unlink(missing_ok=True)
                    except OSError:
                        pass
        try:
            # Recreate the Windows SAPI engine for every utterance. On some
            # Windows installations one persistent pyttsx3 engine speaks only
            # the first response and then silently stops.
            speaker = pyttsx3.init()
            requested_voice = os.getenv("JARVIS_VOICE", "").strip().lower()
            voices = speaker.getProperty("voices") or []
            def voice_text(voice):
                return f"{voice.name} {voice.id}".lower()

            russian_markers = (
                "ru-ru", "russian", "русск", "русский", "pavel", "dmitry",
                "дмитр", "павел", "milena", "elena", "наталь",
            )
            english_markers = (
                "english", "en-us", "en-gb", "david", "zira", "hazel",
                "george", "mark",
            )
            selected = None
            # Prefer a Russian male voice even when an old .env contains
            # JARVIS_VOICE=David; David is English and caused English speech.
            selected = next(
                (voice for voice in voices
                 if any(marker in voice_text(voice) for marker in russian_markers)
                 and not any(marker in voice_text(voice) for marker in FEMALE_VOICE_MARKERS)),
                None,
            )
            if selected is None and requested_voice and not any(
                marker in requested_voice for marker in english_markers
            ):
                selected = next(
                    (voice for voice in voices
                     if requested_voice in voice_text(voice)
                     and not any(marker in voice_text(voice)
                                 for marker in FEMALE_VOICE_MARKERS)),
                    None,
                )
            if selected is None:
                male_words = ("pavel", "dmitry", "дмитр", "павел", "russian", "русск")
                selected = next(
                    (voice for voice in voices
                     if any(word in voice_text(voice) for word in male_words)
                     and not any(marker in voice_text(voice)
                                 for marker in FEMALE_VOICE_MARKERS)),
                    None,
                )
            if selected is None:
                # Last resort: use any non-female installed voice.
                selected = next(
                    (voice for voice in voices
                     if not any(marker in voice_text(voice)
                                for marker in FEMALE_VOICE_MARKERS)),
                    None,
                )
            if selected is not None:
                speaker.setProperty("voice", selected.id)
            # SAPI's default is usually around 200; 240 makes the assistant
            # clearly faster without turning the words into a blur.
            speaker.setProperty("rate", int(os.getenv("JARVIS_VOICE_RATE", "240")))
            speaker.setProperty("volume", max(0.0, min(1.0, JARVIS_VOLUME)))
            speaker.say(text)
            speaker.runAndWait()
            speaker.stop()
        except Exception:
            self.on_status("Ответ показан, но голосовой движок не ответил.")

    def say_news(self, text, run_event=None):
        """Read news with Fish Audio, Jarvis SAPI, or Edge-TTS fallback."""
        selected_voice = self.online_voice_id()
        voice_label = "выбранным голосом" if selected_voice else "голосом Джарвиса"
        self.on_reply(f"Читаю новость {voice_label}. Скажите новую команду, чтобы перебить.")
        audio_path = None
        run_event = run_event or self.news_stop_event
        # The generic remote TTS endpoint has the old Jarvis voice.  It must
        # not run when the user selected a specific Fish Audio profile.
        remote_audio = None if selected_voice else self.ai.remote_tts(text)
        if remote_audio:
            try:
                with tempfile.NamedTemporaryFile(
                    prefix="jarvis_remote_", suffix=".mp3", delete=False
                ) as audio_file:
                    audio_path = audio_file.name
                    audio_file.write(remote_audio)
                if run_event.is_set() or self.stop_event.is_set():
                    return
                ffplay = shutil.which("ffplay")
                if ffplay:
                    process = subprocess.Popen(
                        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    with self.news_process_lock:
                        self.news_process = process
                    while process.poll() is None:
                        if run_event.is_set() or self.stop_event.is_set():
                            process.terminate()
                            break
                        time.sleep(0.1)
                    with self.news_process_lock:
                        self.news_process = None
                    return
            except Exception as exc:
                self.on_status(f"Удалённый голос недоступен: {exc}. Использую обычный голос.")
            finally:
                if audio_path:
                    try:
                        Path(audio_path).unlink(missing_ok=True)
                    except OSError:
                        pass
        fish_key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
        fish_reference = selected_voice or os.getenv("FISH_AUDIO_REFERENCE_ID", "").strip()
        fish_failed = False
        if fish_key and fish_reference:
            try:
                self.say_news_fish(text, run_event, fish_key, fish_reference)
                return
            except Exception as exc:
                fish_failed = True
                if not run_event.is_set() and not self.stop_event.is_set():
                    self.on_status(
                        f"Fish Audio недоступен ({exc}). Переключаюсь на русский голос."
                    )
        # The user's requested mode is the normal Jarvis voice.  It is
        # available offline through the configured Windows SAPI voice and
        # does not require a second cloud voice.  Edge-TTS remains available
        # as an explicit fallback for installations that prefer it.
        if not fish_failed and os.getenv("NEWS_USE_JARVIS_VOICE", "true").strip().casefold() in {
            "1", "true", "yes", "on"
        }:
            if not run_event.is_set() and not self.stop_event.is_set():
                self.say(text)
            return
        try:
            import edge_tts

            if run_event.is_set() or self.stop_event.is_set():
                return
            requested_voice = os.getenv("NEWS_VOICE", NEWS_MALE_VOICE).strip()
            # Do not let a stale/custom .env value silently switch news to a
            # female neural voice.  Only known male Russian voices are valid.
            male_edge_voices = {
                "ru-ru-dmitryneural",
                "ru-ru-dmitryneural".casefold(),
                "ru-ru-dmitrymultilingualneural",
            }
            voice = (
                requested_voice
                if requested_voice.casefold() in male_edge_voices
                else NEWS_MALE_VOICE
            )
            rate = os.getenv(
                "NEWS_FALLBACK_RATE" if fish_failed else "NEWS_VOICE_RATE",
                "-10%" if fish_failed else NEWS_VOICE_DEFAULT_RATE,
            ).strip()
            with tempfile.NamedTemporaryFile(
                prefix="jarvis_news_", suffix=".mp3", delete=False
            ) as audio_file:
                audio_path = audio_file.name

            async def save_audio():
                communicator = edge_tts.Communicate(text, voice, rate=rate)
                await communicator.save(audio_path)

            asyncio.run(save_audio())
            ffplay = shutil.which("ffplay")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if ffplay:
                process = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                with self.news_process_lock:
                    self.news_process = process
                while process.poll() is None:
                    if run_event.is_set() or self.stop_event.is_set():
                        process.terminate()
                        break
                    time.sleep(0.1)
                with self.news_process_lock:
                    self.news_process = None
            elif self.voice_library.mixer_ready:
                self.voice_library.mixer.music.load(audio_path)
                self.voice_library.mixer.music.play()
                while (
                    self.voice_library.mixer.music.get_busy()
                    and not self.stop_event.is_set()
                    and not run_event.is_set()
                ):
                    time.sleep(0.1)
                if run_event.is_set():
                    self.voice_library.mixer.music.stop()
            else:
                raise RuntimeError("не найден проигрыватель MP3")
        except Exception as exc:
            if not run_event.is_set():
                self.on_status(
                    f"Нейроголос новостей недоступен: {exc}. Использую обычный голос."
                )
            # This fallback is still generated speech and does not touch the
            # user's pre-recorded voice folders.
            if not run_event.is_set():
                self.say(text)
        finally:
            if audio_path:
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def say_news_fish(self, text, run_event, api_key, reference_id):
        """Generate and play news with the user's Fish Audio voice model."""
        model = os.getenv("FISH_AUDIO_MODEL", "s2.1-pro-free").strip()
        response = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "model": model,
            },
            json={
                "text": text,
                "reference_id": reference_id,
                "format": "mp3",
                "prosody_control": {
                    "speed": float(os.getenv("FISH_AUDIO_SPEED", "0.9")),
                    "normalize_loudness": True,
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("Fish Audio вернул пустой звук")
        with tempfile.NamedTemporaryFile(
            prefix="jarvis_fish_", suffix=".mp3", delete=False
        ) as audio_file:
            audio_path = audio_file.name
            audio_file.write(response.content)
        try:
            if run_event.is_set() or self.stop_event.is_set():
                return
            ffplay = shutil.which("ffplay")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if ffplay:
                process = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                with self.news_process_lock:
                    self.news_process = process
                while process.poll() is None:
                    if run_event.is_set() or self.stop_event.is_set():
                        process.terminate()
                        break
                    time.sleep(0.1)
                with self.news_process_lock:
                    self.news_process = None
            elif self.voice_library.mixer_ready:
                self.voice_library.mixer.music.load(audio_path)
                self.voice_library.mixer.music.play()
                while (
                    self.voice_library.mixer.music.get_busy()
                    and not self.stop_event.is_set()
                    and not run_event.is_set()
                ):
                    time.sleep(0.1)
                if run_event.is_set():
                    self.voice_library.mixer.music.stop()
            else:
                raise RuntimeError("нужен FFmpeg (ffplay) или pygame")
        finally:
            try:
                Path(audio_path).unlink(missing_ok=True)
            except OSError:
                pass

    def stop_news(self):
        """Interrupt fetching/reading without stopping the microphone."""
        self.news_stop_event.set()
        with self.news_process_lock:
            process = self.news_process
            self.news_process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if self.voice_library.mixer_ready:
            try:
                self.voice_library.mixer.music.stop()
            except Exception:
                pass

    def start_news_command(self):
        """Run news away from the microphone loop so commands remain active."""
        self.stop_news()
        run_event = threading.Event()
        self.news_stop_event = run_event
        self.news_thread = threading.Thread(
            target=self.handle_news_command,
            args=(run_event,),
            daemon=True,
        )
        self.news_thread.start()

    def handle_news_command(self, run_event=None):
        run_event = run_event or self.news_stop_event
        url = NEWS_FALLBACK_URL
        try:
            items = select_news_stories(fetch_news(limit=9), limit=3)
            if run_event.is_set() or self.stop_event.is_set():
                return
            if not items:
                items = [{"title": "Новость NASA", "link": url, "description": ""}]
            # Open and read one story at a time. This avoids guessing where
            # the browser inserted a batch of tabs and lets speech begin while
            # the next story is still unopened.
            for index, item in enumerate(items, 1):
                if run_event.is_set() or self.stop_event.is_set():
                    return
                article_url = item.get("link") or url
                article_text = item.get("description", "")
                try:
                    opened = bool(webbrowser.open_new_tab(article_url))
                except webbrowser.Error:
                    opened = open_url(article_url)
                time.sleep(0.15)
                if run_event.is_set() or self.stop_event.is_set():
                    return
                # A new-tab request normally activates exactly this tab.
                # Refocus the browser so the next keyboard action cannot land
                # in the Tkinter window.
                if os.name == "nt":
                    user32 = _windows_input()
                    if user32:
                        _focus_player_window(
                            user32,
                            ("chrome", "edge", "firefox", "яндекс браузер", "браузер"),
                            ("chrome.exe", "msedge.exe", "firefox.exe", "browser.exe"),
                        )
                self.on_status(
                    f"Новость {index} из {len(items)}. "
                    + ("Открыта статья, читаю пересказ…"
                       if opened else "Читаю пересказ в фоне…")
                )
                summary = self.ai.summarize_news(item["title"], article_text)
                self.say_news(
                    article_digest(item, summary),
                    run_event,
                )
        except Exception as exc:
            if run_event.is_set() or self.stop_event.is_set():
                return
            self.on_status(f"Не удалось получить новость: {exc}")
            try:
                article_url, article_text = fetch_article_text(NEWS_FALLBACK_URL)
                if run_event.is_set() or self.stop_event.is_set():
                    return
                opened = open_url(article_url or NEWS_FALLBACK_URL)
                self.on_status("Открыта резервная статья NASA. Читаю основной текст…")
                self.say_news(article_digest(
                    {"title": "Фантастическая космическая операция NASA закончилась провалом"},
                    article_text,
                ), run_event)
            except Exception:
                if not run_event.is_set():
                    self.on_reply("Текст новости сейчас недоступен для озвучивания.")

    def handle_development_command(self, command):
        text = command.casefold()
        if "давай поработаем" in text and "нов" in text and "проект" in text:
            try:
                root, source, language = create_project(command)
                self.active_project = root
                self.active_source = source
                self.active_language = language
                if open_in_visual_studio(root):
                    return jarvis_ack(
                        f"создала новый проект {root.name} и открыла его в Visual Studio",
                        1,
                    )
                return f"Проект создан в {root}, но Visual Studio не удалось открыть."
            except OSError as exc:
                return f"Не удалось создать проект: {exc}"

        if any(word in text for word in ("напиши", "напиcи", "создай код", "сделай программу")):
            try:
                if self.active_project is None:
                    root, source, language = create_project(command)
                    self.active_project = root
                    self.active_source = source
                    self.active_language = language
                code = self.ai.generate_code(command, self.active_language or project_language(command))
                self.active_source.write_text(code + "\n", encoding="utf-8")
                open_in_visual_studio(self.active_source)
                return jarvis_ack(
                    f"написала код в {self.active_source.name} и открыла его в Visual Studio",
                    2,
                )
            except OSError as exc:
                return f"Не удалось записать код в проект: {exc}"
        return None

    def start_tik_tok_protocol(self, command):
        """Schedule the Tik Tok protocol and open its short confirmation window."""
        delay = parse_protocol_delay(command)
        if delay is None:
            return "Назовите задержку, например: через 10 секунд или через час."

        self.protocol_confirmation_until = 0.0
        self.on_status(f"Протокол Тик Ток запущен. Пауза через {delay} сек.")

        def run_protocol():
            if self.stop_event.wait(delay):
                return
            if yandex_music_action("play_pause"):
                self.on_status("Протокол Тик Ток: поставила музыку на паузу.")
            else:
                self.on_status("Протокол Тик Ток: не удалось отправить Fn+F7.")
            # Mark the protocol as busy while the prompt is playing, but do
            # not start the answer deadline yet.  This prevents the
            # microphone from treating Jarvis's own prompt as the user's
            # answer and, importantly, makes the deadline start after an
            # asynchronous pygame playback has actually finished.
            self.protocol_confirmation_active.set()
            self.protocol_confirmation_until = 0.0
            self.say("Время вышло.", "prot_otd")
            self.voice_library.wait_until_finished()
            if not self.stop_event.is_set():
                # Ten seconds is still a short confirmation window, but is
                # much more tolerant of Google recognition/network latency.
                self.protocol_confirmation_until = time.monotonic() + 10
                self.on_status("Протокол Тик Ток: ответьте «да» или «нет» в течение 10 секунд.")

        self.protocol_timer = threading.Thread(target=run_protocol, daemon=True)
        self.protocol_timer.start()
        return f"Протокол Тик Ток запущен. Поставлю паузу через {delay} секунд."

    def arm_auto_tik_tok(self, command):
        """Arm one automatic interruption for a newly started media session."""
        if not self.auto_tik_tok_enabled or not media_command(command):
            return
        self.auto_protocol_cancel.set()
        self.auto_protocol_cancel = threading.Event()
        cancel = self.auto_protocol_cancel

        def wait_and_interrupt():
            # The first hour is never interrupted.  The second hour gets one
            # random interruption, so the behaviour does not feel mechanical.
            if cancel.wait(3600):
                return
            if cancel.wait(random.uniform(0, 3600)):
                return
            if self.stop_event.is_set() or self.protocol_confirmation_active.is_set():
                return
            self.on_status("Автоматический протокол Тик Ток: ставлю медиа на паузу.")
            self.run_tik_tok_pause_prompt()

        self.auto_protocol_timer = threading.Thread(
            target=wait_and_interrupt, daemon=True
        )
        self.auto_protocol_timer.start()
        self.on_status("Авто-Тик Ток включён: проверка через 1–2 часа.")

    def cancel_auto_tik_tok(self):
        """Stop monitoring when the last command is no longer media."""
        self.auto_protocol_cancel.set()

    def run_tik_tok_pause_prompt(self):
        """Pause media and ask the same yes/no question as manual Tik Tok."""
        if not yandex_music_action("play_pause"):
            self.on_status("Авто-Тик Ток: не удалось отправить Fn+F7.")
        self.protocol_confirmation_active.set()
        self.protocol_confirmation_until = 0.0
        self.say("Время вышло.", "prot_otd")
        self.voice_library.wait_until_finished()
        if not self.stop_event.is_set():
            self.protocol_confirmation_until = time.monotonic() + 10
            self.on_status("Протокол Тик Ток: ответьте «да» или «нет» в течение 10 секунд.")

    def handle_tik_tok_answer(self, answer):
        """Handle the only two answers accepted by the protocol window."""
        self.protocol_confirmation_until = 0.0
        self.protocol_confirmation_active.clear()
        if answer == "yes":
            self.say("Запускаю первый проект.", "prot_y")
            _, reply = open_local_app("давай поработаем")
            if "не найден" in reply.casefold() or "не удалось" in reply.casefold():
                self.say(reply, "work")
            return
        self.say("Поняла, оставляю музыку на паузе.", "prot_n")
        if yandex_music_action("play_pause"):
            self.on_status("Протокол Тик Ток: повторно нажала Fn+F7.")
        else:
            self.on_status("Протокол Тик Ток: не удалось повторно отправить Fn+F7.")

    def _loop(self):
        try:
            with sr.Microphone(device_index=self.device_index) as source:
                self.on_status("Слушаю микрофон. Скажите «Джарвис» и команду.")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                while not self.stop_event.is_set():
                    try:
                        listen_started = time.monotonic()
                        audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=8)
                        heard = self.recognizer.recognize_google(
                            audio, language="ru-RU"
                        ).strip()
                        wake_found, wake_command = extract_wake_command(heard)
                        # Recognition is a network operation.  If Google
                        # returns a result a little after the deadline, it
                        # should still count when the user started speaking
                        # while the confirmation window was open.
                        protocol_window = (
                            self.protocol_confirmation_active.is_set()
                            and self.protocol_confirmation_until > 0.0
                            and (
                                time.monotonic() < self.protocol_confirmation_until
                                or listen_started < self.protocol_confirmation_until
                            )
                        )
                        if not wake_found:
                            if protocol_window:
                                answer = protocol_answer(heard)
                                if answer:
                                    self.on_status(f"Протокол Тик Ток: услышала «{heard}».")
                                    self.handle_tik_tok_answer(answer)
                                else:
                                    self.on_status(
                                        f"Протокол Тик Ток: ответ «{heard}» не распознан."
                                    )
                                continue
                            # Every user command must start with the exact
                            # keyword configured in the launcher.  Do not
                            # recognize the old built-in name or free-form
                            # conversational phrases here.
                            continue
                        else:
                            self.protocol_confirmation_until = 0.0
                            self.protocol_confirmation_active.clear()
                            command = wake_command
                        if not command:
                            if is_gratitude_phrase(heard):
                                self.say("Всегда к вашим услугам, сэр.", "blagodar")
                            else:
                                self.say("Я слушаю.")
                            continue
                        # Any new command interrupts a running news article.
                        self.stop_news()
                        self.on_status(f"Команда: {command}")
                        if not media_command(command):
                            self.cancel_auto_tik_tok()
                        composition_request = compose_command(command)
                        if composition_request is not None:
                            if not composition_request:
                                self.say("Уточните, что именно составить.")
                                continue
                            self.on_status("ИИ составляет текст. Подождите немного.")
                            composed = self.ai.compose_text(composition_request)
                            if not composed:
                                self.say(
                                    "Не удалось составить текст. Проверьте AI API."
                                )
                                continue
                            saved, saved_path = save_composition_to_notepad(composed)
                            if saved:
                                self.say(
                                    f"Готово. Я составила текст и открыла его в Блокноте: "
                                    f"{saved_path.name}",
                                    None,
                                )
                            else:
                                self.say("Текст составлен, но открыть Блокнот не удалось.")
                            continue
                        if drawing_command(command):
                            drawing_request = re.sub(
                                r"^\s*(?:нарисуй|нарисовать|рисуй|порисуй)\b",
                                "",
                                command,
                                flags=re.I,
                            ).strip(" ,.!?-") or "абстрактную картинку"
                            self.on_status("ИИ готовит рисунок для Paint.")
                            svg = self.ai.draw_svg(drawing_request)
                            if svg:
                                drawn, drawing_path = save_svg_and_open_paint(svg)
                                drawing_reply = (
                                    "Готово, я нарисовала это в Paint."
                                    if drawn else "Рисунок создан, но Paint не удалось открыть."
                                )
                            else:
                                _, drawing_reply = open_paint()
                            self.say(drawing_reply, None)
                            continue
                        if is_tik_tok_protocol(command):
                            protocol_reply = self.start_tik_tok_protocol(command)
                            self.say(protocol_reply)
                            continue
                        category = voice_category(command)
                        development_reply = self.handle_development_command(command)
                        if development_reply:
                            self.say(development_reply, "work")
                            continue
                        if news_command(command):
                            self.start_news_command()
                            continue
                        input_reply = perform_input_action(command)
                        if input_reply:
                            self.say(self.ai.action_reply(command, input_reply), category)
                            continue
                        if any(x in command.lower() for x in ("остановись", "выключись", "стоп")):
                            self.say(jarvis_ack("останавливаюсь", 1), category)
                            self.stop_event.set()
                            continue
                        elif any(word in command.lower() for word in ("закрой", "закрыть", "выключи", "выключить")):
                            app_name = command.lower()
                            for word in ("закрой", "закрыть", "выключи", "выключить", "приложение"):
                                app_name = app_name.replace(word, "").strip()
                            _, reply = close_local_app(app_name)
                            self.say(self.ai.action_reply(command, reply), category)
                        elif any(word in command.lower() for word in (
                            "открой", "запусти", "запустить", "открыть",
                            "включи", "найди", "поищи", "поиск",
                            "давай поработаем",
                        )):
                            app_name = command.lower()
                            if not any(word in app_name for word in ("включи", "найди", "поищи", "поиск")):
                                for word in ("открой", "запусти", "запустить", "открыть", "приложение"):
                                    app_name = app_name.replace(word, "").strip()
                            _, reply = open_local_app(app_name)
                            if is_general_web_search(command):
                                # Search confirmation comes from the user's
                                # saved blagodar recordings. The found title
                                # and snippet are a separate generated speech
                                # response, voiced through Fish Audio.
                                if not self.online_voice_id() and not self.play_recorded("blagodar"):
                                    self.say("Нашла результат, сэр.")
                                # The technical browser confirmation is only
                                # context for the AI and is never spoken.
                                # The user hears one natural answer to the
                                # original query after the saved phrase.
                                self.say(
                                    self.ai.search_reply(command, reply),
                                    None,
                                )
                            else:
                                self.say(self.ai.action_reply(command, reply), category)
                            if media_command(command):
                                self.arm_auto_tik_tok(command)
                        else:
                            # Never use a prerecorded confirmation for a
                            # conversational answer.
                            self.say(self.ai.reply(command), None)
                    except sr.WaitTimeoutError:
                        continue
                    except sr.UnknownValueError:
                        self.on_status("Не удалось разобрать речь. Жду следующую команду.")
                    except sr.RequestError:
                        self.on_status("Сервис распознавания речи недоступен. Проверьте интернет.")
                        time.sleep(2)
                    except Exception as exc:
                        self.on_status(f"Ошибка команды: {exc}. Продолжаю слушать.")
                        time.sleep(0.5)
        except Exception as exc:
            self.on_status(f"Не удалось открыть микрофон: {exc}")


class App(tk.Tk):
    """Glass-style launcher shell around the existing Jarvis voice worker."""

    BG = "#070d1b"
    PANEL = "#0b1528"
    PANEL_2 = "#132746"
    TEXT = "#f1f5ff"
    MUTED = "#8290ad"
    BLUE = "#4d8dff"
    CYAN = "#62d7ff"
    GREEN = "#5ee5a5"

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x680")
        self.minsize(900, 590)
        self.configure(bg=self.BG)
        self.db = Database()
        self.voice = None
        self.current_user = None
        self.status = tk.StringVar(value="Ассистент готов к запуску")
        self.history = []
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure("TButton", font=("Segoe UI", 10), padding=(13, 9),
                             background=self.PANEL_2, foreground=self.TEXT, borderwidth=0)
        self.style.map("TButton", background=[("active", "#21365b")])
        self.style.configure("Accent.TButton", background=self.BLUE, foreground="white",
                             font=("Segoe UI", 10, "bold"), padding=(18, 11))
        self.style.map("Accent.TButton", background=[("active", "#6aa4ff")])
        self.style.configure("Stop.TButton", background="#26324b", foreground="#ff9da8",
                             padding=(18, 11))
        self.style.configure("TNotebook", background=self.PANEL, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=self.PANEL, foreground="#7f90b3",
                             borderwidth=0, padding=(18, 10), font=("Segoe UI", 9, "bold"))
        self.style.map("TNotebook.Tab", background=[("selected", "#1a3159")],
                       foreground=[("selected", self.CYAN)])
        self.show_auth()

    def clear(self):
        if getattr(self, "animation_job", None):
            self.after_cancel(self.animation_job)
            self.animation_job = None
        if getattr(self, "waves_job", None):
            self.after_cancel(self.waves_job)
            self.waves_job = None
        for child in self.winfo_children():
            child.destroy()

    def label(self, parent, text, color=None, size=10, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", self.BG),
                        fg=color or self.TEXT, font=("Segoe UI", size, "bold" if bold else "normal"),
                        **kwargs)

    def card(self, parent, **kwargs):
        return tk.Frame(parent, bg=kwargs.pop("bg", self.PANEL),
                        highlightthickness=1, highlightbackground=kwargs.pop("border", "#1d2b47"),
                        **kwargs)

    def show_auth(self):
        self.clear()
        outer = tk.Frame(self, bg=self.BG)
        outer.pack(fill="both", expand=True)
        hero = tk.Frame(outer, bg="#0a1428", width=430)
        hero.pack(side="left", fill="both")
        hero.pack_propagate(False)
        self.label(hero, "JARVIS", self.CYAN, 30, True, bg="#0a1428").pack(anchor="w", padx=52, pady=(90, 3))
        self.label(hero, "PERSONAL AI SYSTEM", "#6782b8", 9, True, bg="#0a1428").pack(anchor="w", padx=55)
        self.label(hero, "Ваш умный центр управления компьютером.", "#dce7ff", 17, True,
                   bg="#0a1428", wraplength=300, justify="left").pack(anchor="w", padx=52, pady=(75, 14))
        self.label(hero, "Голосовые команды, приложения, музыка,\nновости и разработка — в одном окне.",
                   self.MUTED, 11, bg="#0a1428", justify="left").pack(anchor="w", padx=52)
        self.label(hero, "●  LOCAL CONTROL  /  ONLINE INTELLIGENCE", "#4b6a9d", 8, True,
                   bg="#0a1428").pack(anchor="w", padx=52, side="bottom", pady=35)

        body = tk.Frame(outer, bg=self.BG)
        body.pack(side="right", fill="both", expand=True)
        self.label(body, "Добро пожаловать", self.TEXT, 24, True).pack(anchor="w", padx=62, pady=(112, 5))
        self.label(body, "Войдите, чтобы открыть командный центр", self.MUTED, 11).pack(anchor="w", padx=64, pady=(0, 30))
        form = self.card(body, bg=self.PANEL, border="#1b2d4d")
        form.pack(anchor="w", padx=60, fill="x", ipadx=18, ipady=20)
        tabs = tk.Frame(form, bg=self.PANEL)
        tabs.pack(fill="x", padx=20, pady=(4, 10))
        login = tk.Frame(form, bg=self.PANEL)
        register = tk.Frame(form, bg=self.PANEL)
        self.auth_views = {"login": login, "register": register}
        self.auth_tab_buttons = {}
        for key, caption in (("login", "Войти"), ("register", "Регистрация")):
            button = tk.Button(tabs, text=caption, relief="flat", bd=0, cursor="hand2",
                               font=("Segoe UI", 9, "bold"), padx=16, pady=7,
                               command=lambda value=key: self.switch_auth(value))
            button.pack(side="left", padx=(0, 6))
            self.auth_tab_buttons[key] = button
        self.auth_form(login, False)
        self.auth_form(register, True)
        self.switch_auth("login")

    def switch_auth(self, key):
        for view in self.auth_views.values():
            view.pack_forget()
        self.auth_views[key].pack(fill="both", expand=True, padx=5)
        for name, button in self.auth_tab_buttons.items():
            button.configure(bg="#1a3159" if name == key else self.PANEL,
                             fg=self.CYAN if name == key else "#7f90b3",
                             activebackground="#1a3159")

    def auth_form(self, parent, is_register):
        entries = {}
        for row, (caption, key) in enumerate((("Email", "email"), ("Пароль", "password"))):
            self.label(parent, caption.upper(), "#93a4c7", 8, True, bg=self.PANEL).grid(
                row=row * 2, column=0, sticky="w", padx=20, pady=(12, 4))
            entry = tk.Entry(parent, show="*" if key == "password" else "", bg="#1a2944",
                             fg=self.TEXT, insertbackground=self.CYAN, relief="flat",
                             font=("Segoe UI", 11), width=34)
            entry.grid(row=row * 2 + 1, column=0, padx=20, pady=(0, 8), ipady=8, sticky="ew")
            entries[key] = entry
        parent.columnconfigure(0, weight=1)
        ttk.Button(parent, text="Создать аккаунт" if is_register else "Войти в систему",
                   style="Accent.TButton",
                   command=lambda: self.auth_submit(entries, is_register)).grid(
                       row=4, column=0, padx=20, pady=(14, 12), sticky="ew")

    def auth_submit(self, entries, is_register):
        email, password = entries["email"].get().strip(), entries["password"].get()
        if "@" not in email or len(password) < 4:
            messagebox.showwarning("Проверьте данные", "Введите корректный email и пароль минимум из 4 символов.")
            return
        if is_register:
            ok, text = self.db.register(email, password)
            messagebox.showinfo("JARVIS", text)
            if ok:
                self.current_user = self.db.login(email, password)
                self.show_dashboard()
        else:
            self.current_user = self.db.login(email, password)
            if self.current_user:
                self.show_dashboard()
            else:
                messagebox.showerror("Ошибка входа", "Неверный email или пароль.")

    def show_dashboard(self):
        self.clear()
        self.status.set("Ассистент готов к запуску")
        shell = tk.Frame(self, bg=self.BG)
        shell.pack(fill="both", expand=True)
        side = tk.Frame(shell, bg="#0a1428", width=205)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.label(side, "◈  JARVIS", self.CYAN, 18, True, bg="#0a1428").pack(anchor="w", padx=22, pady=(27, 48))
        for icon, title, active in (("⌂", "Главное окно", True), ("◌", "Команды", False),
                                    ("▣", "Новости", False), ("✦", "Интеграции", False),
                                    ("⚙", "Настройки", False)):
            row = tk.Frame(side, bg="#19315a" if active else "#0a1428")
            row.pack(fill="x", padx=12, pady=3, ipady=8)
            item = tk.Button(row, text=f"{icon}   {title}", relief="flat", bd=0,
                             anchor="w", cursor="hand2", padx=13,
                             bg="#19315a" if active else "#0a1428",
                             fg=self.TEXT if active else "#8290ad",
                             activebackground="#19315a", activeforeground=self.TEXT,
                             font=("Segoe UI", 10, "bold" if active else "normal"))
            item.pack(fill="x")
            if title == "Настройки":
                item.configure(command=self.show_settings)
        self.label(side, "СИСТЕМА", "#45638f", 8, True, bg="#0a1428").pack(anchor="w", padx=25, pady=(34, 14))
        self.label(side, "●  Система активна", self.GREEN, 9, bg="#0a1428").pack(anchor="w", padx=25)
        self.label(side, f"v{APP_VERSION}", "#405779", 8, bg="#0a1428").pack(anchor="w", padx=25, side="bottom", pady=25)
        main = tk.Frame(shell, bg=self.BG)
        main.pack(side="right", fill="both", expand=True)
        self.wave_canvas = tk.Canvas(main, bg=self.BG, highlightthickness=0)
        self.wave_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.wave_phase = 0
        self.draw_waves()
        header = tk.Frame(main, bg=self.BG)
        header.pack(fill="x", padx=34, pady=(25, 18))
        self.label(header, "Основное окно", self.TEXT, 22, True).pack(side="left")
        self.label(header, f"{self.current_user[1]}  ︱  аккаунт пользователя", self.MUTED, 9).pack(side="right", pady=9)
        content = tk.Frame(main, bg=self.BG)
        content.pack(fill="both", expand=True, padx=34)
        right = tk.Frame(content, bg=self.BG, width=390)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)
        left = tk.Frame(content, bg=self.BG)
        left.pack(side="right", fill="both", expand=True, padx=(13, 0))
        history = self.card(left, bg="#0b1528", border="#172b4a")
        history.pack(fill="both", expand=True)
        self.label(history, "ИСТОРИЯ КОМАНД", "#7892bf", 9, True, bg=self.PANEL).pack(anchor="w", padx=22, pady=(19, 12))
        self.history_box = tk.Text(history, bg=self.PANEL, fg="#c9d6ef", insertbackground=self.CYAN,
                                   relief="flat", font=("Segoe UI", 10), padx=22, pady=4, wrap="word",
                                   state="disabled", height=12)
        self.history_box.pack(fill="both", expand=True, padx=2, pady=(0, 12))
        self.add_history("Добро пожаловать в командный центр.")
        self.add_history("Скажите «Джарвис» и назовите команду.")
        power_card = self.card(right, bg="#0b1528", border="#1b3864")
        power_card.pack(fill="x", pady=(0, 13), ipady=7)
        self.label(power_card, "ЦЕНТР УПРАВЛЕНИЯ", "#7892bf", 9, True,
                   bg="#0b1528").pack(anchor="w", padx=18, pady=(16, 0))
        self.label(power_card, "ГОЛОСОВОЙ МОДУЛЬ", "#526b96", 8, True,
                   bg="#0b1528").pack(anchor="w", padx=18, pady=(3, 2))
        self.power_canvas = tk.Canvas(power_card, width=320, height=280, bg="#0b1528",
                                      highlightthickness=0, cursor="hand2")
        self.power_canvas.pack(pady=(0, 3))
        self.power_canvas.bind("<Button-1>", lambda event: self.toggle_voice())
        self.power_running = False
        self.power_phase = 0
        self.draw_power_button()
        self.label(power_card, "Нажмите, чтобы запустить Джарвиса", "#657da6", 9,
                   bg="#0b1528").pack(pady=(0, 15))
        status_card = self.card(right, bg="#0b1528", border="#172b4a")
        status_card.pack(fill="x", pady=(0, 13))
        self.label(status_card, "ПАНЕЛЬ УПРАВЛЕНИЯ", "#7892bf", 9, True, bg=self.PANEL).pack(anchor="w", padx=18, pady=(18, 16))
        self.label(status_card, "СТАТУС", "#667b9f", 8, True, bg=self.PANEL).pack(anchor="w", padx=18)
        self.live_status = self.label(status_card, "●  Готов к работе", self.GREEN, 11, True, bg=self.PANEL)
        self.live_status.pack(anchor="w", padx=18, pady=(5, 18))
        self.label(status_card, "МИКРОФОН", "#667b9f", 8, True, bg=self.PANEL).pack(anchor="w", padx=18)
        names = microphone_names()
        self.microphones = names
        values = ["По умолчанию"] + [f"{i}: {name}" for i, name in enumerate(names)]
        self.mic_choice = tk.StringVar(value=values[0])
        self.mic_combo = tk.OptionMenu(status_card, self.mic_choice, *values)
        self.mic_combo.configure(bg="#1a2944", fg="#dce7ff", activebackground="#29456f",
                                 activeforeground="white", relief="flat", bd=0,
                                 highlightthickness=0, anchor="w", font=("Segoe UI", 9))
        self.mic_combo["menu"].configure(bg="#14213a", fg="#dce7ff",
                                         activebackground="#315a91", activeforeground="white",
                                         relief="flat", bd=0, font=("Segoe UI", 9))
        self.mic_combo.pack(fill="x", padx=18, pady=(5, 8), ipady=4)
        if not names:
            self.mic_choice.set("Микрофоны не найдены")
        ttk.Button(status_card, text="Проверить микрофон", command=self.test_microphone).pack(fill="x", padx=18, pady=(0, 18))
        plan = self.card(right, bg="#0b1528", border="#172b4a")
        plan.pack(fill="x")
        self.label(plan, "ДОСТУП", "#7892bf", 8, True, bg="#0b1528").pack(anchor="w", padx=18, pady=(16, 6))
        self.label(plan, "Тестовый режим", self.CYAN, 13, True, bg="#0b1528").pack(anchor="w", padx=18)
        self.label(plan, "Бесплатный день активен", self.MUTED, 9, bg="#0b1528").pack(anchor="w", padx=18, pady=(3, 13))
        ttk.Button(plan, text="Варианты подписки", command=self.subscription).pack(fill="x", padx=18, pady=(0, 16))
        footer = tk.Frame(main, bg=self.BG)
        footer.pack(fill="x", padx=34, pady=(15, 19))
        self.label(footer, "Джарвис слушает только команды, начинающиеся с имени ассистента.", "#526887", 8).pack(side="left")
        ttk.Button(footer, text="Выйти", command=self.logout).pack(side="right")

    def add_history(self, text):
        if not hasattr(self, "history_box"):
            return
        self.history_box.configure(state="normal")
        self.history_box.insert("end", f"  {text}\n\n")
        self.history_box.see("end")
        self.history_box.configure(state="disabled")

    def auth_form_old_removed(self, *args):
        pass

    def show_settings(self):
        self.clear()
        page = tk.Frame(self, bg=self.BG)
        page.pack(fill="both", expand=True, padx=70, pady=55)
        self.label(page, "Настройки", self.TEXT, 26, True).pack(anchor="w")
        self.label(page, "Настройте поведение и звук Джарвиса", self.MUTED, 11).pack(anchor="w", pady=(5, 30))
        sound = self.card(page, bg=self.PANEL, border="#203b68")
        sound.pack(fill="x", ipadx=20, ipady=14)
        self.label(sound, "ЗВУКОВОЙ ПРОФИЛЬ", "#7892bf", 9, True, bg=self.PANEL).pack(
            anchor="w", padx=24, pady=(18, 8))
        self.label(sound, "Громкость голоса Джарвиса", self.TEXT, 12, True, bg=self.PANEL).pack(
            anchor="w", padx=24)
        volume_value = tk.StringVar(value=f"{round(JARVIS_VOLUME * 100)}%")
        volume_row = tk.Frame(sound, bg=self.PANEL)
        volume_row.pack(fill="x", padx=20, pady=(12, 18))
        volume = tk.Scale(volume_row, from_=0, to=100, orient="horizontal",
                          showvalue=False, bg=self.PANEL, fg=self.CYAN,
                          troughcolor="#1a2944", activebackground=self.CYAN,
                          highlightthickness=0, bd=0, relief="flat",
                          command=lambda value: self.set_jarvis_volume(value, volume_value))
        volume.set(int(JARVIS_VOLUME * 100))
        volume.pack(side="left", fill="x", expand=True)
        tk.Label(volume_row, textvariable=volume_value, bg=self.PANEL, fg=self.CYAN,
                 font=("Segoe UI", 10, "bold"), width=5).pack(side="right", padx=(15, 0))
        history = self.card(page, bg=self.PANEL, border="#203b68")
        history.pack(fill="x", pady=16, ipadx=20, ipady=14)
        self.label(history, "ИСТОРИЯ", "#7892bf", 9, True, bg=self.PANEL).pack(
            anchor="w", padx=24, pady=(18, 5))
        self.label(history, "История команд хранится только в текущем запуске.", self.MUTED,
                   10, bg=self.PANEL).pack(anchor="w", padx=24, pady=(0, 12))
        ttk.Button(history, text="Очистить историю", command=self.clear_history).pack(
            anchor="w", padx=24, pady=(0, 15))
        ttk.Button(page, text="←  Вернуться в командный центр",
                   style="Accent.TButton", command=self.show_dashboard).pack(anchor="w", pady=18)

    def set_jarvis_volume(self, value, label):
        global JARVIS_VOLUME
        JARVIS_VOLUME = max(0.0, min(1.0, float(value) / 100))
        label.set(f"{round(JARVIS_VOLUME * 100)}%")

    def clear_history(self):
        self.history = []
        self.show_dashboard()

    def draw_power_button(self):
        if not hasattr(self, "power_canvas"):
            return
        canvas = self.power_canvas
        canvas.delete("all")
        cx, cy = 160, 135
        glow = "#315ca8" if self.power_running else "#17294a"
        outer = "#6d9cff" if self.power_running else "#30466e"
        canvas.create_oval(cx - 112, cy - 112, cx + 112, cy + 112,
                           outline="#192a4c", width=3)
        canvas.create_oval(cx - 97, cy - 97, cx + 97, cy + 97,
                           outline=glow, width=12)
        canvas.create_oval(cx - 78, cy - 78, cx + 78, cy + 78,
                           outline=outer, width=3)
        canvas.create_oval(cx - 64, cy - 64, cx + 64, cy + 64,
                           fill="#101e38", outline="#334d7f", width=2)
        color = self.CYAN if self.power_running else "#778ab3"
        canvas.create_line(cx, cy - 38, cx, cy + 6, fill=color, width=7,
                           capstyle="round")
        canvas.create_arc(cx - 34, cy - 25, cx + 34, cy + 43,
                          start=215 + self.power_phase, extent=110,
                          style="arc", outline=color, width=6)
        if self.power_running:
            orbit_color = "#66bfff" if self.power_phase % 24 < 12 else "#496fca"
            for radius, offset in ((103, 0), (115, 120), (122, 240)):
                angle = math.radians(self.power_phase * 2.5 + offset)
                dot_x = cx + math.cos(angle) * radius
                dot_y = cy + math.sin(angle) * radius
                canvas.create_oval(dot_x - 3, dot_y - 3, dot_x + 3, dot_y + 3,
                                   fill=orbit_color, outline="")

    def animate_power(self):
        if not getattr(self, "power_running", False):
            return
        self.power_phase = (self.power_phase + 8) % 360
        self.draw_power_button()
        self.animation_job = self.after(45, self.animate_power)

    def draw_waves(self):
        if not hasattr(self, "wave_canvas"):
            return
        canvas = self.wave_canvas
        width = max(canvas.winfo_width(), 900)
        height = max(canvas.winfo_height(), 600)
        canvas.delete("all")
        top_color = (4, 7, 15)
        bottom_color = (12, 27, 54)
        for y in range(0, height + 6, 6):
            ratio = y / max(height, 1)
            color = "#{:02x}{:02x}{:02x}".format(
                int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio),
                int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio),
                int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio),
            )
            canvas.create_rectangle(0, y, width, y + 6, fill=color,
                                    outline="", tags="background")
        colors = ("#071d4c", "#082b69", "#0b408f")
        for layer, color in enumerate(colors):
            base = height * (0.78 + layer * 0.055)
            amplitude = 20 + layer * 9
            points = [0, height]
            for x in range(0, width + 18, 18):
                y = base + math.sin(x / 145 + self.wave_phase / 27 + layer * 1.7) * amplitude
                y += math.sin(x / 73 - self.wave_phase / 40) * amplitude * 0.32
                points.extend((x, y))
            points.extend((width, height, 0, height))
            canvas.create_polygon(points, fill=color, outline="", tags="waves")
        if getattr(self, "wave_running", True):
            self.wave_phase = (self.wave_phase + 2) % 360
            self.waves_job = self.after(55, self.draw_waves)

    def toggle_voice(self):
        if self.power_running:
            self.stop_voice()
        else:
            self.start_voice()

    def start_voice(self):
        choice = self.mic_choice.get()
        device_index = None
        if choice and choice != "По умолчанию" and ":" in choice:
            try:
                device_index = int(choice.split(":", 1)[0])
            except ValueError:
                device_index = None
        self.voice = VoiceWorker(self.set_status, self.set_reply, device_index)
        self.voice.start()
        if hasattr(self, "start_btn"):
            self.start_btn.configure(state="disabled")
        if hasattr(self, "stop_btn"):
            self.stop_btn.configure(state="normal")
        self.power_running = True
        self.draw_power_button()
        if not getattr(self, "animation_job", None):
            self.animation_job = self.after(45, self.animate_power)
        self.live_status.configure(text="●  Слушаю вас", fg=self.CYAN)
        self.add_history("Ассистент запущен — микрофон активен.")

    def test_microphone(self):
        choice = self.mic_choice.get()
        device_index = None
        if choice and choice != "По умолчанию" and ":" in choice:
            try:
                device_index = int(choice.split(":", 1)[0])
            except ValueError:
                pass
        threading.Thread(target=self._test_microphone_worker,
                         args=(device_index,), daemon=True).start()

    def _test_microphone_worker(self, device_index):
        try:
            recognizer = sr.Recognizer()
            with sr.Microphone(device_index=device_index) as source:
                self.set_status("Проверка микрофона: скажите что-нибудь…")
                audio = recognizer.listen(source, timeout=4, phrase_time_limit=4)
            text = recognizer.recognize_google(audio, language="ru-RU")
            self.set_status(f"Микрофон работает: «{text}»")
        except sr.WaitTimeoutError:
            self.set_status("Микрофон открыт, но звук не найден. Проверьте разрешение Windows.")
        except Exception as exc:
            self.set_status(f"Ошибка микрофона: {exc}")

    def stop_voice(self):
        if self.voice:
            self.voice.stop()
        if hasattr(self, "start_btn"):
            self.start_btn.configure(state="normal")
        if hasattr(self, "stop_btn"):
            self.stop_btn.configure(state="disabled")
        self.power_running = False
        if getattr(self, "animation_job", None):
            self.after_cancel(self.animation_job)
            self.animation_job = None
        self.draw_power_button()
        if hasattr(self, "live_status"):
            self.live_status.configure(text="●  Готов к работе", fg=self.GREEN)

    def set_status(self, text):
        self.after(0, lambda: self.status.set(text))
        self.after(0, lambda: self._show_runtime_status(text))

    def set_reply(self, text):
        self.after(0, lambda: self.status.set(f"Джарвис: {text}"))
        self.after(0, lambda: self.add_history(f"Джарвис: {text}"))

    def _show_runtime_status(self, text):
        if hasattr(self, "live_status"):
            self.live_status.configure(text="●  " + ("Слушаю вас" if "слуш" in text.casefold() else "Выполняю команду"),
                                       fg=self.CYAN)
            if text.startswith("Команда:"):
                self.add_history(text)

    def subscription(self):
        messagebox.showinfo("Подписка", "Оплата будет добавлена позже. Сейчас ассистент работает в тестовом режиме без ограничений.")

    def logout(self):
        self.stop_voice()
        self.current_user = None
        self.show_auth()


def main():
    if "--headless" in sys.argv:
        selected_microphone = os.getenv("JARVIS_MICROPHONE_INDEX", "").strip()
        try:
            selected_microphone = int(selected_microphone) if selected_microphone else None
        except ValueError:
            selected_microphone = None
        worker = VoiceWorker(
            lambda text: print(f"[JARVIS] {text}", flush=True),
            lambda text: print(f"[JARVIS] {text}", flush=True),
            selected_microphone,
        )
        worker.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
    else:
        App().mainloop()


if __name__ == "__main__":
    main()