import sys
import time
import socket
import platform
import subprocess
import re
from collections import deque

import psutil

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QVBoxLayout,
    QWidget,
    QFrame,
)


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "PerichMonitor"
VERSION = "1.1.2"

UPDATE_INTERVAL = 1000
HISTORY_LENGTH = 60

# Temperature is refreshed less frequently because Windows/WMI
# sensor queries can be considerably heavier than psutil calls.
TEMPERATURE_INTERVAL = 3000


# ============================================================
# HELPERS
# ============================================================

def format_bytes(value):
    """Convert bytes into a human-readable format."""
    units = ["B", "KB", "MB", "GB", "TB"]

    value = float(value)

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} PB"


def format_speed(value):
    """Format network speed."""
    return f"{format_bytes(value)}/s"


def format_uptime(seconds):
    """Convert uptime in seconds into a readable format."""
    seconds = int(seconds)

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours:02d}h {minutes:02d}m"

    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"


def get_local_ip():
    """Get the local IPv4 address."""
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except Exception:
        return "Unavailable"


def percentage_to_color(percentage):
    """
    Return a smooth green -> yellow -> orange -> red color.

    0%   = green
    50%  = yellow
    70%  = orange
    100% = red
    """
    try:
        value = max(0.0, min(100.0, float(percentage)))
    except (TypeError, ValueError):
        return "#4F8CFF"

    # Control points: (percentage, RGB)
    stops = [
        (0.0, (46, 204, 113)),    # green
        (50.0, (241, 196, 15)),   # yellow
        (70.0, (243, 156, 18)),   # orange
        (85.0, (230, 126, 34)),   # dark orange
        (100.0, (231, 76, 60)),   # red
    ] for index in range(len(stops) - 1):
        left_value, left_color = stops[index]
        right_value, right_color = stops[index + 1]

        if left_value <= value <= right_value:
            span = right_value - left_value
            ratio = (value - left_value) / span if span else 0

            r = int(left_color[0] + (right_color[0] - left_color[0]) * ratio)
            g = int(left_color[1] + (right_color[1] - left_color[1]) * ratio)
            b = int(left_color[2] + (right_color[2] - left_color[2]) * ratio)

            return f"#{r:02X}{g:02X}{b:02X}"

    return "#E74C3C"


def get_cpu_temperature():
    """
    Read the Windows ACPI thermal zone exposed by the mini PC.

    Confirmed sensor:
        ACPI\ThermalZone\TZ01_0

    Windows reports the value in tenths of Kelvin.
    Example:
        2932 -> 293.2 K -> 20.05 °C

    The function returns:
        (temperature_in_celsius, sensor_name)
    """

    if platform.system() != "Windows":
        return None, "Unsupported OS"

    try:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$sensor = Get-CimInstance "
                "-Namespace root/wmi "
                "-ClassName MSAcpi_ThermalZoneTemperature "
                "-ErrorAction Stop | "
                "Where-Object {$_.InstanceName -like '*TZ01_0*'} | "
                "Select-Object -First 1; "
                "if ($null -eq $sensor) { "
                "    Write-Output 'NO_SENSOR' "
                "} else { "
                "    Write-Output $sensor.CurrentTemperature "
                "}"
            ),
        ]

        # WMI/CIM can take more than 2 seconds on some mini PCs.
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0
            ),
        )

        if result.returncode != 0:
            error = result.stderr.strip()

            if error:
                return None, f"ACPI error: {error[:45]}"

            return None, "ACPI query failed"

        output = result.stdout.strip()

        if not output:
            return None, "ACPI returned no data"

        if output == "NO_SENSOR":
            return None, "TZ01_0 not found"

        # PowerShell may return more than one line.
        # Find the first numeric value.
        numeric_value = None

        for line in output.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                numeric_value = float(line)
                break
            except ValueError:
                continue

        if numeric_value is None:
            return None, f"Invalid ACPI value: {output[:40]}"

        # Windows ACPI reports temperature in tenths of Kelvin.
        celsius = (numeric_value / 10.0) - 273.15

        # Sanity check.
        if not (-20 < celsius < 120):
            return None, f"Invalid temperature: {celsius:.1f} °C"

        return celsius, "Windows ACPI TZ01_0"

def temperature_to_percentage(celsius):
    """
    Convert CPU temperature into a visual 0-100 scale.

    30 °C = 0%
    90 °C = 100%

    This is only for the progress bar; it does not claim
    that 90 °C is the CPU's exact thermal limit.
    """
    if celsius is None:
        return 0

    return max(0, min(100, ((celsius - 30) / 60) * 100))


# ============================================================
# METRIC CARD
# ============================================================

