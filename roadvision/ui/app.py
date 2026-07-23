from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from ..camera import Camera, CameraInfo
from ..config import APP_CONFIG, MODEL_SPECS, PerformanceProfile
from ..engine import EngineEvent, EngineState, ProcessingEngine
from ..logbook import EventJournal, LogLevel, LogRecord, SessionLogSink, create_default_journal
from ..media import create_default_recorder
from ..sources import SourceFactory, SourceKind


BG = "#0b1118"
PANEL = "#121c27"
PANEL_2 = "#172330"
TEXT = "#edf3f8"
MUTED = "#8fa2b5"
ACCENT = "#38d996"
ACCENT_DARK = "#163e33"
DANGER = "#ff6577"
BORDER = "#263646"
PREVIEW_PLACEHOLDER = "Kaynak seçildikten sonra çıktı burada görünecek"
MAX_VISIBLE_LOG_ROWS = 1000


class RoadVisionApp:
    def __init__(self, root: tk.Tk, journal: EventJournal | None = None) -> None:
        self.root = root
        self._journal = journal or create_default_journal()
        self._session_log_sink = SessionLogSink()
        self._journal.add_sink(self._session_log_sink)
        self._journal.prepare_journal()
        self.root.title(APP_CONFIG.title)
        self.root.geometry("1360x820")
        self.root.minsize(1080, 700)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._events: queue.Queue[EngineEvent] = queue.Queue()
        self._recorder = create_default_recorder(self._journal)
        self.engine = ProcessingEngine(
            self._events.put,
            journal=self._journal,
            recorder=self._recorder,
        )
        self._journal.app_event(
            LogLevel.INFO,
            "RoadVision başlatıldı.",
            build=APP_CONFIG.build,
            device=self.engine.device,
        )
        self._photo: ImageTk.PhotoImage | None = None
        self._last_display_frame = None
        self._camera_infos: list[CameraInfo] = []
        self._camera_scan_running = False
        self._active_run_id: int | None = None
        self._closing = False
        self._closed = False
        self._session_log_count = 0

        self.source_kind = tk.StringVar(value=SourceKind.CAMERA.value)
        self.source_path = tk.StringVar(value="")
        self.camera_value = tk.StringVar(value="Kameralar taranıyor…")
        self.status_text = tk.StringVar(value="Bir kaynak ve en az bir model seçin.")
        self.performance_text = tk.StringVar(value=f"Aygıt: {self.engine.device.upper()}")
        self.model_confidence_vars = {
            spec.id: tk.DoubleVar(value=APP_CONFIG.confidence) for spec in MODEL_SPECS
        }
        self.model_confidence_texts = {
            spec.id: tk.StringVar(value=f"%{APP_CONFIG.confidence * 100:.0f}")
            for spec in MODEL_SPECS
        }
        self.annotation_vars = {
            spec.id: tk.BooleanVar(value=True) for spec in MODEL_SPECS
        }
        profile_labels = {
            PerformanceProfile.SPEED: "Hızlı",
            PerformanceProfile.BALANCED: "Dengeli",
            PerformanceProfile.QUALITY: "Kalite",
        }
        self.performance_profile = tk.StringVar(value=profile_labels[APP_CONFIG.performance_profile])
        self.model_vars = {spec.id: tk.BooleanVar(value=False) for spec in MODEL_SPECS}

        self._configure_styles()
        self._build_layout()
        self._show_source_controls()
        self._update_start_availability()
        self.refresh_cameras()
        self.root.after(33, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Panel2.TFrame", background=PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Helvetica", 11))
        style.configure("Muted.TLabel", foreground=MUTED, background=PANEL, font=("Helvetica", 10))
        style.configure("Panel.TLabel", foreground=TEXT, background=PANEL, font=("Helvetica", 11))
        style.configure("Title.TLabel", foreground=TEXT, background=BG, font=("Helvetica", 22, "bold"))
        style.configure("Subtitle.TLabel", foreground=MUTED, background=BG, font=("Helvetica", 11))
        style.configure("Section.TLabel", foreground=TEXT, background=PANEL, font=("Helvetica", 12, "bold"))
        style.configure("Stat.TLabel", foreground=ACCENT, background=PANEL, font=("Helvetica", 10, "bold"))
        style.configure("ModelStat.TLabel", foreground=ACCENT, background=PANEL_2, font=("Helvetica", 9, "bold"))
        style.configure("TButton", background=PANEL_2, foreground=TEXT, borderwidth=0, padding=(12, 9), font=("Helvetica", 10, "bold"))
        style.map("TButton", background=[("active", BORDER), ("disabled", PANEL)], foreground=[("disabled", "#526273")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#08130f", padding=(16, 12), font=("Helvetica", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#65e7ad"), ("disabled", "#244238")])
        style.configure("Danger.TButton", background=DANGER, foreground="#1d080b", padding=(16, 12), font=("Helvetica", 11, "bold"))
        style.configure("TRadiobutton", background=PANEL, foreground=TEXT, font=("Helvetica", 10), padding=(0, 5))
        style.map("TRadiobutton", background=[("active", PANEL)], foreground=[("active", TEXT)])
        style.configure("TCheckbutton", background=PANEL_2, foreground=TEXT, font=("Helvetica", 10, "bold"), padding=(10, 9))
        style.map("TCheckbutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure("Model.TCheckbutton", background=PANEL_2, foreground=TEXT, font=("Helvetica", 9, "bold"), padding=(8, 4))
        style.map("Model.TCheckbutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure("Compact.TCheckbutton", background=PANEL_2, foreground=MUTED, font=("Helvetica", 8, "bold"), padding=(4, 3))
        style.map("Compact.TCheckbutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)])
        style.configure("Horizontal.TScale", background=PANEL, troughcolor=PANEL_2)
        style.configure("Model.Horizontal.TScale", background=PANEL_2, troughcolor=BORDER)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=PANEL_2,
            foreground=MUTED,
            padding=(16, 9),
            font=("Helvetica", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL)],
            foreground=[("selected", ACCENT)],
        )
        style.configure(
            "Log.Treeview",
            background="#05090d",
            fieldbackground="#05090d",
            foreground=TEXT,
            rowheight=25,
            borderwidth=0,
            font=("Helvetica", 9),
        )
        style.map(
            "Log.Treeview",
            background=[("selected", ACCENT_DARK)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Log.Treeview.Heading",
            background=PANEL_2,
            foreground=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
        )

    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, padding=(22, 18, 22, 12))
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text="ROADVISION", style="Title.TLabel").pack(anchor="w")
        device_badge = tk.Label(
            header,
            text=f"  {self.engine.device.upper()}  •  {APP_CONFIG.build}  ",
            bg=ACCENT_DARK,
            fg=ACCENT,
            font=("Helvetica", 10, "bold"),
            padx=8,
            pady=6,
        )
        device_badge.pack(side="right")

        body = ttk.Frame(self.root, padding=(22, 0, 22, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, minsize=360)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(body, style="Panel.TFrame", padding=18)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.sidebar.columnconfigure(0, weight=1)

        self._build_source_section(self.sidebar)
        self._separator(self.sidebar)
        self._build_model_section(self.sidebar)
        self._separator(self.sidebar)
        self._build_settings_section(self.sidebar)

        self.start_button = ttk.Button(self.sidebar, text="Başlat", style="Accent.TButton", command=self._toggle_processing)
        self.start_button.grid(row=9, column=0, sticky="ew", pady=(15, 0))

        self.content_tabs = ttk.Notebook(body)
        self.content_tabs.grid(row=0, column=1, sticky="nsew")

        preview_panel = ttk.Frame(self.content_tabs, style="Panel.TFrame", padding=12)
        self.content_tabs.add(preview_panel, text="Canlı Önizleme")
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(1, weight=1)

        preview_header = ttk.Frame(preview_panel, style="Panel.TFrame")
        preview_header.grid(row=0, column=0, sticky="ew", pady=(1, 10))
        ttk.Label(preview_header, text="Canlı Önizleme", style="Section.TLabel").pack(side="left")
        ttk.Label(preview_header, textvariable=self.performance_text, style="Stat.TLabel").pack(side="right")

        self.preview = tk.Label(
            preview_panel,
            bg="#05090d",
            fg=MUTED,
            text=PREVIEW_PLACEHOLDER,
            font=("Helvetica", 13),
            bd=0,
            anchor="center",
        )
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._on_preview_resize)

        status_bar = ttk.Frame(preview_panel, style="Panel.TFrame")
        status_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.status_dot = tk.Label(status_bar, text="●", bg=PANEL, fg=MUTED, font=("Helvetica", 10))
        self.status_dot.pack(side="left", padx=(2, 8))
        ttk.Label(status_bar, textvariable=self.status_text, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

        self._build_log_panel()

    def _build_log_panel(self) -> None:
        log_panel = ttk.Frame(self.content_tabs, style="Panel.TFrame", padding=12)
        self.content_tabs.add(log_panel, text="Oturum Günlüğü")
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(1, weight=1)

        header = ttk.Frame(log_panel, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(1, 10))
        ttk.Label(header, text="Anlık Oturum Kayıtları", style="Section.TLabel").pack(side="left")
        self.log_count_text = tk.StringVar(value="0 kayıt")
        ttk.Button(header, text="Ekranı Temizle", command=self._clear_session_logs).pack(side="right")
        ttk.Label(header, textvariable=self.log_count_text, style="Stat.TLabel").pack(
            side="right", padx=(0, 12)
        )

        table = ttk.Frame(log_panel, style="Panel.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        columns = ("time", "level", "category", "run", "model", "message", "details")
        self.log_tree = ttk.Treeview(
            table,
            columns=columns,
            show="headings",
            style="Log.Treeview",
            selectmode="browse",
        )
        headings = {
            "time": "Saat",
            "level": "Seviye",
            "category": "Kategori",
            "run": "Run",
            "model": "Model",
            "message": "Mesaj",
            "details": "Ayrıntılar",
        }
        widths = {
            "time": (95, False),
            "level": (75, False),
            "category": (85, False),
            "run": (55, False),
            "model": (120, False),
            "message": (320, True),
            "details": (300, True),
        }
        for column in columns:
            self.log_tree.heading(column, text=headings[column])
            width, stretch = widths[column]
            self.log_tree.column(column, width=width, minwidth=width, stretch=stretch)
        self.log_tree.tag_configure(LogLevel.DEBUG.value, foreground=MUTED)
        self.log_tree.tag_configure(LogLevel.INFO.value, foreground=TEXT)
        self.log_tree.tag_configure(LogLevel.WARNING.value, foreground="#ffd166")
        self.log_tree.tag_configure(LogLevel.ERROR.value, foreground=DANGER)

        vertical = ttk.Scrollbar(table, orient="vertical", command=self.log_tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.log_tree.xview)
        self.log_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        ttk.Label(
            log_panel,
            text="Bu ekran yalnız mevcut oturumu gösterir; JSONL dosya kaydı arka planda sürer.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

    def _build_source_section(self, parent: ttk.Frame) -> None:
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=0, column=0, sticky="ew")
        section.columnconfigure(0, weight=1)
        ttk.Label(section, text="1  •  Girdi Kaynağı", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        radio_row = ttk.Frame(section, style="Panel.TFrame")
        radio_row.grid(row=1, column=0, sticky="ew")
        for column, (label, value) in enumerate((("Kamera", "camera"), ("Fotoğraf", "image"), ("Video", "video"))):
            ttk.Radiobutton(
                radio_row,
                text=label,
                value=value,
                variable=self.source_kind,
                command=self._on_source_kind_change,
            ).grid(row=0, column=column, padx=(0, 12))

        self.camera_controls = ttk.Frame(section, style="Panel.TFrame")
        self.camera_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.camera_controls.columnconfigure(0, weight=1)
        self.camera_combo = ttk.Combobox(self.camera_controls, textvariable=self.camera_value, state="readonly")
        self.camera_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.camera_combo.bind("<<ComboboxSelected>>", self._on_camera_selection)
        self.refresh_button = ttk.Button(self.camera_controls, text="Yenile", command=self.refresh_cameras)
        self.refresh_button.grid(row=0, column=1)

        self.file_controls = ttk.Frame(section, style="Panel.TFrame")
        self.file_controls.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.file_controls.columnconfigure(0, weight=1)
        self.file_label = tk.Label(
            self.file_controls,
            text="Henüz dosya seçilmedi",
            bg=PANEL_2,
            fg=MUTED,
            anchor="w",
            padx=10,
            pady=9,
            font=("Helvetica", 9),
        )
        self.file_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(self.file_controls, text="Dosya Seç", command=self._choose_file).grid(row=0, column=1)

    def _build_model_section(self, parent: ttk.Frame) -> None:
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=2, column=0, sticky="ew")
        section.columnconfigure(0, weight=1)
        title_row = ttk.Frame(section, style="Panel.TFrame")
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(title_row, text="2  •  Modeller", style="Section.TLabel").pack(side="left")
        ttk.Button(title_row, text="Tümünü seç", command=self._select_all_models).pack(side="right")

        list_box = ttk.Frame(section, style="Panel.TFrame")
        list_box.grid(row=1, column=0, sticky="ew")
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)

        self.model_canvas = tk.Canvas(
            list_box,
            height=300,
            bg=PANEL,
            bd=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.model_canvas.grid(row=0, column=0, sticky="nsew")
        model_scrollbar = ttk.Scrollbar(
            list_box,
            orient="vertical",
            command=self.model_canvas.yview,
        )
        model_scrollbar.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        self.model_canvas.configure(yscrollcommand=model_scrollbar.set)

        model_list = ttk.Frame(self.model_canvas, style="Panel.TFrame")
        model_list.columnconfigure(0, weight=1)
        model_window = self.model_canvas.create_window((0, 0), window=model_list, anchor="nw")
        model_list.bind(
            "<Configure>",
            lambda _event: self.model_canvas.configure(scrollregion=self.model_canvas.bbox("all")),
        )
        self.model_canvas.bind(
            "<Configure>",
            lambda event: self.model_canvas.itemconfigure(model_window, width=event.width),
        )
        self.root.bind_all("<MouseWheel>", self._on_model_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_model_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_model_mousewheel, add="+")

        for row, spec in enumerate(MODEL_SPECS):
            frame = ttk.Frame(model_list, style="Panel2.TFrame")
            frame.grid(row=row, column=0, sticky="ew", pady=3)
            frame.columnconfigure(0, weight=1)
            check = ttk.Checkbutton(
                frame,
                text=spec.display_name,
                variable=self.model_vars[spec.id],
                command=self._on_model_selection,
                style="Model.TCheckbutton",
            )
            check.grid(row=0, column=0, sticky="ew")
            annotation_label = "Maske göster" if spec.task == "semantic" else "Box göster"
            ttk.Checkbutton(
                frame,
                text=annotation_label,
                variable=self.annotation_vars[spec.id],
                command=lambda model_id=spec.id: self._on_annotation_change(model_id),
                style="Compact.TCheckbutton",
            ).grid(row=0, column=1, sticky="e", padx=(4, 8))
            detail = f"{spec.input_size}px  •  {'Segmentasyon' if spec.task == 'semantic' else 'Nesne tespiti'}"
            tk.Label(frame, text=detail, bg=PANEL_2, fg=MUTED, font=("Helvetica", 8)).grid(row=1, column=0, sticky="w", padx=(31, 4))
            ttk.Label(
                frame,
                textvariable=self.model_confidence_texts[spec.id],
                style="ModelStat.TLabel",
            ).grid(row=1, column=1, sticky="e", padx=(4, 10))
            confidence_slider = ttk.Scale(
                frame,
                from_=0.10,
                to=0.90,
                variable=self.model_confidence_vars[spec.id],
                command=lambda value, model_id=spec.id: self._on_model_confidence_preview(model_id, value),
                style="Model.Horizontal.TScale",
            )
            confidence_slider.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(31, 10), pady=(3, 7))
            confidence_slider.bind(
                "<ButtonRelease-1>",
                lambda _event, model_id=spec.id: self._on_model_confidence_commit(model_id),
            )
            confidence_slider.bind(
                "<KeyRelease>",
                lambda _event, model_id=spec.id: self._on_model_confidence_commit(model_id),
            )

    def _on_model_mousewheel(self, event: tk.Event) -> None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self.model_canvas:
            widget = getattr(widget, "master", None)
        if widget is not self.model_canvas:
            return

        button = getattr(event, "num", None)
        if button == 4:
            units = -1
        elif button == 5:
            units = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            units = -1 if delta > 0 else 1
        self.model_canvas.yview_scroll(units, "units")

    def _build_settings_section(self, parent: ttk.Frame) -> None:
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=4, column=0, sticky="ew")
        section.columnconfigure(0, weight=1)
        ttk.Label(section, text="3  •  Performans", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        profile_row = ttk.Frame(section, style="Panel.TFrame")
        profile_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        profile_row.columnconfigure(1, weight=1)
        ttk.Label(profile_row, text="Performans", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 10))
        profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.performance_profile,
            values=("Hızlı", "Dengeli", "Kalite"),
            state="readonly",
            width=12,
        )
        profile_combo.grid(row=0, column=1, sticky="ew")
        profile_combo.bind("<<ComboboxSelected>>", self._on_performance_profile)

    def _separator(self, parent: ttk.Frame) -> None:
        row = parent.grid_size()[1]
        tk.Frame(parent, height=1, bg=BORDER).grid(row=row, column=0, sticky="ew", pady=14)

    def _show_source_controls(self) -> None:
        if self.source_kind.get() == SourceKind.CAMERA.value:
            self.file_controls.grid_remove()
            self.camera_controls.grid()
        else:
            self.camera_controls.grid_remove()
            self.file_controls.grid()
        self._update_start_availability()

    def _on_source_kind_change(self) -> None:
        self.source_path.set("")
        self.file_label.configure(text="Henüz dosya seçilmedi", fg=MUTED)
        self._show_source_controls()
        self._reset_for_source_change("Kaynak türü değişti. Yeni kaynağı seçin.")

    def _on_camera_selection(self, _: tk.Event) -> None:
        self._reset_for_source_change("Kamera seçildi. İşlemi başlatabilirsiniz.")

    def _choose_file(self) -> None:
        if self.source_kind.get() == SourceKind.IMAGE.value:
            types = [("Fotoğraflar", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff"), ("Tüm dosyalar", "*.*")]
        else:
            types = [("Videolar", "*.mp4 *.avi *.mov *.mkv *.m4v *.webm"), ("Tüm dosyalar", "*.*")]
        path = filedialog.askopenfilename(title="Kaynak dosyayı seçin", filetypes=types)
        if path:
            self.source_path.set(path)
            name = Path(path).name
            self.file_label.configure(text=name, fg=TEXT)
            self._reset_for_source_change(f"{name} seçildi. İşlemi başlatabilirsiniz.")
        else:
            self._update_start_availability()

    def _reset_for_source_change(self, message: str) -> None:
        self._active_run_id = None
        if self.engine.state != EngineState.IDLE:
            self.engine.request_stop()
        self._discard_pending_events()
        self._clear_preview()
        self.performance_text.set(f"Aygıt: {self.engine.device.upper()}")
        self.status_text.set(message)
        self._set_running_ui(False)

    def _discard_pending_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break

    def _clear_preview(self) -> None:
        self._last_display_frame = None
        self._photo = None
        self.preview.configure(image="", text=PREVIEW_PLACEHOLDER)

    def refresh_cameras(self) -> None:
        if self._camera_scan_running:
            return
        if self.source_kind.get() == SourceKind.CAMERA.value:
            self._reset_for_source_change("Kameralar yeniden taranıyor…")
        self._camera_scan_running = True
        self._camera_infos = []
        self.camera_value.set("Kameralar taranıyor…")
        self.camera_combo.configure(values=())
        self.refresh_button.configure(state="disabled")
        self._update_start_availability()

        def scan() -> None:
            try:
                cameras = Camera.get_camera_indexes(APP_CONFIG.max_camera_index)
                self.root.after(0, self._finish_camera_scan, cameras, None)
            except Exception as exc:
                self.root.after(0, self._finish_camera_scan, [], str(exc))

        threading.Thread(target=scan, name="roadvision-camera-scan", daemon=True).start()

    def _finish_camera_scan(self, cameras: list[CameraInfo], error: str | None) -> None:
        self._camera_scan_running = False
        self._camera_infos = cameras
        values = [str(camera) for camera in cameras]
        self.camera_combo.configure(values=values)
        self.refresh_button.configure(state="normal")
        if values:
            self.camera_value.set(values[0])
            self.status_text.set(f"{len(values)} erişilebilir kamera bulundu.")
        else:
            self.camera_value.set("Erişilebilir kamera bulunamadı")
            self.status_text.set(error or "Kamera bulunamadı; fotoğraf veya video seçebilirsiniz.")
        self._update_start_availability()

    def _selected_models(self) -> set[str]:
        return {model_id for model_id, variable in self.model_vars.items() if variable.get()}

    def _select_all_models(self) -> None:
        should_select = not all(variable.get() for variable in self.model_vars.values())
        for variable in self.model_vars.values():
            variable.set(should_select)
        self._on_model_selection()

    def _on_model_selection(self) -> None:
        selected = self._selected_models()
        if self.engine.state in (EngineState.STARTING, EngineState.RUNNING):
            self.engine.update_models(selected)
        self._update_start_availability()

    def _on_model_confidence_preview(self, model_id: str, value: str) -> None:
        self.model_confidence_texts[model_id].set(f"%{float(value) * 100:.0f}")

    def _on_model_confidence_commit(self, model_id: str) -> None:
        self.engine.set_model_confidence(model_id, self.model_confidence_vars[model_id].get())

    def _on_annotation_change(self, model_id: str) -> None:
        enabled = self.annotation_vars[model_id].get()
        self.engine.set_annotation_enabled(model_id, enabled)
        spec = next(spec for spec in MODEL_SPECS if spec.id == model_id)
        if enabled:
            self.status_text.set(f"{spec.short_name} çizimi açıldı.")
        elif self.model_vars[model_id].get():
            self.status_text.set(f"{spec.short_name} çizimi gizlendi; tespit devam ediyor.")
        else:
            self.status_text.set(f"{spec.short_name} çizimi kapatıldı.")

    def _on_performance_profile(self, _: tk.Event) -> None:
        profiles = {
            "Hızlı": PerformanceProfile.SPEED,
            "Dengeli": PerformanceProfile.BALANCED,
            "Kalite": PerformanceProfile.QUALITY,
        }
        profile = profiles[self.performance_profile.get()]
        self.engine.set_performance_profile(profile)
        self.status_text.set(
            f"Performans profili: {self.performance_profile.get()}. Sonraki kareye uygulanacak."
        )

    def _source_is_ready(self) -> bool:
        kind = self.source_kind.get()
        if kind == SourceKind.CAMERA.value:
            return bool(self._camera_infos and self.camera_combo.current() >= 0)
        return bool(self.source_path.get() and Path(self.source_path.get()).is_file())

    def _update_start_availability(self) -> None:
        if self._closing or self._closed:
            self.start_button.configure(state="disabled")
            return
        if self.engine.state in (EngineState.STARTING, EngineState.RUNNING):
            self.start_button.configure(
                state="normal" if self._active_run_id is not None else "disabled"
            )
            return
        if self.engine.state != EngineState.IDLE:
            self.start_button.configure(state="disabled")
            return
        ready = self._source_is_ready() and bool(self._selected_models())
        self.start_button.configure(state="normal" if ready else "disabled")

    def _create_source(self):
        kind = self.source_kind.get()
        if kind == SourceKind.CAMERA.value:
            current = self.camera_combo.current()
            if current < 0 or current >= len(self._camera_infos):
                raise ValueError("Erişilebilir bir kamera seçin.")
            return SourceFactory.create_camera(
                self._camera_infos[current].index,
                APP_CONFIG.camera_width,
                APP_CONFIG.camera_height,
                APP_CONFIG.camera_fps,
            )
        path = self.source_path.get()
        if kind == SourceKind.IMAGE.value:
            return SourceFactory.create_image(path)
        return SourceFactory.create_video(path)

    def _toggle_processing(self) -> None:
        if self._closing or self._closed:
            return
        if self.engine.state in (EngineState.STARTING, EngineState.RUNNING):
            self.engine.request_stop()
            self.start_button.configure(
                text="Durduruluyor…",
                style="Danger.TButton",
                state="disabled",
            )
            self.status_text.set("İşlem durduruluyor…")
            self.status_dot.configure(fg="#ffd166")
            return
        if self.engine.state != EngineState.IDLE:
            self._set_running_ui(False)
            return
        try:
            selected = self._selected_models()
            if not selected:
                raise ValueError("Başlatmak için en az bir model seçin.")
            source = self._create_source()
            for model_id in selected:
                self.engine.set_model_confidence(
                    model_id,
                    self.model_confidence_vars[model_id].get(),
                )
                self.engine.set_annotation_enabled(
                    model_id,
                    self.annotation_vars[model_id].get(),
                )
            profiles = {
                "Hızlı": PerformanceProfile.SPEED,
                "Dengeli": PerformanceProfile.BALANCED,
                "Kalite": PerformanceProfile.QUALITY,
            }
            self.engine.set_performance_profile(profiles[self.performance_profile.get()])
            self._active_run_id = self.engine.start(source, selected)
            self._set_running_ui(True)
            self.status_text.set("Kaynak hazırlanıyor…")
            self.status_dot.configure(fg="#ffd166")
        except Exception as exc:
            self._active_run_id = None
            self._set_running_ui(False)
            messagebox.showerror("Başlatılamadı", str(exc), parent=self.root)

    def _set_running_ui(self, running: bool) -> None:
        if running:
            self.start_button.configure(text="Durdur", style="Danger.TButton", state="normal")
        else:
            self.start_button.configure(text="Başlat", style="Accent.TButton")
            self.status_dot.configure(fg=MUTED)
            self._update_start_availability()

    def _poll_events(self) -> None:
        self._poll_log_records()
        latest_frame: EngineEvent | None = None
        try:
            while not self._closed:
                event = self._events.get_nowait()
                if event.kind == "shutdown_complete":
                    self._handle_event(event)
                    continue
                if not self._event_belongs_to_active_run(event):
                    continue
                if event.kind == "frame":
                    latest_frame = event
                else:
                    self._handle_event(event)
        except queue.Empty:
            pass
        if self._closed:
            return
        if latest_frame is not None:
            self._handle_event(latest_frame)
        self._update_start_availability()
        if self.root.winfo_exists():
            self.root.after(33, self._poll_events)

    def _poll_log_records(self) -> None:
        sink = getattr(self, "_session_log_sink", None)
        tree = getattr(self, "log_tree", None)
        if sink is None or tree is None:
            return
        records = sink.drain()
        if not records:
            return
        for record in records:
            self._append_log_record(record)
        tree.yview_moveto(1.0)

    def _append_log_record(self, record: LogRecord) -> None:
        local_time = datetime.fromtimestamp(record.timestamp).astimezone().strftime("%H:%M:%S")
        details = json.dumps(record.payload, ensure_ascii=False, default=str) if record.payload else ""
        self.log_tree.insert(
            "",
            "end",
            values=(
                local_time,
                record.level.value.upper(),
                record.category.value,
                record.run_id if record.run_id is not None else "—",
                record.model_id or "—",
                record.message,
                details,
            ),
            tags=(record.level.value,),
        )
        self._session_log_count += 1
        if self._session_log_count > MAX_VISIBLE_LOG_ROWS:
            rows = self.log_tree.get_children()
            excess = self._session_log_count - MAX_VISIBLE_LOG_ROWS
            self.log_tree.delete(*rows[:excess])
            self._session_log_count -= excess
        self.log_count_text.set(f"{self._session_log_count} kayıt")

    def _clear_session_logs(self) -> None:
        self._session_log_sink.drain()
        rows = self.log_tree.get_children()
        if rows:
            self.log_tree.delete(*rows)
        self._session_log_count = 0
        self.log_count_text.set("0 kayıt")

    def _event_belongs_to_active_run(self, event: EngineEvent) -> bool:
        return self._active_run_id is not None and event.run_id == self._active_run_id

    def _handle_event(self, event: EngineEvent) -> None:
        if event.kind == "shutdown_complete":
            if self._closing and not self._closed:
                self._closed = True
                self._journal.release_journal()
                self.root.destroy()
            return
        if not self._event_belongs_to_active_run(event):
            return
        if self.engine.state == EngineState.STOPPING and event.kind not in {
            "error",
            "stopped",
        }:
            return

        if event.kind == "frame" and event.frame is not None:
            self._last_display_frame = event.frame
            self._display_frame(event.frame)
            details = "  ·  ".join(f"{stat.display_name}: {stat.object_count} ({stat.elapsed_ms:.0f} ms)" for stat in event.stats)
            self.performance_text.set(
                f"{event.inference_fps:.1f} FPS  •  Toplam {event.total_ms:.0f} ms"
                + (f"  •  {details}" if details else "")
            )
        elif event.kind == "started":
            self.status_text.set(f"{event.source_name} işleniyor. Model seçimini istediğiniz an değiştirebilirsiniz.")
            self.status_dot.configure(fg=ACCENT)
            self._set_running_ui(True)
        elif event.kind == "status":
            self.status_text.set(event.message)
        elif event.kind == "source_ended":
            self.status_text.set(event.message + " Son kare üzerinde model seçimini değiştirebilirsiniz.")
            self.status_dot.configure(fg="#ffd166")
        elif event.kind == "error":
            self._active_run_id = None
            self.status_text.set(event.message)
            self.status_dot.configure(fg=DANGER)
            self._set_running_ui(False)
            messagebox.showerror("İşlem hatası", event.message, parent=self.root)
        elif event.kind == "stopped":
            self._active_run_id = None
            self.status_text.set(event.message)
            self.performance_text.set(f"Aygıt: {self.engine.device.upper()}")
            self._set_running_ui(False)

    def _display_frame(self, frame) -> None:
        width = max(320, self.preview.winfo_width())
        height = max(240, self.preview.winfo_height())
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image=image)
        self.preview.configure(image=self._photo, text="")

    def _on_preview_resize(self, _: tk.Event) -> None:
        if self._last_display_frame is not None:
            self._display_frame(self._last_display_frame)

    def _on_close(self) -> None:
        if self._closing or self._closed:
            return
        self._closing = True
        self._active_run_id = None
        self._discard_pending_events()
        self.start_button.configure(text="Başlat", style="Accent.TButton", state="disabled")
        self.status_text.set("Uygulama kapatılıyor…")
        self.status_dot.configure(fg="#ffd166")
        self.engine.request_shutdown()


def run_app() -> None:
    root = tk.Tk()
    RoadVisionApp(root)
    root.mainloop()
