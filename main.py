import os
import json
import tempfile
import shutil
import zipfile
import tarfile
import io
import time
import requests
from PySide2.QtGui import QPalette  
from PySide2.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem,
    QPushButton, QVBoxLayout, QWidget, QComboBox, QLabel,
    QInputDialog, QFileDialog, QMessageBox, QProgressDialog,
    QHBoxLayout, QLineEdit, QFormLayout, QDialog, QStyleFactory
)
from PySide2.QtCore import Qt, QThread, Signal
from PySide2.QtGui import QDoubleValidator

class DownloadThread(QThread):
    progress = Signal(int, str, str)  # percent, downloaded/total, speed
    finished = Signal(bool, str)       # success, message
    error = Signal(str)

    def __init__(self, url, file_type, download_folder):
        super().__init__()
        self.url = url
        self.file_type = file_type
        self.download_folder = download_folder
        self._is_running = True

    def run(self):
        try:
            response = requests.get(self.url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            temp_file = os.path.join(tempfile.gettempdir(), f"release.{self.file_type}")

            downloaded = 0
            start_time = time.time()
            last_update = 0

            with open(temp_file, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not self._is_running:
                        raise Exception("Download canceled by user.")
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Update progress every 0.1s
                    current_time = time.time()
                    if current_time - last_update > 0.1:
                        speed = downloaded / (current_time - start_time) if (current_time - start_time) > 0 else 0
                        percent = int((downloaded / total_size) * 100) if total_size > 0 else 0
                        self.progress.emit(
                            percent,
                            f"{downloaded / (1024 * 1024):.2f} / {total_size / (1024 * 1024):.2f} MB" if total_size > 0 else "Unknown size",
                            f"{speed / (1024 * 1024):.2f} MB/s"
                        )
                        last_update = current_time

            # Extract/move based on file type
            if self.file_type == "zip":
                with zipfile.ZipFile(temp_file) as zip_ref:
                    zip_ref.extractall(self.download_folder)
            elif self.file_type == "tar.gz":
                with tarfile.open(temp_file, "r:gz") as tar_ref:
                    tar_ref.extractall(self.download_folder)
            else:  # exe, dmg, or other
                os.makedirs(self.download_folder, exist_ok=True)
                shutil.move(temp_file, os.path.join(self.download_folder, os.path.basename(temp_file)))

            # Cleanup
            if self.file_type in ["zip", "tar.gz", "7z"]:
                os.remove(temp_file)

            self.finished.emit(True, "Update completed successfully!")
        except Exception as e:
            self.finished.emit(False, f"Update failed: {str(e)}")

    def stop(self):
        self._is_running = False

class AssetSelectionDialog(QDialog):
    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Asset")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Asset selection
        self.asset_combo = QComboBox()
        for asset in assets:
            self.asset_combo.addItem(f"{asset['name']} ({asset['size'] / (1024 * 1024):.2f} MB)", asset)
        layout.addWidget(QLabel("Select an asset to download:"))
        layout.addWidget(self.asset_combo)

        # OK/Cancel buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def get_selected_asset(self):
        return self.asset_combo.currentData()

class AutoUpdater(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitHub Auto Updater")
        self.setGeometry(100, 100, 800, 600)

        # Main widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Theme selector
        theme_layout = QHBoxLayout()
        theme_layout.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.currentTextChanged.connect(self.change_theme)
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        layout.addLayout(theme_layout)

        # Table for repositories (removed Auto-Update column)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Repository URL", "Download Folder", "Action"])
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()
        self.add_repo_btn = QPushButton("Add Repository")
        self.add_repo_btn.clicked.connect(self.add_repository)
        button_layout.addWidget(self.add_repo_btn)

        self.save_btn = QPushButton("Save List")
        self.save_btn.clicked.connect(self.save_repositories)
        button_layout.addWidget(self.save_btn)

        self.load_btn = QPushButton("Load List")
        self.load_btn.clicked.connect(self.load_repositories)
        button_layout.addWidget(self.load_btn)

        layout.addLayout(button_layout)

        # Release dropdown and update button
        self.release_dropdown = QComboBox()
        layout.addWidget(QLabel("Select Release:"))
        layout.addWidget(self.release_dropdown)

        self.update_btn = QPushButton("Update Selected")
        self.update_btn.clicked.connect(self.update_selected)
        layout.addWidget(self.update_btn)

        # Download thread and progress dialog
        self.download_thread = None
        self.progress_dialog = None

        # Load saved repositories (if any)
        if os.path.exists("repositories.json"):
            self.load_repositories()

        # Load default theme
        self.change_theme("Light")

    def change_theme(self, theme_name):
        qss_file = f"{theme_name.lower()}.qss"
        if os.path.exists(qss_file):
            with open(qss_file, "r") as f:
                self.setStyleSheet(f.read())
        else:
            # Fallback to default theme if QSS file not found
            QApplication.setStyle(QStyleFactory.create("Fusion"))

    def add_repository(self):
        repo_url, ok1 = QInputDialog.getText(
            self, "Repository URL",
            "Enter GitHub repo URL (e.g., https://github.com/godotengine/godot):"
        )
        if not ok1:
            return

        # Validate URL
        if "github.com" not in repo_url:
            QMessageBox.warning(self, "Error", "Invalid GitHub URL.")
            return

        download_folder = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not download_folder:
            return

        # Add to table
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(repo_url))
        self.table.setItem(row, 1, QTableWidgetItem(download_folder))

        # Action buttons
        action_layout = QHBoxLayout()
        update_btn = QPushButton("Update")
        update_btn.clicked.connect(lambda: self.update_repository(row))
        action_layout.addWidget(update_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(lambda: self.table.removeRow(row))
        action_layout.addWidget(delete_btn)

        action_widget = QWidget()
        action_widget.setLayout(action_layout)
        self.table.setCellWidget(row, 2, action_widget)

    def fetch_releases(self, repo_url):
        parts = repo_url.strip("/").split("/")
        owner, repo = parts[-2], parts[-1]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"

        try:
            response = requests.get(api_url)
            response.raise_for_status()
            releases = response.json()
            return ["latest"] + [release["tag_name"] for release in releases]
        except requests.RequestException as e:
            QMessageBox.warning(self, "Error", f"Failed to fetch releases: {e}")
            return ["latest"]

    def fetch_assets(self, repo_url, release_tag):
        parts = repo_url.strip("/").split("/")
        owner, repo = parts[-2], parts[-1]

        if release_tag == "latest":
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{release_tag}"

        try:
            response = requests.get(api_url)
            response.raise_for_status()
            release = response.json()
            return release.get("assets", [])
        except requests.RequestException as e:
            QMessageBox.warning(self, "Error", f"Failed to fetch assets: {e}")
            return []

    def update_repository(self, row):
        self.selected_row = row
        repo_url = self.table.item(row, 0).text()
        releases = self.fetch_releases(repo_url)

        self.release_dropdown.clear()
        self.release_dropdown.addItems(releases)

    def update_selected(self):
        if not hasattr(self, "selected_row"):
            return

        row = self.selected_row
        repo_url = self.table.item(row, 0).text()
        download_folder = self.table.item(row, 1).text()
        release_tag = self.release_dropdown.currentText()

        # Fetch assets for the selected release
        assets = self.fetch_assets(repo_url, release_tag)
        if not assets:
            QMessageBox.warning(self, "Error", "No assets found for this release.")
            return

        # Let user select an asset
        dialog = AssetSelectionDialog(assets, self)
        if not dialog.exec_():
            return  # User canceled

        selected_asset = dialog.get_selected_asset()
        download_url = selected_asset["browser_download_url"]
        file_name = selected_asset["name"]
        file_type = os.path.splitext(file_name)[1][1:]  # e.g., "zip", "exe"

        # Show progress dialog
        self.progress_dialog = QProgressDialog("Starting download...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Downloading...")
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setValue(0)
        self.progress_dialog.show()

        # Start download in a thread
        self.download_thread = DownloadThread(download_url, file_type, download_folder)
        self.download_thread.progress.connect(self.update_progress)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.error.connect(self.download_error)
        self.download_thread.start()

    def update_progress(self, percent, downloaded_total, speed):
        self.progress_dialog.setValue(percent)
        self.progress_dialog.setLabelText(
            f"Downloading... {downloaded_total} | Speed: {speed}"
        )

    def download_finished(self, success, message):
        self.progress_dialog.close()
        if success:
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.critical(self, "Error", message)
        self.download_thread = None

    def download_error(self, message):
        self.progress_dialog.close()
        QMessageBox.critical(self, "Error", message)
        self.download_thread = None

    def save_repositories(self):
        repositories = []
        for row in range(self.table.rowCount()):
            repo_url = self.table.item(row, 0).text()
            download_folder = self.table.item(row, 1).text()
            repositories.append({
                "repo_url": repo_url,
                "download_folder": download_folder
            })

        with open("repositories.json", "w") as f:
            json.dump(repositories, f, indent=2)

        QMessageBox.information(self, "Success", "Repositories saved to repositories.json")

    def load_repositories(self):
        try:
            with open("repositories.json", "r") as f:
                repositories = json.load(f)

            for repo in repositories:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(repo["repo_url"]))
                self.table.setItem(row, 1, QTableWidgetItem(repo["download_folder"]))

                # Action buttons
                action_layout = QHBoxLayout()
                update_btn = QPushButton("Update")
                update_btn.clicked.connect(lambda r=row: self.update_repository(r))
                action_layout.addWidget(update_btn)

                delete_btn = QPushButton("Delete")
                delete_btn.clicked.connect(lambda r=row: self.table.removeRow(r))
                action_layout.addWidget(delete_btn)

                action_widget = QWidget()
                action_widget.setLayout(action_layout)
                self.table.setCellWidget(row, 2, action_widget)

            QMessageBox.information(self, "Success", "Repositories loaded from repositories.json")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load repositories: {e}")

if __name__ == "__main__":
    import sys
    from PySide2.QtGui import QColor
    app = QApplication(sys.argv)
    window = AutoUpdater()
    window.show()
    sys.exit(app.exec_())