class MetricCard(QFrame):
    """Reusable card for displaying a system metric."""

    def __init__(self, title, parent=None):
        super().__init__(parent)

        self.setObjectName("metricCard")

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")

        self.value_label = QLabel("--")
        self.value_label.setObjectName("cardValue")

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("cardDetail")

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.progress)

        self.setLayout(layout)

        self.set_progress_color("#4F8CFF")

    def set_progress_color(self, color):
        """Set the progress bar chunk color for this card."""
        self.progress.setStyleSheet(
            f"""
            QProgressBar {{
                background-color: #292D36;
                border: none;
                border-radius: 3px;
            }}

            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 3px;
            }}
            """
        )

    def update_value(self, value, detail="", percentage=None, color=None):
        """Update card contents and optional progress."""
        self.value_label.setText(value)
        self.detail_label.setText(detail)

        if percentage is not None:
            percentage = max(0, min(100, float(percentage)))
            self.progress.setValue(int(percentage))

            if color is None:
                color = percentage_to_color(percentage)

            self.set_progress_color(color)
        else:
            self.progress.setValue(0)

            if color is None:
                color = "#4F8CFF"

            self.set_progress_color(color)


# ============================================================
# HISTORY WIDGET
# ============================================================

class HistoryWidget(QFrame):
    """Simple CPU/RAM history visualization."""

    def __init__(self, title, parent=None):
        super().__init__(parent)

        self.setObjectName("historyCard")

        self.title = title
        self.values = deque(maxlen=HISTORY_LENGTH)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 15, 18, 15)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("chartTitle")

        self.chart_label = QLabel()
        self.chart_label.setMinimumHeight(100)
        self.chart_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.title_label)
        layout.addWidget(self.chart_label)

        self.setLayout(layout)

    def add_value(self, value):
        self.values.append(value)
        self.update_chart()

    def update_chart(self):
        if not self.values:
            return

        width = 45
        height = 10

        rows = []

        values = list(self.values)

        while len(values) < HISTORY_LENGTH:
            values.insert(0, 0)

        for row in range(height, -1, -1):
            line = ""

            threshold = (row / height) * 100

            for value in values:
                if value >= threshold:
                    line += "█"
                else:
                    line += " "

            rows.append(line)

        chart = "\n".join(rows)

        self.chart_label.setText(
            f"{chart}\n\n"
            f"Current: {values[-1]:.1f}%"
        )


# ============================================================
# SYSTEM INFORMATION
# ============================================================

