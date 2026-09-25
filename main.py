import sys
import time
import socket
import platform
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
VERSION = "1.0.0"

UPDATE_INTERVAL = 1000
HISTORY_LENGTH = 60


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

    def update_value(self, value, detail="", percentage=None):

        self.value_label.setText(value)
        self.detail_label.setText(detail)

        if percentage is not None:
            self.progress.setValue(int(percentage))
        else:
            self.progress.setValue(0)


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

        metrics_layout.addWidget(self.cpu_card, 0, 0)
        metrics_layout.addWidget(self.ram_card, 0, 1)
        metrics_layout.addWidget(self.disk_card, 0, 2)

        metrics_layout.addWidget(self.network_card, 1, 0)
        metrics_layout.addWidget(self.uptime_card, 1, 1)

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
    # METRICS
    # --------------------------------------------------------

    def update_metrics(self):

        # CPU
        cpu = psutil.cpu_percent(interval=None)

        self.cpu_card.update_value(
            f"{cpu:.1f}%",
            f"{psutil.cpu_count(logical=True)} logical cores",
            cpu,
        )

        self.cpu_history.add_value(cpu)

        # RAM
        memory = psutil.virtual_memory()

        ram_percent = memory.percent

        self.ram_card.update_value(
            f"{ram_percent:.1f}%",
            f"{format_bytes(memory.used)} / {format_bytes(memory.total)}",
            ram_percent,
        )

        self.ram_history.add_value(ram_percent)

        # DISK
        disk = psutil.disk_usage("/")

        self.disk_card.update_value(
            f"{disk.percent:.1f}%",
            f"{format_bytes(disk.used)} / {format_bytes(disk.total)}",
            disk.percent,
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
        )

        # UPTIME
        uptime = time.time() - psutil.boot_time()

        self.uptime_card.update_value(
            format_uptime(uptime),
            "System uptime",
        )


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

        QProgressBar {
            background-color: #292D36;
            border: none;
            border-radius: 3px;
        }

        QProgressBar::chunk {
            background-color: #4F8CFF;
            border-radius: 3px;
        }
        """
    )

    window = PerichMonitor()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()