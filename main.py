import sys, os, webbrowser, time, threading, json
from pathlib import Path

import pyperclip
from pynput import keyboard
from PySide6.QtGui import QTextOption  # (gerekirse)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QAction, QIcon, QKeySequence, QFont, QTextCursor, QTextCharFormat, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QLabel,
    QInputDialog, QMessageBox, QTextEdit
)

# ---------- Ayarlar ----------
APP_NAME = "NotlarApp"

NOTES_DIR = Path.home() / ("Documents" if os.name == "nt" else "") / "NotlarApp"
NOTES_DIR.mkdir(parents=True, exist_ok=True)

SEPARATOR = "\n-------------------------------\n\n"  

class NoteList(QListWidget):
    def mousePressEvent(self, event):
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class CaptureWorker(QObject):
    captured_text = Signal(str)
    status = Signal(str)

    def __init__(self, poll_ms=600):  # <-- 600ms: daha az CPU/RAM baskısı
        super().__init__()
        self.poll_ms = poll_ms
        self._running = False
        self._last_clip = ""
        self._t = None
        self._hotkey_listener = None

    def start(self):
        if self._running: return
        self._running = True
        self._last_clip = (pyperclip.paste() or "").strip()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        self._hotkey_listener = keyboard.GlobalHotKeys({
            '<ctrl>+<shift>+s': self.copy_and_emit
        })
        self._hotkey_listener.start()
        self.status.emit("Capture ON")

    def stop(self):
        self._running = False
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        self.status.emit("Capture OFF")

    def _loop(self):
        while self._running:
            try:
                current = pyperclip.paste()
                norm = (current or "").strip()
                if norm and norm != self._last_clip:
                    self._last_clip = norm
                    self.captured_text.emit(norm)  # çok satır korunur
                time.sleep(self.poll_ms / 1000.0)
            except Exception as e:
                self.status.emit(f"Hata (clipboard): {e}")
                time.sleep(0.5)

    def copy_and_emit(self):
        try:
            before = (pyperclip.paste() or "").strip()
            kb = keyboard.Controller()
            with kb.pressed(keyboard.Key.ctrl):
                kb.press('c'); kb.release('c')
            time.sleep(0.15)
            after = (pyperclip.paste() or "").strip()
            if after and after != self._last_clip:
                self._last_clip = after
                self.captured_text.emit(after)
                self.status.emit("Kopyalandı → eklendi.")
            else:
                self.status.emit("Seçili metin bulunamadı.")
        except Exception as e:
            self.status.emit(f"Hata (hotkey): {e}")


def safe_note_path(name: str) -> Path:
    n = (name or "Yeni Not").strip()
    if not n.lower().endswith(".txt"):
        n += ".txt"
    for ch in r'\/:*?"<>|':
        n = n.replace(ch, "_")
    return NOTES_DIR / n