class SystemInfoCard(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("infoCard")

        layout = QGridLayout()

        layout.setContentsMargins(18, 15, 18, 15)
        layout.setHorizontalSpacing(25)
        layout.setVerticalSpacing(10)

        self.labels = {}

        information = [
            ("Hostname", socket.gethostname()),
            ("OS", f"{platform.system()} {platform.release()}"),
            ("Processor", platform.processor() or "Unknown"),
            ("Architecture", platform.machine()),
            ("Local IP", get_local_ip()),
        ]

        for row, (name, value) in enumerate(information):

            name_label = QLabel(name)
            name_label.setObjectName("infoName")

            value_label = QLabel(value)
            value_label.setObjectName("infoValue")

            value_label.setWordWrap(True)

            layout.addWidget(name_label, row, 0)
            layout.addWidget(value_label, row, 1)

            self.labels[name] = value_label

        self.setLayout(layout)


# ============================================================
# MAIN WINDOW
# ============================================================

class PerichMonitor(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.resize(1100, 720)

        self.previous_network = psutil.net_io_counters()
        self.previous_time = time.time()

        self.last_temperature_check = 0
        self.last_temperature = None
        self.temperature_source = "Unavailable"

        self.setup_ui()
        self.setup_timer()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def setup_ui(self):

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()

        main_layout.setContentsMargins(25, 20, 25, 20)
        main_layout.setSpacing(15)

        # Header
        header_layout = QHBoxLayout()

        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")

        subtitle = QLabel("Real-time system monitoring")
        subtitle.setObjectName("subtitle")

        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        # Metrics
        metrics_layout = QGridLayout()
        metrics_layout.setSpacing(12)

        self.cpu_card = MetricCard("CPU")
        self.ram_card = MetricCard("MEMORY")
        self.disk_card = MetricCard("DISK")
        self.network_card = MetricCard("NETWORK")
        self.uptime_card = MetricCard("UPTIME")
        self.temperature_card = MetricCard("CPU TEMPERATURE")

        metrics_layout.addWidget(self.cpu_card, 0, 0)
        metrics_layout.addWidget(self.ram_card, 0, 1)
        metrics_layout.addWidget(self.disk_card, 0, 2)

        metrics_layout.addWidget(self.network_card, 1, 0)
        metrics_layout.addWidget(self.uptime_card, 1, 1)
        metrics_layout.addWidget(self.temperature_card, 1, 2)

        main_layout.addLayout(metrics_layout)

        # Charts
        charts_layout = QHBoxLayout()

        self.cpu_history = HistoryWidget("CPU HISTORY")
        self.ram_history = HistoryWidget("MEMORY HISTORY")

        charts_layout.addWidget(self.cpu_history)
        charts_layout.addWidget(self.ram_history)

        main_layout.addLayout(charts_layout)

        # System information
        info_title = QLabel("SYSTEM INFORMATION")
        info_title.setObjectName("sectionTitle")

        main_layout.addWidget(info_title)

        self.system_info = SystemInfoCard()

        main_layout.addWidget(self.system_info)

        # Footer
        footer = QLabel(
            f"{APP_NAME} {VERSION} • Local monitoring • No telemetry"
        )

        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignCenter)

        main_layout.addWidget(footer)

        central_widget.setLayout(main_layout)

    # --------------------------------------------------------
    # TIMER
    # --------------------------------------------------------

    def setup_timer(self):

        self.timer = QTimer()

        self.timer.timeout.connect(self.update_metrics)

        self.timer.start(UPDATE_INTERVAL)

        # Initial update
        self.update_metrics()

    # --------------------------------------------------------
    # TEMPERATURE
    # --------------------------------------------------------

    def update_temperature(self):
        """Refresh CPU temperature if the refresh interval has elapsed."""

        current_time = time.time()

        if (
            self.last_temperature_check == 0
            or current_time - self.last_temperature_check >= TEMPERATURE_INTERVAL / 1000
        ):
            temperature, source = get_cpu_temperature()

            self.last_temperature = temperature
            self.temperature_source = source
            self.last_temperature_check = current_time

        if self.last_temperature is None:
            self.temperature_card.update_value(
                "N/A",
                self.temperature_source,
                0,
                "#626875",
            )
            return

        temperature = self.last_temperature
        percentage = temperature_to_percentage(temperature)
        color = percentage_to_color(percentage)

        self.temperature_card.update_value(
            f"{temperature:.1f} °C",
            f"Sensor: {self.temperature_source}",
            percentage,
            color,
        )

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    def update_metrics(self):

        # CPU
        cpu = psutil.cpu_percent(interval=None)

        self.cpu_card.update_value(
            f"{cpu:.1f}%",
            f"{psutil.cpu_count(logical=True)} logical cores",
            cpu,
            percentage_to_color(cpu),
        )

        self.cpu_history.add_value(cpu)

        # RAM
        memory = psutil.virtual_memory()
        ram_percent = memory.percent

        self.ram_card.update_value(
            f"{ram_percent:.1f}%",
            f"{format_bytes(memory.used)} / {format_bytes(memory.total)}",
            ram_percent,
            percentage_to_color(ram_percent),
        )

        self.ram_history.add_value(ram_percent)

        # DISK
        disk = psutil.disk_usage("/")

        self.disk_card.update_value(
            f"{disk.percent:.1f}%",
            f"{format_bytes(disk.used)} / {format_bytes(disk.total)}",
            disk.percent,
            percentage_to_color(disk.percent),
        )

        # NETWORK
        current_network = psutil.net_io_counters()
        current_time = time.time()

        elapsed = current_time - self.previous_time

        if elapsed > 0:
            download = (
                current_network.bytes_recv
                - self.previous_network.bytes_recv
            ) / elapsed

            upload = (
                current_network.bytes_sent
                - self.previous_network.bytes_sent
            ) / elapsed
        else:
            download = 0
            upload = 0

        self.previous_network = current_network
        self.previous_time = current_time

        self.network_card.update_value(
            format_speed(download),
            f"Upload: {format_speed(upload)}",
            color="#4F8CFF",
        )

        # UPTIME
        uptime = time.time() - psutil.boot_time()

        self.uptime_card.update_value(
            format_uptime(uptime),
            "System uptime",
            color="#4F8CFF",
        )

        # CPU TEMPERATURE
        self.update_temperature()


# ============================================================
# APPLICATION
# ============================================================

def main():

    app = QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))

    # --------------------------------------------------------
    # DARK THEME
    # --------------------------------------------------------

    app.setStyleSheet(
        """
        QWidget {
            background-color: #111318;
            color: #E6E6E6;
        }

        QMainWindow {
            background-color: #111318;
        }

        QLabel#appTitle {
            font-size: 28px;
            font-weight: bold;
            color: #FFFFFF;
        }

        QLabel#subtitle {
            font-size: 13px;
            color: #8D93A1;
            margin-left: 10px;
        }

        QFrame#metricCard,
        QFrame#historyCard,
        QFrame#infoCard {
            background-color: #191C22;
            border: 1px solid #292D36;
            border-radius: 10px;
        }

        QLabel#cardTitle {
            font-size: 11px;
            font-weight: bold;
            color: #8D93A1;
        }

        QLabel#cardValue {
            font-size: 25px;
            font-weight: bold;
            color: #FFFFFF;
        }

        QLabel#cardDetail {
            font-size: 11px;
            color: #8D93A1;
        }

        QLabel#chartTitle {
            font-size: 11px;
            font-weight: bold;
            color: #8D93A1;
        }

        QLabel#sectionTitle {
            font-size: 11px;
            font-weight: bold;
            color: #8D93A1;
            margin-top: 5px;
        }

        QLabel#infoName {
            font-size: 11px;
            font-weight: bold;
            color: #8D93A1;
        }

        QLabel#infoValue {
            font-size: 12px;
            color: #E6E6E6;
        }

        QLabel#footer {
            font-size: 10px;
            color: #626875;
            margin-top: 5px;
        }
        """
    )

    window = PerichMonitor()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()