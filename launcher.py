import math
import base64
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import zipfile

import requests
from PySide6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, Qt, QTimer, Signal, QSettings,
)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QCheckBox, QColorDialog, QComboBox, QDialog, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)


# Set this to "owner/repository" before building, or pass JARVIS_GITHUB_REPO
# in the user's environment. Releases must contain a Jarvis-windows.zip asset.
GITHUB_REPO = os.getenv("JARVIS_GITHUB_REPO", "gogosha11/Jarvis")
UPDATE_API_URL = os.getenv(
    "JARVIS_UPDATE_API_URL",
    f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
)
APP_VERSION = "2.6.3"


class WaveBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.base_color = QColor(
            QSettings("Jarvis", "JarvisAssistant").value(
                "background_color", "#071426"
            )
        )
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_wave)
        self.timer.start(35)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def update_wave(self):
        self.phase += 0.035
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, self.base_color.darker(360))
        gradient.setColorAt(0.58, self.base_color)
        gradient.setColorAt(1, self.base_color.lighter(145))
        painter.fillRect(self.rect(), gradient)
        width, height = self.width(), self.height()
        colors = (QColor(5, 28, 70, 170), QColor(5, 54, 125, 150), QColor(13, 89, 190, 120))
        for index, color in enumerate(colors):
            path = QPainterPath(QPointF(0, height * (0.82 + index * 0.06)))
            for x in range(0, width + 25, 25):
                y = height * (0.82 + index * 0.06)
                y += math.sin(x / 150 + self.phase + index * 1.4) * (18 + index * 10)
                y += math.sin(x / 78 - self.phase * 0.7) * (7 + index * 4)
                path.lineTo(x, y)
            path.lineTo(width, height)
            path.lineTo(0, height)
            path.closeSubpath()
            painter.fillPath(path, color)

    def set_background_color(self, color):
        self.base_color = QColor(color)
        self.update()


class GlassPanel(QFrame):
    def __init__(self, parent=None, color="rgba(8, 19, 39, 150)"):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {color}; border: 1px solid rgba(105, 157, 235, 55); "
            "border-radius: 14px; }}"
        )


class PowerButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.phase = 0.0
        self.setMinimumSize(320, 320)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)

    def set_active(self, value):
        self.active = value
        if value:
            self.timer.start(30)
        else:
            self.timer.stop()
        self.update()

    def animate(self):
        self.phase += 0.06
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) * 0.36
        pulse = math.sin(self.phase * 2) * 3 if self.active else 0
        accent = QColor("#5fd9ff") if self.active else QColor("#7186b4")
        glow = QColor(65, 155, 255, 38 if self.active else 18)
        painter.setPen(QPen(glow, 18 + pulse))
        painter.drawEllipse(center, radius + 15 + pulse, radius + 15 + pulse)
        painter.setPen(QPen(QColor("#192e59"), 3))
        painter.drawEllipse(center, radius + 32, radius + 32)
        painter.setPen(QPen(QColor("#36548b"), 3))
        painter.drawEllipse(center, radius + 8, radius + 8)
        painter.setBrush(QColor("#101e39"))
        painter.setPen(QPen(QColor("#41649a"), 2))
        painter.drawEllipse(center, radius - 10, radius - 10)
        if self.active:
            painter.setPen(QPen(QColor("#72c9ff"), 2))
            painter.drawArc(int(center.x() - radius - 25), int(center.y() - radius - 25),
                            int((radius + 25) * 2), int((radius + 25) * 2),
                            int(-self.phase * 180), 105 * 16)
            for offset in (0, 2.1, 4.2):
                angle = self.phase + offset
                dot = QPointF(center.x() + math.cos(angle) * (radius + 27),
                               center.y() + math.sin(angle) * (radius + 27))
                painter.setBrush(QColor("#68d8ff"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(dot, 4, 4)
        painter.setPen(QPen(accent, 7))
        painter.drawLine(QPointF(center.x(), center.y() - 38),
                         QPointF(center.x(), center.y() + 7))
        painter.drawArc(int(center.x() - 34), int(center.y() - 23), 68, 70, 215 * 16, 110 * 16)


class AvatarWidget(QWidget):
    """Small built-in animated avatar; no extra service or model is required."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.active = False
        self.setFixedSize(150, 150)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(80)

    def set_active(self, value):
        self.active = value
        self.update()

    def animate(self):
        self.phase += 0.28
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        glow = QColor(83, 205, 255, 35 if self.active else 16)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 64, 64)
        painter.setBrush(QColor("#152c50"))
        painter.setPen(QPen(QColor("#62d7ff"), 2))
        painter.drawEllipse(QPointF(cx, cy - 5), 45, 48)
        painter.setBrush(QColor("#091426"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx - 17, cy - 14), 5, 7)
        painter.drawEllipse(QPointF(cx + 17, cy - 14), 5, 7)
        mouth_height = 6 + (abs(math.sin(self.phase)) * 10 if self.active else 2)
        painter.setBrush(QColor("#62d7ff"))
        painter.drawRoundedRect(
            int(cx - 14), int(cy + 19), 28, int(mouth_height), 5, 5
        )


class Launcher(QWidget):
    _update_ready = Signal(str, str, str)
    _update_started = Signal()
    _update_failed = Signal()

    def __init__(self):
        super().__init__()
        self.process = None
        self._update_ready.connect(self._show_update_dialog)
        self._update_started.connect(self._update_started_message)
        self._update_failed.connect(self._update_failed_message)
        settings = QSettings("Jarvis", "JarvisAssistant")
        if settings.value("wake_keyword", None) is None:
            settings.setValue("wake_keyword", "Джарвис")
        self.setWindowTitle("Джарвис — голосовой ассистент")
        icon_path = self._resource_path("jarvis.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(1050, 680)
        self.resize(1180, 760)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.build_ui()
        QTimer.singleShot(700, self.offer_desktop_shortcut)
        QTimer.singleShot(1200, self.check_for_updates)

    def check_for_updates(self):
        """Check GitHub in the background so startup never freezes."""
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self):
        if "YOUR_GITHUB_NAME" in UPDATE_API_URL:
            return
        try:
            response = requests.get(
                UPDATE_API_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=8,
            )
            response.raise_for_status()
            release = response.json()
            latest = str(release.get("tag_name", "")).lstrip("v")
            if not latest or not self._is_newer_version(latest, APP_VERSION):
                return
            assets = release.get("assets", [])
            asset = next(
                (item for item in assets if item.get("name", "").lower().endswith(".zip")),
                None,
            )
            if not asset:
                return
            self._update_ready.emit(latest, release.get("name") or latest,
                                    asset["browser_download_url"])
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return

    @staticmethod
    def _is_newer_version(candidate, current):
        def parts(value):
            return tuple(int(piece) for piece in value.split(".")[:3])
        try:
            return parts(candidate) > parts(current)
        except (ValueError, AttributeError):
            return candidate != current

    def _show_update_dialog(self, version, title, download_url):
        answer = QMessageBox.question(
            self,
            "Доступно обновление Jarvis",
            f"Вышла версия {version}.\n\n{title}\n\nСкачать и установить обновление сейчас?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            threading.Thread(
                target=self._download_and_restart,
                args=(download_url, version),
                daemon=True,
            ).start()

    def _download_and_restart(self, download_url, version):
        try:
            response = requests.get(download_url, timeout=60, stream=True)
            response.raise_for_status()
            archive = Path(tempfile.gettempdir()) / f"jarvis-update-{version}.zip"
            with archive.open("wb") as output:
                for chunk in response.iter_content(1024 * 128):
                    if chunk:
                        output.write(chunk)
            target = Path(sys.executable).resolve().parent
            subprocess.Popen([sys.executable, "--apply-update", str(archive), str(target)])
            self._update_started.emit()
        except (requests.RequestException, OSError):
            self._update_failed.emit()

    def _update_started_message(self):
        self.set_status_message("Обновление скачано. Jarvis перезапускается…")
        QTimer.singleShot(500, self.close)

    def _update_failed_message(self):
        QMessageBox.warning(self, "Обновление Jarvis", "Не удалось скачать обновление.")


    @staticmethod
    def _resource_path(name):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        candidate = root / name
        if candidate.exists():
            return candidate
        return Path(__file__).resolve().parent / name

    def offer_desktop_shortcut(self):
        """Ask once whether to create a normal Windows desktop shortcut."""
        settings = QSettings("Jarvis", "JarvisAssistant")
        if settings.value("desktop_shortcut_prompted", False, type=bool):
            return
        settings.setValue("desktop_shortcut_prompted", True)
        answer = QMessageBox.question(
            self,
            "Jarvis",
            "Создать ярлык Jarvis на рабочем столе?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if self.create_desktop_shortcut():
                self.set_status_message("Ярлык Jarvis создан на рабочем столе.")
            else:
                QMessageBox.warning(
                    self,
                    "Jarvis",
                    "Не удалось создать ярлык автоматически.",
                )

    def create_desktop_shortcut(self):
        if os.name != "nt":
            return False
        try:
            desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            shortcut = desktop / "Jarvis.lnk"
            if getattr(sys, "frozen", False):
                target = Path(sys.executable).resolve()
                arguments = ""
                working_dir = target.parent
            else:
                target = Path(sys.executable).resolve()
                script = Path(__file__).resolve()
                arguments = f'"{script}"'
                working_dir = script.parent
            icon = self._resource_path("jarvis.ico").resolve()
            script = (
                "$ws=New-Object -ComObject WScript.Shell;"
                f"$s=$ws.CreateShortcut('{shortcut}');"
                f"$s.TargetPath='{target}';"
                f"$s.Arguments='{arguments}';"
                f"$s.WorkingDirectory='{working_dir}';"
                f"$s.IconLocation='{icon},0';"
                "$s.Save()"
            )
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15,
                check=False,
            )
            return result.returncode == 0 and shortcut.exists()
        except (OSError, subprocess.SubprocessError, KeyError):
            return False

    def build_ui(self):
        # Keep the animated background and the controls as two ordinary
        # children.  This is deliberately more explicit than a stacked
        # layout: translucent/frameless windows can otherwise end up painting
        # the background over the second page on some Qt platforms.
        background = WaveBackground(self)
        overlay = QWidget(self)
        self.background = background
        self.overlay = overlay
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        overlay.setStyleSheet("background: transparent;")
        background.setGeometry(self.rect())
        overlay.setGeometry(self.rect())
        overlay.raise_()
        close_button = QPushButton("×", overlay)
        close_button.setFixedSize(38, 38)
        close_button.setStyleSheet(
            "QPushButton { color:#a8b8d6; background:transparent; border:0; "
             "font-size:24px; font-weight:300; padding-bottom:10px; }"
            "QPushButton:hover { color:white; background:rgba(225,70,100,150); border-radius:7px; }"
        )
        close_button.clicked.connect(self.close)
        minimize_button = QPushButton("—", overlay)
        minimize_button.setFixedSize(38, 38)
        minimize_button.setStyleSheet(
             "QPushButton { color:#a8b8d6; background:transparent; border:0; font-size:17px; padding-bottom:10px; }"
            "QPushButton:hover { color:white; background:rgba(80,120,180,130); border-radius:7px; }"
        )
        minimize_button.clicked.connect(self.showMinimized)

        def resize_overlay(event):
            close_button.move(overlay.width() - 52, 12)
            minimize_button.move(overlay.width() - 94, 12)
            QWidget.resizeEvent(overlay, event)

        overlay.resizeEvent = resize_overlay
        close_button.raise_()
        minimize_button.raise_()
        main = QHBoxLayout(overlay)
        main.setContentsMargins(20, 18, 20, 18)
        main.setSpacing(18)
        sidebar = QFrame()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet("QFrame { background-color: rgba(4, 12, 29, 175); border-radius: 16px; }")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 22, 14, 18)
        logo = QLabel("◈  JARVIS")
        logo.setStyleSheet("color:#69dcff; font-size:21px; font-weight:700;")
        side.addWidget(logo)
        side.addSpacing(38)
        for text, active in (("⌂   Главное окно", True), ("◌   Команды", False),
                             ("▣   Новости", False), ("✦   Интеграции", False)):
            item = self.nav_button(text, active)
            if "Главное окно" in text:
                item.clicked.connect(lambda: self.pages.setCurrentIndex(0))
            elif "Команды" in text:
                item.clicked.connect(lambda: self.set_status_message("Команды доступны через голосовой модуль."))
            elif "Новости" in text:
                item.clicked.connect(lambda: self.set_status_message("Скажите: «Джарвис, какие новости?»"))
            elif "Интеграции" in text:
                item.clicked.connect(lambda: self.set_status_message("Интеграции появятся в следующем обновлении."))
            side.addWidget(item)
        settings = self.nav_button("⚙   Настройки", False)
        settings.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        side.addWidget(settings)
        side.addStretch()
        system = QLabel("●  Система активна")
        system.setStyleSheet("color:#64e6a6; font-size:11px;")
        side.addWidget(system)
        main.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background-color: transparent;")
        self.pages.addWidget(self.home_page())
        self.pages.addWidget(self.settings_page())
        main.addWidget(self.pages, 1)

    def resizeEvent(self, event):
        if hasattr(self, "background"):
            self.background.setGeometry(self.rect())
        if hasattr(self, "overlay"):
            self.overlay.setGeometry(self.rect())
            self.overlay.raise_()
        super().resizeEvent(event)

    def nav_button(self, text, active=False):
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton {{ text-align:left; color:{'#f2f6ff' if active else '#8292b2'}; "
            f"background:{'rgba(41, 82, 145, 185)' if active else 'transparent'}; "
            "border:0; border-radius:9px; padding:12px 12px; font-size:12px; }"
            "QPushButton:hover { background: rgba(48, 103, 179, 130); color:white; }"
        )
        return button

    def title(self, text, size=25):
        label = QLabel(text)
        label.setStyleSheet(f"color:#f1f5ff; font-size:{size}px; font-weight:700;")
        return label

    def home_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 12, 20, 12)
        header = QHBoxLayout()
        header.addWidget(self.title("Основное окно"))
        header.addStretch()
        nickname = QLabel("gogosha_blender")
        nickname.setStyleSheet("color:#8ea6cc; font-size:11px;")
        header.addWidget(nickname)
        header.addSpacing(15)
        mode = QLabel("локальная система  ︱  JARVIS PRO")
        mode.setStyleSheet("color:#7385a5; font-size:11px;")
        header.addWidget(mode)
        layout.addLayout(header)
        layout.addSpacing(18)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        control = GlassPanel(color="rgba(7, 18, 37, 112)")
        control.setMinimumWidth(360)
        control_layout = QVBoxLayout(control)
        control_layout.setContentsMargins(22, 20, 22, 18)
        control_layout.addWidget(self.small_label("ЦЕНТР УПРАВЛЕНИЯ"))
        control_layout.addWidget(self.small_label("ГОЛОСОВОЙ МОДУЛЬ"))
        self.avatar = AvatarWidget()
        self.avatar.setVisible(self.avatar_enabled())
        control_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignCenter)
        self.power = PowerButton()
        self.power.clicked.connect(self.toggle_process)
        control_layout.addWidget(self.power, 1, Qt.AlignmentFlag.AlignCenter)
        self.status = QLabel("Готов к работе")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet("color:#8b9fca; font-size:12px;")
        control_layout.addWidget(self.status)
        columns.addWidget(control)
        history = GlassPanel(color="rgba(7, 18, 37, 95)")
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(25, 22, 25, 22)
        history_layout.addWidget(self.small_label("ИСТОРИЯ КОМАНД"))
        self.history = QLabel("Добро пожаловать в командный центр.\n\nСкажите «Джарвис» и назовите команду.")
        self.history.setWordWrap(True)
        self.history.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history.setStyleSheet("color:#d0dcf2; font-size:13px; line-height:1.6;")
        history_layout.addWidget(self.history)
        columns.addWidget(history, 1)
        layout.addLayout(columns, 1)
        return page

    def settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.addWidget(self.title("Настройки"))
        subtitle = QLabel("Настройте звук и поведение голосового модуля")
        subtitle.setStyleSheet("color:#8292b2; font-size:12px;")
        layout.addWidget(subtitle)
        layout.addSpacing(24)
        customization = QPushButton("✧  Кастомизация")
        customization.setStyleSheet(self.secondary_button_style())
        customization.clicked.connect(self.show_customization)
        layout.addWidget(customization, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(14)
        panel = GlassPanel(color="rgba(7, 18, 37, 125)")
        p = QVBoxLayout(panel)
        p.setContentsMargins(28, 26, 28, 28)
        p.addWidget(self.small_label("ЗВУКОВОЙ ПРОФИЛЬ"))
        mic_title = QLabel("Микрофон для распознавания речи")
        mic_title.setStyleSheet("color:#d0dcf2; font-size:12px;")
        p.addWidget(mic_title)
        self.microphone = QComboBox()
        self.microphone.addItem("Микрофон по умолчанию", "")
        try:
            import speech_recognition as sr
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                label = name.strip() or f"Микрофон {index}"
                self.microphone.addItem(f"{index}: {label}", str(index))
        except Exception as error:
            self.microphone.addItem(f"Не удалось получить список: {error}", "")
        self.microphone.setStyleSheet(
            "QComboBox { color:#dce8ff; background:#142746; border:1px solid #315486; "
            "border-radius:7px; padding:8px 10px; }"
            "QComboBox QAbstractItemView { color:#dce8ff; background:#142746; "
            "selection-background-color:#2d65a6; }"
        )
        p.addWidget(self.microphone)
        p.addSpacing(14)
        p.addWidget(self.small_label("АКТИВАЦИЯ"))
        keyword_title = QLabel("Ключевое слово")
        keyword_title.setStyleSheet("color:#d0dcf2; font-size:12px;")
        p.addWidget(keyword_title)
        self.keyword = QLineEdit()
        self.keyword.setText(
            QSettings("Jarvis", "JarvisAssistant").value("wake_keyword", "Джарвис")
        )
        self.keyword.setPlaceholderText("Джарвис")
        self.keyword.setToolTip("Слово, с которого должна начинаться голосовая команда")
        self.keyword.setStyleSheet(
            "QLineEdit { color:#dce8ff; background:#142746; border:1px solid #315486; "
            "border-radius:7px; padding:8px 10px; }"
            "QLineEdit:focus { border:1px solid #65d9ff; }"
        )
        self.keyword.editingFinished.connect(self.save_keyword)
        p.addWidget(self.keyword)
        keyword_hint = QLabel("Например: «Компьютер, открой браузер»")
        keyword_hint.setStyleSheet("color:#7385a5; font-size:10px;")
        p.addWidget(keyword_hint)
        p.addSpacing(14)
        p.addWidget(self.small_label("ГОЛОС ОТВЕТОВ"))
        voice_title = QLabel("Голос для ответов ассистента")
        voice_title.setStyleSheet("color:#d0dcf2; font-size:12px;")
        p.addWidget(voice_title)
        self.voice_choice = QComboBox()
        self.voice_profiles = (
            ("Джарвис (базовый, локальный)", ""),
            ("Мита (Mita / Miside)", "6dc11f3f67a543f6ad4537a4a347e224"),
            ("Мариарти", "cc1b79b1108f4ed3b8aac118ba6ebd07"),
            ("Рик", "fcb391ebe91a438d9c810ae17cde81de"),
            ("Морти", "3674e320208a4da19becbea85d993d6e"),
            ("Губка Боб", "a2acc0d939984f5a96edd720d5564d44"),
        )
        for name, voice_id in self.voice_profiles:
            self.voice_choice.addItem(name, voice_id)
        saved_voice = QSettings("Jarvis", "JarvisAssistant").value("voice_id", "")
        saved_index = self.voice_choice.findData(saved_voice)
        self.voice_choice.setCurrentIndex(max(0, saved_index))
        self.voice_choice.currentIndexChanged.connect(self.save_voice_choice)
        self.voice_choice.setStyleSheet(
            "QComboBox { color:#dce8ff; background:#142746; border:1px solid #315486; "
            "border-radius:7px; padding:8px 10px; }"
            "QComboBox QAbstractItemView { color:#dce8ff; background:#142746; "
            "selection-background-color:#2d65a6; }"
        )
        p.addWidget(self.voice_choice)
        self.voice_notice = QLabel(
            "⚠ Другие голоса работают только в онлайн-режиме через Fish Audio. "
            "В локальном режиме доступен базовый голос Джарвиса."
        )
        self.voice_notice.setWordWrap(True)
        self.voice_notice.setStyleSheet(
            "color:#e7b96a; background:rgba(120,78,25,80); border:1px solid rgba(231,185,106,80); "
            "border-radius:7px; padding:8px; font-size:10px;"
        )
        self.voice_notice.setVisible(bool(saved_voice))
        p.addWidget(self.voice_notice)
        self.personality = QCheckBox("Использовать характер выбранного голоса")
        self.personality.setChecked(
            QSettings("Jarvis", "JarvisAssistant").value(
                "voice_personality", False, type=bool
            )
        )
        self.personality.setToolTip(
            "ИИ будет подбирать стиль ответа под выбранного персонажа."
        )
        self.personality.setStyleSheet(
            "QCheckBox { color:#d0dcf2; font-size:12px; spacing:9px; }"
            "QCheckBox::indicator { width:18px; height:18px; }"
            "QCheckBox::indicator:unchecked { background:#142746; border:1px solid #315486; border-radius:4px; }"
            "QCheckBox::indicator:checked { background:#56bfff; border:1px solid #b8efff; border-radius:4px; }"
        )
        self.personality.toggled.connect(
            lambda value: QSettings("Jarvis", "JarvisAssistant").setValue(
                "voice_personality", value
            )
        )
        p.addWidget(self.personality)
        p.addSpacing(8)
        row = QHBoxLayout()
        volume_title = QLabel("Громкость голоса Джарвиса")
        volume_title.setStyleSheet("color:#d0dcf2; font-size:12px;")
        row.addWidget(volume_title)
        self.volume_value = QLabel("80%")
        self.volume_value.setStyleSheet("color:#65d9ff; font-weight:700;")
        row.addStretch()
        row.addWidget(self.volume_value)
        p.addLayout(row)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.valueChanged.connect(lambda value: self.volume_value.setText(f"{value}%"))
        self.volume.setStyleSheet(
            "QSlider::groove:horizontal { height:6px; background:rgba(91,124,177,80); border-radius:3px; }"
            "QSlider::sub-page:horizontal { background:#56bfff; border-radius:3px; }"
            "QSlider::handle:horizontal { width:16px; margin:-5px 0; background:#d8f6ff; "
            "border:2px solid #55caff; border-radius:8px; }"
        )
        p.addWidget(self.volume)
        p.addSpacing(16)
        self.auto_interrupt = QCheckBox(
            "Разрешить Джарвису иногда отвлекать меня"
        )
        self.auto_interrupt.setChecked(
            QSettings("Jarvis", "JarvisAssistant").value(
                "auto_tik_tok", False, type=bool
            )
        )
        self.auto_interrupt.setToolTip(
            "После запуска музыки или видео Джарвис подождёт час, "
            "затем один раз запустит протокол Тик Ток без команды."
        )
        self.auto_interrupt.setStyleSheet(
            "QCheckBox { color:#d0dcf2; font-size:12px; spacing:9px; }"
            "QCheckBox::indicator { width:18px; height:18px; }"
            "QCheckBox::indicator:unchecked { background:#142746; border:1px solid #315486; border-radius:4px; }"
            "QCheckBox::indicator:checked { background:#56bfff; border:1px solid #b8efff; border-radius:4px; }"
        )
        self.auto_interrupt.toggled.connect(
            lambda value: QSettings("Jarvis", "JarvisAssistant").setValue(
                "auto_tik_tok", value
            )
        )
        p.addWidget(self.auto_interrupt)
        p.addSpacing(10)
        clear = QPushButton("Очистить историю команд")
        clear.setStyleSheet(self.secondary_button_style())
        clear.clicked.connect(lambda: self.history.setText("История команд очищена."))
        p.addWidget(clear, 0, Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:0; }"
            "QScrollBar:vertical { background:rgba(7,18,37,100); width:10px; }"
            "QScrollBar::handle:vertical { background:#315486; border-radius:5px; min-height:30px; }"
        )
        panel.setMinimumHeight(760)
        scroll.setWidget(panel)
        layout.addWidget(scroll, 1)
        back = QPushButton("←  Вернуться в командный центр")
        back.setStyleSheet(self.secondary_button_style())
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        return page

    def show_customization(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Кастомизация")
        dialog.setFixedSize(430, 300)
        dialog.setStyleSheet(
            "QDialog { background:#071426; color:#f1f5ff; }"
            "QLabel { color:#d0dcf2; }"
            "QPushButton { color:#dce8ff; background:#18345d; border:1px solid #35649d; "
            "border-radius:7px; padding:10px 14px; font-size:12px; }"
            "QPushButton:hover { color:white; background:#28558e; }"
        )
        content = QVBoxLayout(dialog)
        content.setContentsMargins(26, 24, 26, 24)
        heading = QLabel("КАСТОМИЗАЦИЯ")
        heading.setStyleSheet("color:#69dcff; font-size:13px; font-weight:700;")
        content.addWidget(heading)
        subtitle = QLabel("Настройте внешний вид командного центра")
        subtitle.setStyleSheet("color:#8292b2; font-size:11px;")
        content.addWidget(subtitle)
        content.addSpacing(16)
        choose = QPushButton("Палитра цветов")
        reset = QPushButton("Вернуть стандартный фон")
        avatar = QPushButton()
        avatar.setText(
            "Выключить аватара" if self.avatar_enabled() else "Включить аватара"
        )
        content.addWidget(choose)
        content.addWidget(reset)
        content.addWidget(avatar)
        content.addStretch()
        close = QPushButton("Готово")
        content.addWidget(close)
        close.clicked.connect(dialog.accept)

        def choose_color():
            current = self.background.base_color
            color = QColorDialog.getColor(current, self, "Цвет фона")
            if color.isValid():
                QSettings("Jarvis", "JarvisAssistant").setValue(
                    "background_color", color.name()
                )
                self.background.set_background_color(color)
                self.set_status_message("Цвет фона сохранён.")

        def reset_color():
            default = QColor("#071426")
            QSettings("Jarvis", "JarvisAssistant").setValue(
                "background_color", default.name()
            )
            self.background.set_background_color(default)
            self.set_status_message("Цвет фона возвращён к стандартному.")

        def toggle_avatar():
            enabled = not self.avatar_enabled()
            QSettings("Jarvis", "JarvisAssistant").setValue(
                "avatar_enabled", enabled
            )
            if hasattr(self, "avatar"):
                self.avatar.setVisible(enabled)
            avatar.setText("Выключить аватара" if enabled else "Включить аватара")
            self.set_status_message(
                "Аватар включён." if enabled else "Аватар выключен."
            )

        choose.clicked.connect(choose_color)
        reset.clicked.connect(reset_color)
        avatar.clicked.connect(toggle_avatar)
        dialog.exec()

    @staticmethod
    def avatar_enabled():
        return QSettings("Jarvis", "JarvisAssistant").value(
            "avatar_enabled", True, type=bool
        )

    def save_keyword(self):
        keyword = self.keyword.text().strip()
        if not keyword:
            keyword = "Джарвис"
            self.keyword.setText(keyword)
        QSettings("Jarvis", "JarvisAssistant").setValue("wake_keyword", keyword)
        self.set_status_message(f"Ключевое слово сохранено: «{keyword}».")

    def save_voice_choice(self, index):
        voice_id = self.voice_choice.itemData(index) or ""
        QSettings("Jarvis", "JarvisAssistant").setValue("voice_id", voice_id)
        self.voice_notice.setVisible(bool(voice_id))
        if voice_id:
            self.set_status_message(
                "Выбран онлайн-голос. Для него нужен интернет и ключ Fish Audio."
            )
        else:
            self.set_status_message("Выбран базовый локальный голос Джарвиса.")

    @staticmethod
    def small_label(text):
        label = QLabel(text)
        label.setStyleSheet("color:#7894c1; font-size:10px; font-weight:700;")
        return label

    @staticmethod
    def secondary_button_style():
        return (
            "QPushButton { color:#dce8ff; background:#18345d; border:1px solid #35649d; "
            "border-radius:7px; padding:9px 14px; font-size:12px; }"
            "QPushButton:hover { color:white; background:#28558e; }"
            "QPushButton:pressed { background:#102947; }"
        )

    def set_status_message(self, text):
        if hasattr(self, "status"):
            self.status.setText(text)
        if hasattr(self, "history"):
            self.history.setText(text)

    def toggle_process(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process = None
            self.power.set_active(False)
            if hasattr(self, "avatar"):
                self.avatar.set_active(False)
            self.status.setText("Готов к работе")
            return
        base_dir = Path(__file__).resolve().parent
        app_file = base_dir / "app.py"
        # Also work when the launcher is started directly from an exported
        # attachment whose files received timestamped names.
        if not app_file.exists():
            candidates = sorted(base_dir.glob("app_*.py"))
            if candidates:
                app_file = candidates[0]
        env = os.environ.copy()
        env["JARVIS_VOLUME"] = str(self.volume.value() / 100)
        env["JARVIS_MICROPHONE_INDEX"] = str(self.microphone.currentData() or "")
        keyword = self.keyword.text().strip() or "Джарвис"
        QSettings("Jarvis", "JarvisAssistant").setValue("wake_keyword", keyword)
        env["JARVIS_WAKE_WORD"] = keyword
        voice_id = self.voice_choice.currentData() or ""
        env["JARVIS_VOICE_ID"] = voice_id
        env["JARVIS_VOICE_PERSONALITY"] = (
            "1" if self.personality.isChecked() else "0"
        )
        enabled = (
            self.auto_interrupt.isChecked()
            if getattr(self, "auto_interrupt", None) is not None
            else QSettings("Jarvis", "JarvisAssistant").value(
                "auto_tik_tok", False, type=bool
            )
        )
        env["JARVIS_AUTO_TIK_TOK"] = "1" if enabled else "0"
        try:
            if not app_file.exists():
                raise FileNotFoundError("Файл app.py не найден рядом с launcher.py")
            # In a PyInstaller build the launcher and the voice worker are
            # started by the same executable.  In source mode keep the
            # separate app.py process for easy debugging.
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--headless"]
            else:
                command = [sys.executable, str(app_file), "--headless"]
            self.process = subprocess.Popen(
                command,
                cwd=str(base_dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.power.set_active(True)
            if hasattr(self, "avatar"):
                self.avatar.set_active(True)
            self.status.setText("Слушаю вас")
        except OSError as error:
            self.status.setText(f"Ошибка запуска: {error}")

    def closeEvent(self, event):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    icon_path = Launcher._resource_path("jarvis.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = Launcher()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()