def highlight_sidecar(p: Path) -> Path:
    # Notlar.txt -> Notlar.txt.highlights.json
    return p.with_suffix(p.suffix + ".highlights.json")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} • Üstünü Çiz → Ctrl+C → Kaydet")
        icon_file = Path.cwd() / "icon.png"
        if icon_file.exists():
            self.setWindowIcon(QIcon(str(icon_file)))

        self.current_file: Path | None = None
        self._block_text_changed = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(600)  # yazarken 0.6sn sonra otomatik kaydet

  
        self._last_paste = ""
        self._last_paste_ts = 0.0

        central = QWidget(self); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(10,10,10,10); root.setSpacing(8)

     
        top = QHBoxLayout(); top.setSpacing(8)
        self.btn_new = QPushButton("Ekle")
        self.btn_del = QPushButton("Sil")
        self.btn_ren = QPushButton("Yeniden Adlandır")
        self.btn_open_dir = QPushButton("Klasörü Aç")
        top.addWidget(self.btn_new); top.addWidget(self.btn_del); top.addWidget(self.btn_ren)
        top.addStretch(1); top.addWidget(self.btn_open_dir)

        self.btn_start = QPushButton("Kaydetmeye Başla")
        self.btn_stop = QPushButton("Durdur"); self.btn_stop.setEnabled(False)
        top.addWidget(self.btn_start); top.addWidget(self.btn_stop)
        root.addLayout(top)

        palette = QHBoxLayout(); palette.setSpacing(6)
        palette.addWidget(QLabel("Renk:"))
        self.palette_colors = [
            "#FFD700", "#FF6B6B", "#4ECDC4", "#1E90FF", "#FF8C00",
            "#ADFF2F", "#DA70D6", "#FF1493", "#40E0D0"
        ]
        for col in self.palette_colors:
            b = QPushButton(" ")
            b.setFixedSize(24, 32)
            b.setStyleSheet(f"background:{col}; border:1px solid #444;")
            b.clicked.connect(lambda _, c=col: self.apply_highlight(c))
            palette.addWidget(b)
        clear_btn = QPushButton("Temizle")
        clear_btn.setMinimumWidth(72)
        clear_btn.setFixedHeight(26)
        clear_btn.setStyleSheet("background:#2b2b2b; color:#fff; padding:2px 8px; border:1px solid #555; border-radius:6px;")
        clear_btn.clicked.connect(lambda: self.apply_highlight(None))
        palette.addWidget(clear_btn)
        palette.addStretch(1)
        root.addLayout(palette)

     
        mid = QHBoxLayout(); mid.setSpacing(10); root.addLayout(mid, 1)

        self.listw = NoteList()
        self.listw.setMinimumWidth(260)
        self.listw.itemSelectionChanged.connect(self.on_selection_changed)
        self.listw.setStyleSheet("""
            QListView::item:selected { background: #FFF59D; color: black; }
            QListView::item:selected:!active { background: #FFF59D; color: black; }
            QListWidget { background:#1b1b1b; border:1px solid #333; color:#eee; }
        """)
        mid.addWidget(self.listw, 0)

        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.editor.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.editor.textChanged.connect(self.on_text_changed)
        f = QFont("Consolas" if os.name=="nt" else "Monospace", 11)
        f.setStyleHint(QFont.Monospace)
        self.editor.setFont(f)
        mid.addWidget(self.editor, 1)


        self.status = QLabel("Hazır")
        root.addWidget(self.status)

       
        act_sep = QAction(self)
        act_sep.setShortcut(QKeySequence("Ctrl+I"))
        act_sep.triggered.connect(self.insert_separator)
        self.addAction(act_sep)

       
        undo_act = QAction(self)
        undo_act.setShortcut(QKeySequence.Undo)   # Ctrl+Z
        undo_act.triggered.connect(self.editor.undo)
        self.addAction(undo_act)

        redo_act = QAction(self)
        redo_act.setShortcut(QKeySequence.Redo)   # Ctrl+Y
        redo_act.triggered.connect(self.editor.redo)
        self.addAction(redo_act)

       
        self.editor.document().setUndoRedoEnabled(True)

     
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #121212; color: #eee; }
            QPlainTextEdit { background:#1b1b1b; border:1px solid #333; color:#eee; }
            QPushButton { background:#242424; color:#eee; border:1px solid #3a3a3a; padding:6px 10px; border-radius:6px; }
            QPushButton:hover { background:#2b2b2b; }
            QLabel { color:#bbb; }
            QListWidget::item { padding:6px; }
        """)

   
        self.btn_new.clicked.connect(self.create_note)
        self.btn_del.clicked.connect(self.delete_note)
        self.btn_ren.clicked.connect(self.rename_note)
        self.btn_open_dir.clicked.connect(lambda: webbrowser.open(NOTES_DIR.as_uri()))
        self.save_timer.timeout.connect(self.save_current)

        self.worker = CaptureWorker()
        self.worker.captured_text.connect(self.on_captured_text)
        self.worker.status.connect(self.on_status)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.on_stop)

        
        self._hl_ranges = []  # [{'start': int, 'length': int, 'color': '#RRGGBB', 'text': '...'}]

       
        self.reload_list()

  
    def _sanitize_ranges(self):
        doc_len = max(0, self.editor.document().characterCount() - 1)
        cleaned = []
        seen = set()
        for r in self._hl_ranges:
            try:
                s = int(r.get("start", -1))
                l = int(r.get("length", 0))
                col = r.get("color")
            except Exception:
                continue
            if s < 0 or l <= 0 or s >= doc_len:
                continue
            e = min(doc_len, s + l)
            l = e - s
            if l <= 0:
                continue
            key = (s, l, col)
            if key in seen:
                continue
            seen.add(key)
            t = r.get("text")
            if t is not None:
                cleaned.append({"start": s, "length": l, "color": col, "text": t})
            else:
                cleaned.append({"start": s, "length": l, "color": col})
        self._hl_ranges = cleaned

  
    def reload_list(self):
        self.listw.clear()
        files = sorted(NOTES_DIR.glob("*.txt"), key=lambda p: p.name.lower())
        for p in files:
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, str(p))
            self.listw.addItem(it)
        if files:
            self.listw.setCurrentRow(0)

    def current_item(self) -> QListWidgetItem | None:
        sel = self.listw.selectedItems()
        return sel[0] if sel else None


    def create_note(self):
        name, ok = QInputDialog.getText(self, "Yeni Not", "Dosya adı:")
        if not ok: return
        p = safe_note_path(name)
        if p.exists():
            QMessageBox.warning(self, "Uyarı", "Aynı adla bir not zaten var.")
            return
        p.write_text("", encoding="utf-8")
        highlight_sidecar(p).write_text("[]", encoding="utf-8")
        self.reload_list()
        for i in range(self.listw.count()):
            if Path(self.listw.item(i).data(Qt.UserRole)) == p:
                self.listw.setCurrentRow(i); break
        self.editor.setFocus()

    def delete_note(self):
        it = self.current_item()
        if not it: return
        p = Path(it.data(Qt.UserRole))
        if QMessageBox.question(self, "Sil", f"'{p.name}' silinsin mi?") != QMessageBox.Yes:
            return
        try:
            if self.current_file and self.current_file == p:
                self.current_file = None
            p.unlink(missing_ok=True)
            highlight_sidecar(p).unlink(missing_ok=True)
            self.reload_list()
            self.editor.clear()
            self._hl_ranges = []
            self.apply_all_highlights()
            self.status.setText("Silindi.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def rename_note(self):
        it = self.current_item()
        if not it: return
        old = Path(it.data(Qt.UserRole))
        name, ok = QInputDialog.getText(self, "Yeniden Adlandır", "Yeni dosya adı:", text=old.name)
        if not ok: return
        newp = safe_note_path(name)
        if newp.exists() and newp != old:
            QMessageBox.warning(self, "Uyarı", "Bu ad zaten kullanılıyor.")
            return
        try:
            old.rename(newp)
            old_hl = highlight_sidecar(old)
            if old_hl.exists():
                old_hl.rename(highlight_sidecar(newp))
            self.reload_list()
            for i in range(self.listw.count()):
                if Path(self.listw.item(i).data(Qt.UserRole)) == newp:
                    self.listw.setCurrentRow(i); break
            self.status.setText("Yeniden adlandırıldı.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def on_selection_changed(self):
        it = self.current_item()
        if not it:
            self.current_file = None
            self.editor.clear()
            self._hl_ranges = []
            self.apply_all_highlights()
            return
        p = Path(it.data(Qt.UserRole))
        self.current_file = p
        try:
            self._block_text_changed = True
            txt = p.read_text(encoding="utf-8")
            self.editor.setPlainText(txt)
            self._block_text_changed = False
            self.status.setText(f"Açıldı: {p.name}")
        except Exception as e:
            self.status.setText(f"Okuma hatası: {e}")
        self.load_highlights()

    def on_text_changed(self):
        if self._block_text_changed:
            return
        self.save_timer.start()

    def save_current(self):
        if not self.current_file:
            return
        try:
            txt = self.editor.toPlainText()
            self.current_file.write_text(txt, encoding="utf-8")
            highlight_sidecar(self.current_file).write_text(
                json.dumps(self._hl_ranges, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # self.editor.document().clearUndoRedoStacks()  
            self.status.setText("Otomatik kaydedildi.")
        except Exception as e:
            self.status.setText(f"Kaydetme hatası: {e}")

    def insert_separator(self):
        cur = self.editor.textCursor()
        cur.insertText(SEPARATOR)


    def on_start(self):
        self.worker.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText("Capture ON")

    def on_stop(self):
        self.worker.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status.setText("Capture OFF")

    def on_captured_text(self, text: str):
        if not self.current_file:
            self.status.setText("Önce soldan bir not dosyası seç / oluştur.")
            return
        now = time.time()
        if text == self._last_paste and (now - self._last_paste_ts) < 1.0:
            return
        self._last_paste, self._last_paste_ts = text, now

        # Tek undo adımı olarak ekle (undo/redo'yu kapatma!)
        cur = self.editor.textCursor()
        self.editor.blockSignals(True)
        cur.beginEditBlock()
        cur.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cur)

        content = self.editor.toPlainText()
        prefix = "\n\n" if content.strip() else ""
        cur.insertText(prefix + text + "\n\n")

        cur.endEditBlock()
        self.editor.blockSignals(False)

        self.save_current()
        self.status.setText("Panodan eklendi.")

    def on_status(self, s: str):
        self.status.setText(f"Durum: {s}")

    
    def apply_highlight(self, color_hex: str | None):
        if not self.current_file:
            return
        c = self.editor.textCursor()
        if not c.hasSelection():
            self.status.setText("Önce metni seç.")
            return

        start = c.selectionStart()
        end = c.selectionEnd()

        doc_len = max(0, self.editor.document().characterCount() - 1)
        start = max(0, min(start, doc_len))
        end = max(start, min(end, doc_len))
        length = end - start
        if length == 0:
            return

        if color_hex is None:
            ns, ne = start, end
            self._hl_ranges = [
                r for r in self._hl_ranges
                if not (max(ns, r["start"]) < min(ne, r["start"] + r["length"]))
            ]
        else:
            dup = any(
                (r.get("start") == start and r.get("length") == length and r.get("color") == color_hex)
                for r in self._hl_ranges
            )
            if not dup:
                selected_text = c.selectedText()
                self._hl_ranges.append({
                    "start": start,
                    "length": length,
                    "color": color_hex,
                    "text": selected_text
                })

        self.apply_all_highlights()
        self.save_current()

    def apply_all_highlights(self):
        self._sanitize_ranges()

        sels = []
        doc_len = max(0, self.editor.document().characterCount() - 1)

        for r in self._hl_ranges:
            start = max(0, min(doc_len, r["start"]))
            end = max(start, min(doc_len, start + r["length"]))
            if end <= start:
                continue

            # Python dilimi yerine Qt cursor + selectedText()
            qc = self.editor.textCursor()
            qc.setPosition(start)
            qc.setPosition(end, QTextCursor.KeepAnchor)
            cur_text_qt = qc.selectedText()
            expected = r.get("text")
            if expected is not None and cur_text_qt != expected:
                continue  # metin değişmiş/kaymış → uygulama

            fmt = QTextCharFormat()
            if "color" in r and r["color"]:
                fmt.setBackground(QColor(r["color"]))
                fmt.setForeground(QColor("black"))
                fmt.setUnderlineStyle(QTextCharFormat.SingleUnderline)
                fmt.setUnderlineColor(QColor(r["color"]))

            sel = QTextEdit.ExtraSelection()
            sel.cursor = qc
            sel.format = fmt
            sels.append(sel)

        self.editor.setExtraSelections(sels)

    def load_highlights(self):
        self._hl_ranges = []
        if self.current_file:
            side = highlight_sidecar(self.current_file)
            if side.exists():
                try:
                    self._hl_ranges = json.loads(side.read_text(encoding="utf-8"))
                except Exception:
                    self._hl_ranges = []
        self._sanitize_ranges()
        self.apply_all_highlights()

# ---------- main ----------
def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(1050, 700)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
