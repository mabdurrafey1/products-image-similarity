import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import webbrowser

from app_icon import set_app_icon
import match_image_ai
import generate_report
import noon_store

from PIL import Image, ImageTk
import re
import json

def get_config_path():
    home = os.path.expanduser("~")
    import platform
    sys_name = platform.system()
    if sys_name == "Darwin":
        dir_path = os.path.join(home, "Library", "Application Support", "DuplicateFinder")
    elif sys_name == "Windows":
        dir_path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder")
    else:
        dir_path = os.path.join(home, ".local", "share", "DuplicateFinder")
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, "config.json")

def load_config():
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config_data):
    config_path = get_config_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
    except Exception:
        pass

def _saved_store_choices():
    """The account's stores as {label: link}, as last read; none until Load Stores has read them.

    No store is written into this source. An empty box is the honest answer before the accounts have
    been read: a store offered without being read is a guess, and fetching one reads a catalog the
    signed-in account may not even own.
    """
    saved = load_config().get("noon_stores") or []
    return {entry["label"]: entry["url"] for entry in saved
            if entry.get("label") and entry.get("url")}


def get_default_global_image_dir():
    import platform
    home = os.path.expanduser("~")
    sys_name = platform.system()
    if sys_name == "Darwin":
        path = os.path.join(home, "Library", "Application Support", "DuplicateFinder", "downloaded_images")
    elif sys_name == "Windows":
        path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder", "downloaded_images")
    else:
        path = os.path.join(home, ".local", "share", "DuplicateFinder", "downloaded_images")
    return os.path.abspath(path)


PROGRESS_MARK = "progress_line"  # start of the log line that progress updates keep redrawing
TQDM_BAR_REGEX = re.compile(r"\s*\|[^|]*\|\s*")


class CustomStdout:
    def __init__(self, root, log_text, status_var, progress_bar):
        self.root = root
        self.log_text = log_text
        self.status_var = status_var
        self.progress_bar = progress_bar
        # Match something like " 50%|" or " 50/100" in tqdm progress line
        self.pct_regex = re.compile(r'(\d+)%')

    def write(self, text):
        self.root.after(0, self._safe_write, text)

    def _safe_write(self, text):
        # Progress updates (tqdm bars, download progress) start with \r and redraw the same line
        if '\r' in text:
            line = next((part.strip() for part in reversed(text.split('\r')) if part.strip()), "")
            if line:
                self._show_progress(line)
            return

        # Normal logs get written to the text widget, below the last progress line
        if PROGRESS_MARK in self.log_text.mark_names():
            self.log_text.mark_unset(PROGRESS_MARK)
            if not text.startswith('\n'):
                text = '\n' + text
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        
        # Check text for status updates
        clean_line = text.strip()
        if clean_line:
            if "checking images in the current directory for changes" in clean_line.lower():
                self.status_var.set("Scanning images for changes...")
            elif "Checking for missing images" in clean_line:
                self.status_var.set("Checking for missing database images...")
            elif "Starting download" in clean_line:
                self.status_var.set("Downloading missing database images...")
            elif "Initializing CLIP text encoder" in clean_line:
                self.status_var.set("Loading CLIP model text encoder...")
            elif "Performing text similarity search" in clean_line:
                self.status_var.set("Calculating semantic text matching...")
            elif "Attaching visual similarity scores" in clean_line:
                self.status_var.set("Applying rclip visual ranks...")

    def _show_progress(self, line):
        match = self.pct_regex.search(line)
        if match:
            percentage = int(match.group(1))
            if self.progress_bar["mode"] != "determinate":
                self.progress_bar.stop()
                self.progress_bar.config(mode="determinate")
            self.progress_bar["value"] = percentage
            if "download" in line.lower():
                self.status_var.set(f"Downloading images: {percentage}%...")
            else:
                self.status_var.set(f"Scanning & indexing images: {percentage}%...")

        # Drop tqdm's bar drawing (" 45%|████   | 4654/10342 [...]") and keep the numbers
        line = TQDM_BAR_REGEX.sub(" ", line).strip()
        if "images" in line and "download" not in line.lower():
            line = f"[Index] {line}"
        # Replace the previous progress line in place instead of adding a line per update
        if PROGRESS_MARK in self.log_text.mark_names():
            self.log_text.delete(PROGRESS_MARK, "end-1c")
        else:
            if not self.log_text.index("end-1c").endswith(".0"):
                self.log_text.insert(tk.END, "\n")
            self.log_text.mark_set(PROGRESS_MARK, "end-1c")
            self.log_text.mark_gravity(PROGRESS_MARK, "left")
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)

    def flush(self):
        pass

# Thread-safe stdout/stderr redirector to handle parallel execution logs correctly
class ThreadSafeStdoutRedirector:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.redirectors = {} # thread_id -> CustomStdout

    def write(self, message):
        tid = threading.get_ident()
        if tid in self.redirectors:
            self.redirectors[tid].write(message)
        elif self.original_stdout is not None:
            try:
                self.original_stdout.write(message)
            except Exception:
                pass

    def flush(self):
        for r in self.redirectors.values():
            try:
                r.flush()
            except Exception:
                pass
        if self.original_stdout is not None:
            try:
                self.original_stdout.flush()
            except Exception:
                pass

thread_safe_stdout = ThreadSafeStdoutRedirector(sys.stdout)
thread_safe_stderr = ThreadSafeStdoutRedirector(sys.stderr)
sys.stdout = thread_safe_stdout
sys.stderr = thread_safe_stderr

class SearchTab(ttk.Frame):
    def __init__(self, parent, tab_id, main_app):
        super().__init__(parent)
        self.tab_id = tab_id
        self.main_app = main_app
        self.is_running = False
        
        # Container frame inside tab
        main_frame = ttk.Frame(self)
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)
        
        # Form Card Frame
        form_card = ttk.LabelFrame(main_frame, text=f" Search Parameters (Tab #{self.tab_id}) ")
        form_card.pack(fill="x", pady=5, ipadx=10, ipady=10)
        
        # 1. Query Image Selection Row
        image_label = ttk.Label(form_card, text="Query Image:")
        image_label.grid(row=0, column=0, sticky="w", padx=10, pady=10)
        
        self.image_path_var = tk.StringVar()
        self.selected_images = []
        self.preview_photos = []
        
        # Frame to hold the horizontal list of thumbnails
        self.thumbnail_container = ttk.Frame(form_card)
        self.thumbnail_container.grid(row=0, column=1, padx=5, pady=10, sticky="w")
        
        # Placeholder label
        self.placeholder_label = ttk.Label(self.thumbnail_container, text="No images selected. Click Browse...", font=("Segoe UI", 9, "italic"))
        self.placeholder_label.pack(side="left", padx=5)
        
        browse_btn = ttk.Button(form_card, text="Browse...", command=self.browse_image)
        browse_btn.grid(row=0, column=2, padx=10, pady=10)
        
        # Trace variable changes to automatically update preview
        self.image_path_var.trace_add("write", lambda *args: self.update_preview())
        
        # 2. Query Title Row
        title_label = ttk.Label(form_card, text="Query Title:")
        title_label.grid(row=1, column=0, sticky="nw", padx=10, pady=10)
        
        # Standard tk.Text is kept because ttk doesn't have a Text widget
        self.title_text = tk.Text(form_card, height=3, width=40, font=("Segoe UI", 10))
        self.title_text.grid(row=1, column=1, columnspan=2, padx=10, pady=10, sticky="we")
        
        # 3. Input Source Row (multi-select), folded away when the log and results want the room
        self.sources_open = True
        self.sources_toggle = ttk.Button(form_card, text="▼ Input Source(s):", width=18,
                                         command=self.toggle_sources)
        self.sources_toggle.grid(row=2, column=0, sticky="nw", padx=10, pady=10)

        import glob
        excel_files = sorted(glob.glob("input_data/*.xlsx"))
        self.excel_options = [os.path.basename(f) for f in excel_files]

        source_container = ttk.Frame(form_card)
        source_container.grid(row=2, column=1, padx=10, pady=10, sticky="we")

        # What the toggle folds away; the noon store row below it stays, being used constantly
        self.sources_panel = ttk.Frame(source_container)
        self.sources_panel.pack(fill="x", expand=True)

        source_list_frame = ttk.Frame(self.sources_panel)
        source_list_frame.pack(fill="x", expand=True)

        self.source_listbox = tk.Listbox(source_list_frame, selectmode=tk.MULTIPLE, exportselection=False, height=4, font=("Segoe UI", 9))
        self.source_listbox.pack(side="left", fill="both", expand=True)

        source_scrollbar = ttk.Scrollbar(source_list_frame, orient="vertical", command=self.source_listbox.yview)
        source_scrollbar.pack(side="left", fill="y")
        self.source_listbox.config(yscrollcommand=source_scrollbar.set)

        self._populate_source_listbox()

        hint_row = ttk.Frame(self.sources_panel)
        hint_row.pack(fill="x", pady=(2, 0))

        source_hint = ttk.Label(hint_row, text="Click a file to select/deselect it — multiple files allowed", font=("Segoe UI", 8, "italic"))
        source_hint.pack(side="left")

        # Re-read input_data, for files added or fetched outside this window
        self.refresh_files_btn = ttk.Button(hint_row, text="↻ Refresh Files", command=self.refresh_excel_list)
        self.refresh_files_btn.pack(side="right")

        # Fetch a public noon store into input_data, or add the new arrivals of fetched stores
        store_row = self._store_row = ttk.Frame(source_container)
        store_row.pack(fill="x", pady=(8, 0))

        store_lbl = ttk.Label(store_row, text="Noon store:")
        store_lbl.pack(side="left", padx=(0, 5))

        # The stores come from the signed-in Seller Center account, not from a pasted link. Choosing one
        # is a dialog of its own: a flat box of every store of every account said nothing about which
        # account a store came from, and the row had grown to five controls.
        self.store_choices = _saved_store_choices()
        self.store_url_var = tk.StringVar()
        self._select_store(load_config().get("noon_store_url", ""))

        self.chosen_store_lbl = ttk.Label(store_row, textvariable=self.store_url_var, anchor="w",
                                          foreground="#333333")
        self.chosen_store_lbl.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.stores_btn = ttk.Button(store_row, text="Stores…", command=self.open_stores)
        self.stores_btn.pack(side="left", padx=(0, 5))

        # Each account signs into a Chrome profile of its own; the dialog adds, re-signs and removes them
        self.accounts_btn = ttk.Button(store_row, text="Accounts", command=self.open_accounts)
        self.accounts_btn.pack(side="left")

        # 3. Global Images Directory Row
        config = load_config()
        default_image_dir = config.get("image_dir", get_default_global_image_dir())
        self.image_dir_var = tk.StringVar(value=default_image_dir)

        img_dir_label = ttk.Label(form_card, text="Images Directory:")
        img_dir_label.grid(row=3, column=0, sticky="w", padx=10, pady=10)

        img_dir_entry = ttk.Entry(form_card, textvariable=self.image_dir_var, width=40)
        img_dir_entry.grid(row=3, column=1, padx=10, pady=10, sticky="we")

        img_dir_btn = ttk.Button(form_card, text="Browse...", command=self.browse_image_dir)
        img_dir_btn.grid(row=3, column=2, padx=10, pady=10, sticky="w")

        # 4. Price Range Row
        price_label = ttk.Label(form_card, text="Price Range:")
        price_label.grid(row=4, column=0, sticky="w", padx=10, pady=10)
        
        price_frame = ttk.Frame(form_card)
        price_frame.grid(row=4, column=1, columnspan=2, sticky="w", padx=10, pady=10)
        
        min_lbl = ttk.Label(price_frame, text="Min:")
        min_lbl.pack(side="left", padx=2)
        
        self.min_price_var = tk.StringVar(value="")
        min_entry = ttk.Entry(price_frame, textvariable=self.min_price_var, width=10)
        min_entry.pack(side="left", padx=5)
        
        max_lbl = ttk.Label(price_frame, text="Max:")
        max_lbl.pack(side="left", padx=2)
        
        self.max_price_var = tk.StringVar(value="")
        max_entry = ttk.Entry(price_frame, textvariable=self.max_price_var, width=10)
        max_entry.pack(side="left", padx=5)
        
        aed_lbl = ttk.Label(price_frame, text="AED", font=("Segoe UI", 9, "bold"))
        aed_lbl.pack(side="left", padx=5)
        
        # 5. Settings Row
        settings_frame = ttk.Frame(form_card)
        settings_frame.grid(row=5, column=0, columnspan=3, pady=10, sticky="w", padx=10)
        
        self.strict_var = tk.BooleanVar(value=False)
        strict_cb = ttk.Checkbutton(settings_frame, text="Strict Model Matching", variable=self.strict_var)
        strict_cb.pack(side="left", padx=5)
        
        self.no_indexing_var = tk.BooleanVar(value=False)
        no_indexing_cb = ttk.Checkbutton(settings_frame, text="Skip Image Index Check", variable=self.no_indexing_var)
        no_indexing_cb.pack(side="left", padx=5)
        
        top_lbl = ttk.Label(settings_frame, text="Limit Matches:")
        top_lbl.pack(side="left", padx=(10, 5))
        
        self.top_var = tk.StringVar(value="500")
        top_spinner = ttk.Spinbox(settings_frame, from_=5, to=2000, width=5, textvariable=self.top_var)
        top_spinner.pack(side="left", padx=5)

        workers_lbl = ttk.Label(settings_frame, text="Workers:")
        workers_lbl.pack(side="left", padx=(10, 5))

        self.workers_var = tk.StringVar(value="10")
        workers_spinner = ttk.Spinbox(settings_frame, from_=1, to=100, width=5, textvariable=self.workers_var)
        workers_spinner.pack(side="left", padx=5)

        # 6. Thresholds Row
        sim_label = ttk.Label(form_card, text="Match Thresholds:")
        sim_label.grid(row=6, column=0, sticky="w", padx=10, pady=10)

        sim_frame = ttk.Frame(form_card)
        sim_frame.grid(row=6, column=1, columnspan=2, sticky="we", padx=10, pady=10)

        # Text Match Threshold
        text_frame = ttk.Frame(sim_frame)
        text_frame.pack(side="left", fill="x", expand=True, padx=(0, 15))

        text_lbl = ttk.Label(text_frame, text="Text:")
        text_lbl.pack(side="left", padx=(0, 5))

        # Restore thresholds saved from a previous launch (clamped to slider ranges)
        saved_config = load_config()
        try:
            saved_text_sim = min(max(float(saved_config.get("text_threshold", 70.0)), 0.0), 100.0)
        except (TypeError, ValueError):
            saved_text_sim = 70.0
        try:
            saved_img_sim = min(max(float(saved_config.get("image_threshold", 0.20)), 0.0), 2.0)
        except (TypeError, ValueError):
            saved_img_sim = 0.20

        self.text_sim_var = tk.DoubleVar(value=saved_text_sim)
        self.sim_value_lbl = ttk.Label(text_frame, text=f"{saved_text_sim:.0f}%", font=("Segoe UI", 9, "bold"), width=5)

        def update_sim_lbl(val):
            self.sim_value_lbl.config(text=f"{float(val):.0f}%")

        self.sim_slider = ttk.Scale(text_frame, from_=0.0, to=100.0, variable=self.text_sim_var, orient="horizontal", command=update_sim_lbl)
        self.sim_slider.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.sim_value_lbl.pack(side="left")

        # Image Match Threshold
        img_frame = ttk.Frame(sim_frame)
        img_frame.pack(side="left", fill="x", expand=True)

        img_lbl = ttk.Label(img_frame, text="Image:")
        img_lbl.pack(side="left", padx=(0, 5))

        self.img_sim_var = tk.DoubleVar(value=saved_img_sim)
        self.img_sim_value_lbl = ttk.Label(img_frame, text=f"{saved_img_sim:.2f}", font=("Segoe UI", 9, "bold"), width=5)

        def update_img_sim_lbl(val):
            self.img_sim_value_lbl.config(text=f"{float(val):.2f}")

        self.img_sim_slider = ttk.Scale(img_frame, from_=0.0, to=2.0, variable=self.img_sim_var, orient="horizontal", command=update_img_sim_lbl)
        self.img_sim_slider.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.img_sim_value_lbl.pack(side="left")

        # Persist thresholds whenever they change (debounced so dragging doesn't spam writes)
        self._threshold_save_job = None
        self.text_sim_var.trace_add("write", self._schedule_threshold_save)
        self.img_sim_var.trace_add("write", self._schedule_threshold_save)

        # Progress Bar & Status Row
        self.progress_frame = ttk.Frame(main_frame)
        self.progress_frame.pack(fill="x", pady=10)
        
        self.status_var = tk.StringVar(value="Ready to start search.")
        self.status_lbl = ttk.Label(self.progress_frame, textvariable=self.status_var, font=("Segoe UI", 9, "italic"))
        self.status_lbl.pack(anchor="w", pady=2)
        
        # The bar says something is happening; the clock beside it says for how long
        progress_row = ttk.Frame(self.progress_frame)
        progress_row.pack(fill="x", pady=2)

        self.progress = ttk.Progressbar(progress_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)

        self.elapsed_var = tk.StringVar(value="")
        self.elapsed_lbl = ttk.Label(progress_row, textvariable=self.elapsed_var,
                                     font=("Segoe UI", 9), width=12, anchor="e")
        self.elapsed_lbl.pack(side="right", padx=(8, 0))

        self._clock_job = None      # the repeating tick, while something is running
        self._started_at = None
        
        # Action Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=5)
        
        self.run_btn = ttk.Button(btn_frame, text="Find Duplicate Listings", command=self.start_matching_thread)
        self.run_btn.pack(side="left", expand=True, fill="x", padx=5)
        
        self.view_btn = ttk.Button(btn_frame, text="View Results (HTML)", command=self.open_last_results)
        self.view_btn.pack(side="left", expand=True, fill="x", padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="🛑 Stop Execution", command=self.stop_matching, style="Stop.TButton")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=5)
        self.stop_btn.config(state="disabled")
        

        
        # Log Panel
        log_lbl = ttk.Label(main_frame, text="Execution Log:", font=("Segoe UI", 9, "bold"))
        log_lbl.pack(anchor="w", pady=(15, 2))
        
        log_frame = ttk.Frame(main_frame, borderwidth=1, relief="sunken")
        log_frame.pack(fill="both", expand=True, pady=5)
        
        # Text is kept as tk.Text (no ttk.Text exists)
        self.log_text = tk.Text(log_frame, font=("Consolas", 9), wrap="word", height=8)
        self.log_text.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        form_card.columnconfigure(1, weight=1)

    def _populate_source_listbox(self):
        self.source_listbox.delete(0, tk.END)
        if not self.excel_options:
            self.source_listbox.insert(tk.END, "(No Excel files found in input_data/)")
            self.source_listbox.config(state="disabled")
            return
        self.source_listbox.config(state="normal")
        for name in self.excel_options:
            self.source_listbox.insert(tk.END, name)
        # Select the first file by default
        self.source_listbox.selection_set(0)

    def get_selected_excel_files(self):
        if not self.excel_options:
            return []
        selected_indices = self.source_listbox.curselection()
        return [self.excel_options[i] for i in selected_indices if i < len(self.excel_options)]

    def refresh_excel_list(self):
        import glob
        excel_files = sorted(glob.glob("input_data/*.xlsx"))
        previous_selection = set(self.get_selected_excel_files())

        self.excel_options = [os.path.basename(f) for f in excel_files]
        self._populate_source_listbox()

        # Restore any previously selected files that are still present
        if previous_selection and self.excel_options:
            self.source_listbox.selection_clear(0, tk.END)
            for i, name in enumerate(self.excel_options):
                if name in previous_selection:
                    self.source_listbox.selection_set(i)

    def select_sources(self, paths):
        """Add these input files to the selection."""
        names = {os.path.basename(p) for p in paths}
        for i, name in enumerate(self.excel_options):
            if name in names:
                self.source_listbox.selection_set(i)
                self.source_listbox.see(i)

    def browse_image_dir(self):
        initial_dir = self.image_dir_var.get()
        if not os.path.exists(initial_dir):
            initial_dir = os.path.expanduser("~")
        selected = filedialog.askdirectory(initialdir=initial_dir, title="Select Global Images Folder")
        if selected:
            selected = os.path.abspath(selected)
            self.image_dir_var.set(selected)
            # Save configuration
            config = load_config()
            config["image_dir"] = selected
            save_config(config)

    def browse_image(self):
        file_paths = filedialog.askopenfilenames(
            title="Select Product Query Image(s)",
            filetypes=[
                ("Image Files", "*.png *.jpg *.jpeg *.webp *.gif *.avif *.heic *.bmp *.tif *.tiff"),
                ("RAW Images", "*.cr2 *.nef *.arw *.dng *.orf *.rw2 *.pef *.x3f"),
                ("All Files", "*.*")
            ]
        )
        if file_paths:
            for p in file_paths:
                if p not in self.selected_images:
                    self.selected_images.append(p)
            self.image_path_var.set(";".join(self.selected_images))

    def update_preview(self):
        for widget in self.thumbnail_container.winfo_children():
            widget.destroy()
        self.preview_photos.clear()

        paths_str = self.image_path_var.get().strip()
        paths = [p.strip() for p in paths_str.split(";") if p.strip()]
        self.selected_images = paths

        if not paths:
            self.placeholder_label = ttk.Label(self.thumbnail_container, text="No images selected. Click Browse...", font=("Segoe UI", 9, "italic"))
            self.placeholder_label.pack(side="left", padx=5)
            return

        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                item_frame = tk.Frame(self.thumbnail_container, width=72, height=72, bg="#dcdcdc")
                item_frame.pack_propagate(False)
                item_frame.pack(side="left", padx=6)

                img = Image.open(path)
                img.thumbnail((62, 62))
                photo = ImageTk.PhotoImage(img)
                self.preview_photos.append(photo)

                # Keep small overlay close tag as tk.Label to support custom coloring
                img_label = tk.Label(item_frame, image=photo, bg="white")
                img_label.pack(fill="both", expand=True, padx=1, pady=1)

                close_btn = tk.Label(
                    img_label, text="×", bg="#ff4d4d", fg="white",
                    font=("Segoe UI", 9, "bold"), cursor="hand2", bd=0
                )
                close_btn.bind("<Button-1>", lambda event, p=path: self.remove_image(p))
                close_btn.place(x=44, y=2, width=16, height=16)
            except Exception as e:
                print(f"Error rendering thumbnail for {path}: {e}")

    def remove_image(self, path):
        if path in self.selected_images:
            self.selected_images.remove(path)
            self.image_path_var.set(";".join(self.selected_images))

    def _schedule_threshold_save(self, *_):
        if self._threshold_save_job is not None:
            self.after_cancel(self._threshold_save_job)
        self._threshold_save_job = self.after(400, self._save_thresholds)

    def _save_thresholds(self):
        self._threshold_save_job = None
        try:
            text_val = round(float(self.text_sim_var.get()), 1)
            img_val = round(float(self.img_sim_var.get()), 2)
        except (tk.TclError, ValueError):
            return
        config = load_config()
        config["text_threshold"] = text_val
        config["image_threshold"] = img_val
        save_config(config)

    def open_last_results(self):
        if hasattr(self, 'last_report_path') and os.path.exists(self.last_report_path):
            webbrowser.open(f"file:///{os.path.abspath(self.last_report_path)}")
        else:
            messagebox.showwarning("No Results", "No generated reports HTML file was found. Run a search first.")

    def start_matching_thread(self):
        if self.is_running:
            return
            
        # Reset stop flag on new run
        match_image_ai.stop_requested = False
            
        query_image = self.image_path_var.get().strip()
        query_title = self.title_text.get("1.0", tk.END).strip()
        
        if not query_image:
            messagebox.showerror("Error", "Please select a product query image first.")
            return

        if not self.get_selected_excel_files():
            messagebox.showerror("Error", "Please select at least one input Excel file.")
            return

        image_paths = [p.strip() for p in query_image.split(";") if p.strip()]
        for p in image_paths:
            if not os.path.exists(p):
                messagebox.showerror("Error", f"Selected image path does not exist:\n{p}")
                return
            
        self.is_running = True
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id} ⏳")
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.start(10)
        self._start_clock()
        self.status_var.set("Initializing AI search model & calculating embeddings...")
        self.log_text.delete("1.0", tk.END)
        self.append_log(f"Starting AI Product Duplicate Finder (Tab #{self.tab_id})...\n")
        
        self.stop_event = threading.Event()
        thread = threading.Thread(target=self.run_matching_search, args=(query_image, query_title))
        thread.daemon = True
        thread.start()


    def toggle_sources(self):
        """Fold the input source list away, and unfold it again."""
        self.sources_open = not self.sources_open
        if self.sources_open:
            self.sources_panel.pack(fill="x", expand=True, before=self._store_row)
        else:
            self.sources_panel.pack_forget()
        self.sources_toggle.config(text=("▼ " if self.sources_open else "▶ ") + "Input Source(s):")

    def _start_clock(self):
        """Count the time this run is taking, beside the progress bar."""
        self._stop_clock(clear=False)   # never leave two ticks running
        self._started_at = time.time()
        self._tick()

    def _tick(self):
        if self._started_at is None:
            return
        spent = int(time.time() - self._started_at)
        self.elapsed_var.set(f"{spent // 60:d}:{spent % 60:02d} elapsed")
        self._clock_job = self.main_app.root.after(1000, self._tick)

    def _stop_clock(self, clear=True):
        """Stop counting, leaving the time the finished run took on screen."""
        if self._clock_job is not None:
            self.main_app.root.after_cancel(self._clock_job)
            self._clock_job = None
        if clear:
            self._started_at = None

    def append_log(self, text):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def run_matching_search(self, image_path, query_title):
        import argparse, datetime
        tid = threading.get_ident()
        redirector = CustomStdout(self.main_app.root, self.log_text, self.status_var, self.progress)

        # Register thread redirection
        thread_safe_stdout.redirectors[tid] = redirector
        thread_safe_stderr.redirectors[tid] = redirector

        try:
            os.makedirs("temp", exist_ok=True)
            selected_excel_names = self.get_selected_excel_files()
            excel_path = ";".join(os.path.join("input_data", name) for name in selected_excel_names)

            # Build args namespace directly — no sys.argv mutation needed
            min_p = self.min_price_var.get().strip()
            max_p = self.max_price_var.get().strip()
            args = argparse.Namespace(
                query=image_path,
                query_title=query_title,
                input=excel_path,
                output=f"temp/search_results_ai_{self.tab_id}.json",
                workers=int(self.workers_var.get()),
                top=int(self.top_var.get()),
                min_text_sim=self.text_sim_var.get() / 100.0,
                min_score=float(self.img_sim_var.get()),
                min_price=float(min_p) if min_p else None,
                max_price=float(max_p) if max_p else None,
                strict=bool(self.strict_var.get()),
                no_indexing=bool(self.no_indexing_var.get()),
                image_dir=self.image_dir_var.get().strip(),
            )

            self.main_app.root.after(0, self.status_var.set, "Running AI visual search...")

            # Run match_image_ai with per-tab stop event — fully parallel, no shared state
            match_image_ai.main(args=args, stop_event=self.stop_event)

            # Generate HTML report
            slug = re.sub(r'[^a-zA-Z0-9_-]', '_', query_title).strip('_')
            if not slug:
                slug = "search_results"
            slug = slug[:50]
            # Include the unique per-tab id (and microseconds) so concurrent tabs can
            # never resolve to the same report file. Without this, image-only searches
            # (blank title -> slug "search_results") finishing in the same second would
            # overwrite each other's report, making one tab display another tab's results.
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            report_filename = f"{slug}_tab{self.tab_id}_{timestamp}.html"
            report_path = os.path.join("reports", report_filename)
            os.makedirs("reports", exist_ok=True)
            self.last_report_path = report_path

            self.main_app.root.after(0, self.status_var.set, "Compiling search matches into HTML dashboard...")
            self.main_app.root.after(0, self.append_log, f"Generating {report_path} report...\n")

            query_images_list = [p.strip() for p in image_path.split(";") if p.strip()]
            generate_report.generate_html_report(
                json_path=f"temp/search_results_ai_{self.tab_id}.json",
                output_html=report_path,
                excel_path=excel_path,
                query_title=query_title,
                query_images=query_images_list
            )

            self.main_app.root.after(0, self.on_search_success)

        except Exception as e:
            self.main_app.root.after(0, lambda err=str(e): self.on_search_error(err))
        finally:
            thread_safe_stdout.redirectors.pop(tid, None)
            thread_safe_stderr.redirectors.pop(tid, None)
            self.is_running = False

    def stop_matching(self):
        if not self.is_running:
            return
        self.append_log("\n[Stop Request Received] Stopping...\n")
        self.status_var.set("Stopping execution...")
        # Signal only THIS tab's search — doesn't affect other running tabs
        self.stop_event.set()
        self.stop_btn.config(state="disabled")


    def on_search_success(self):
        self.progress.stop()
        self._stop_clock()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set(f"Search complete! Matches saved to {self.last_report_path}")
        self.append_log("\n[SUCCESS] AI Duplicate Finder completed successfully.\n")
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id} ✅")
        
        if messagebox.askyesno("Search Complete", f"AI search matching finished successfully for Tab #{self.tab_id}!\n\nWould you like to open the HTML results dashboard in your browser?"):
            self.open_last_results()

    def on_search_error(self, error_msg):
        self.progress.stop()
        self._stop_clock()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        
        if "StopRequested" in error_msg or "stopped by user" in error_msg:
            self.status_var.set("Execution stopped by user.")
            self.append_log("\n[STOPPED] Search execution was stopped by user.\n")
            self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id}")
        else:
            self.status_var.set("Error occurred during search matching.")
            self.append_log(f"\n[ERROR] Process failed:\n{error_msg}\n")
            self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id}")
            messagebox.showerror("Error During Matching", f"An error occurred:\n\n{error_msg}")

    def start_fetch_store(self):
        if self.is_running:
            return
        url = self.selected_store_link()
        if not url:
            messagebox.showerror("Error", "Pick a store first, or use Load Stores to read them from your noon account.")
            return
        try:
            existing = noon_store.find_listing(url, "input_data")
        except noon_store.StoreError as e:
            messagebox.showerror("Error", str(e))
            return
        if existing and not messagebox.askyesno(
                "Fetch Store", f"This store is already saved as '{os.path.basename(existing)}'.\n\n"
                               "Fetch all of its products again? Refresh only adds the new arrivals."):
            return
        config = load_config()
        config["noon_store_url"] = url
        save_config(config)
        self._start_store_task("Opening the noon store...", self._fetch_store, url)

    def _store_log(self, message):
        """Say it in the execution log, where it stays, as well as on the status line, where it doesn't."""
        self.append_log(f"{message}\n")
        self.status_var.set(message)

    def _log_from_worker(self, message):
        """The same, from the thread doing the reading -- tkinter is only ever touched on its own thread."""
        self.main_app.root.after(0, self._store_log, message)

    def _log_accounts(self):
        """Write out every noon account with the stores it holds, so the log says where each came from."""
        import noon_seller_stores          # imported here: the GUI starts without playwright

        views, orphans = noon_seller_stores.account_overview(self.store_choices)
        self.append_log(f"\nnoon accounts ({len(views)}):\n")
        for view in views:
            self.append_log(f"  {view.title} [{view.status}]\n")
            for label, link in view.stores:
                self.append_log(f"      {label}  {link}\n")
            if not view.stores:
                self.append_log("      (no stores read from it yet -- Load Stores reads them)\n")
        for label in orphans:
            # Saved with a store box from an account since removed: nothing here can fetch these
            self.append_log(f"  (belongs to no account here, cannot be fetched) {label}\n")
        self.append_log("\n")

    def _enable_store_box(self, enabled):
        """Let the store be changed, or don't, while something is reading one.

        The controls live in the Stores dialog now and are destroyed with it, so most of the time
        there are none to speak of -- and one that outlived its window raises if it is configured."""
        if getattr(self, "_stores_window", None) is None:
            return
        for widget in getattr(self, "_stores_widgets", []):
            if widget.winfo_exists():
                widget.config(state="normal" if enabled else "disabled")

    def _enable_account_controls(self, enabled):
        """The Accounts dialog's controls, which are destroyed with it and often aren't there at all."""
        if getattr(self, "_accounts_window", None) is None:
            return
        for widget in getattr(self, "_accounts_widgets", []):
            if widget.winfo_exists():
                widget.config(state="normal" if enabled else "disabled")

    def _redraw_stores(self):
        """Show the chosen store on the row, and redraw the dialog if it happens to be open."""
        self._select_store(self.selected_store_link())
        if getattr(self, "_stores_window", None) is not None:
            self._fill_stores()

    def _select_store(self, url):
        """Show the store with this link, or the first one the account offers."""
        for label, link in self.store_choices.items():
            if link == url:
                self.store_url_var.set(label)
                return
        self.store_url_var.set(next(iter(self.store_choices), ""))

    def selected_store_link(self):
        return self.store_choices.get(self.store_url_var.get().strip(), "")

    def start_load_stores(self):
        """Read the stores of the signed-in noon Seller Center account into the store box."""
        if self.is_running:
            return
        self._enable_store_box(False)
        self._store_log("Reading the stores of your noon accounts...")
        threading.Thread(target=self._load_stores, daemon=True).start()

    def _load_stores(self):
        import noon_seller_stores          # imported here: the GUI starts without playwright
        try:
            stores = noon_seller_stores.fetch_stores(log=self._log_from_worker)
            choices, error = {store.label: store.url for store in stores}, ""
        except Exception as e:
            choices, error = {}, str(e)
        self.main_app.root.after(0, self._stores_loaded, choices, error)

    def _stores_loaded(self, choices, error):
        self._enable_store_box(True)
        if error:
            self._store_log(f"[ERROR] The stores couldn't be read: {error}")
            messagebox.showerror("Load Stores", error)
            return
        chosen = self.selected_store_link()
        self.store_choices = choices
        self._select_store(chosen)
        self._redraw_stores()
        config = load_config()
        config["noon_stores"] = [{"label": label, "url": link} for label, link in choices.items()]
        save_config(config)
        self._store_log(f"{len(choices)} stores read from your noon accounts.")
        self._log_accounts()

    def start_add_account(self):
        """Sign into one more noon account, and add the stores it brings to the box."""
        if self.is_running:
            return
        if not messagebox.askokcancel(
                "Add Account",
                "A Chrome window will open for the new account.\n\n"
                "Sign in there with the noon account you want to add — signing in with an account "
                "that is already on the list changes nothing.\n\n"
                "Its stores join the list as soon as you are in."):
            return
        self.accounts_btn.config(state="disabled")
        self._enable_store_box(False)
        self._store_log("Opening a window to sign into the new noon account...")
        threading.Thread(target=self._add_account, daemon=True).start()

    def _add_account(self):
        import noon_seller_stores          # imported here: the GUI starts without playwright
        try:
            stores = noon_seller_stores.add_account(log=self._log_from_worker)
            added, error = {store.label: store.url for store in stores}, ""
        except Exception as e:
            added, error = {}, str(e)
        self.main_app.root.after(0, self._account_added, added, error)

    def _account_added(self, added, error):
        self.accounts_btn.config(state="normal")
        self._enable_store_box(True)
        if error:
            self._store_log(f"[ERROR] The account wasn't added: {error}")
            messagebox.showerror("Add Account", error)
            return
        chosen = self.selected_store_link()
        # The box offers stores, not accounts: the new account's join the ones already there, by name
        self.store_choices = dict(sorted({**self.store_choices, **added}.items(),
                                         key=lambda choice: choice[0].lower()))
        self._select_store(chosen)
        self._redraw_stores()
        config = load_config()
        config["noon_stores"] = [{"label": label, "url": link}
                                 for label, link in self.store_choices.items()]
        save_config(config)
        self._store_log(f"Account added: {len(added)} more stores to choose from.")
        self._log_accounts()

    def open_stores(self):
        """Choose the store to work on, shown under the account it belongs to."""
        if self.is_running:
            return
        if getattr(self, "_stores_window", None) is not None:
            self._stores_window.lift()
            return
        window = tk.Toplevel(self.main_app.root)
        self._stores_window = window
        window.title("noon Stores")
        window.transient(self.main_app.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_stores)

        self._stores_body = ttk.Frame(window, padding=12)
        self._stores_body.pack(fill="both", expand=True)
        self._fill_stores()

        # Centred on the main window, a little above the middle, where a dialog is looked for
        window.update_idletasks()
        root = self.main_app.root
        x = root.winfo_x() + (root.winfo_width() - window.winfo_width()) // 2
        y = root.winfo_y() + (root.winfo_height() - window.winfo_height()) // 3
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        # No grab: the dialog stays open while a fetch runs, and a grabbing window would swallow the
        # clicks meant for the log and the Stop button underneath it.

        # The stores were read once and written down, and reading them again costs a Chrome launch per
        # account to be told the same thing. They are asked for only when nothing was ever written
        # down -- after that it takes Reload, or noon turning us away, to ask again.
        if not self.store_choices and not getattr(self, "_stores_auto_loaded", False):
            self._stores_auto_loaded = True
            self.start_load_stores()

    def _close_stores(self):
        window, self._stores_window = getattr(self, "_stores_window", None), None
        self._stores_widgets = []
        if window is not None:
            window.destroy()

    def _fill_stores(self):
        """Draw the stores, grouped under their accounts. Drawn again whenever the stores change."""
        import noon_seller_stores          # imported here: the GUI starts without playwright

        for child in self._stores_body.winfo_children():
            child.destroy()
        self._stores_widgets = []
        views, orphans = noon_seller_stores.account_overview(self.store_choices)

        ttk.Label(self._stores_body, font=("Segoe UI", 9, "italic"), wraplength=560,
                  text="These are the stores your accounts were read to hold. Reload only if they have "
                       "changed. Refresh adds the new arrivals of every store already fetched."
                  ).pack(anchor="w", pady=(0, 10))

        if not any(view.stores for view in views):
            ttk.Label(self._stores_body, text="No stores yet — Load Stores reads them from your "
                                              "accounts.").pack(anchor="w")

        # Ticked stores survive a redraw: the dialog is drawn again whenever the stores change, and a
        # tick lost to that would quietly drop a store out of the refresh somebody had just asked for.
        ticked = getattr(self, "_store_ticks", {})
        first_draw = not ticked
        chosen = self.store_url_var.get()
        self._store_ticks = {}

        for view in views:
            if not view.stores:
                continue
            ttk.Label(self._stores_body, text=view.title,
                      font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
            for label, _ in view.stores:
                row = ttk.Frame(self._stores_body)
                row.pack(fill="x", padx=(14, 0))
                # One control per store: the tick is the choice. It says which stores Fetch and Refresh
                # act on -- several at a time -- and the first of them is the store the search runs
                # against, so there is nothing else to set and no second control to disagree with.
                # On the first draw the store already saved comes up ticked, so the dialog opens
                # showing what the row behind it says.
                tick = tk.BooleanVar(value=bool(ticked[label].get()) if label in ticked
                                     else (first_draw and label == chosen))
                self._store_ticks[label] = tick
                # The link is looked up by label, so the label is what the choice carries
                box = ttk.Checkbutton(row, text=label, variable=tick,
                                      command=self._store_tick_changed)
                box.pack(side="left")
                # A store with no workbook yet cannot be refreshed, only fetched. Saying so here is the
                # whole answer to "why did two of my four stores refresh?" -- the other two were never
                # fetched, and Refresh had nothing of theirs to add to.
                if not self._has_listing(label):
                    ttk.Label(row, text="not fetched yet", font=("Segoe UI", 8),
                              foreground="#777777").pack(side="left", padx=(6, 0))
                self._stores_widgets.append(box)

        if orphans:
            # Saved with a store box from an account since removed: nothing can fetch these
            ttk.Label(self._stores_body, font=("Segoe UI", 8), foreground="#b06000", wraplength=560,
                      text=f"{len(orphans)} store(s) belong to no account here and cannot be fetched: "
                           f"{', '.join(orphans)}").pack(anchor="w", pady=(10, 0))

        footer = ttk.Frame(self._stores_body)
        footer.pack(fill="x", pady=(14, 0))
        load = ttk.Button(footer, text="↻ Reload from noon", command=self._load_from_dialog)
        load.pack(side="left")
        fetch = ttk.Button(footer, text="Fetch Store", command=self._fetch_from_dialog)
        fetch.pack(side="left", padx=(5, 0))
        refresh = ttk.Button(footer, text="↻ Refresh", command=self._refresh_from_dialog)
        refresh.pack(side="left", padx=(5, 0))
        self._stores_widgets += [load, fetch, refresh]
        ttk.Button(footer, text="Close", command=self._close_stores).pack(side="right")

        # Drawn again whenever the stores change, which can happen while a task is still running:
        # freshly built controls start enabled, and would offer to start a second one.
        self._enable_store_box(not self.is_running)

    def _has_listing(self, label):
        """Whether this store has been fetched -- i.e. whether a workbook of it is saved to refresh."""
        link = self.store_choices.get(label, "")
        try:
            return bool(link) and bool(noon_store.find_listing(link, "input_data"))
        except Exception:
            return False   # a link that isn't a store has no listing, which is all this asks

    def _ticked_stores(self):
        """The labels of the ticked stores, in the order they are drawn."""
        return [label for label, tick in getattr(self, "_store_ticks", {}).items() if tick.get()]

    def _store_tick_changed(self):
        """Point the search at the first ticked store, so the tick is the only choice there is.

        The row behind the dialog shows this, and the search and the saved setting read it. Untick
        everything and it falls back to the store already chosen rather than to nothing, since the
        search still has to run against something.
        """
        ticked = self._ticked_stores()
        if ticked:
            self.store_url_var.set(ticked[0])

    def _load_from_dialog(self):
        """The dialog stays open while the work runs: its controls go dead, not the window itself."""
        self.start_load_stores()

    def _fetch_from_dialog(self):
        """Fetch every ticked store, one after another."""
        labels = self._ticked_stores()
        if not labels:
            messagebox.showinfo("Fetch Store", "Tick the stores to fetch first.")
            return
        links = [self.store_choices[label] for label in labels if label in self.store_choices]
        self._start_store_task(f"Fetching {len(links)} store(s)...", self._fetch_stores, links)

    def _refresh_from_dialog(self):
        """Refresh the ticked stores, and say plainly which of them there was nothing saved to refresh.

        Refresh adds new arrivals to a workbook that already exists; a store nobody has fetched has no
        workbook, so it is named here rather than passed over in silence.
        """
        labels = self._ticked_stores()
        if not labels:
            messagebox.showinfo("Refresh", "Tick the stores to refresh first.")
            return
        never = [label for label in labels if not self._has_listing(label)]
        paths = [noon_store.find_listing(self.store_choices[label], "input_data")
                 for label in labels if self._has_listing(label)]
        if not paths:
            messagebox.showinfo("Refresh", "None of the ticked stores has been fetched yet, so there "
                                           "is nothing saved to add new arrivals to. Use Fetch Store "
                                           "first:\n\n" + "\n".join(never))
            return
        if never:
            self._store_log(f"Not fetched yet, so nothing to refresh: {', '.join(never)}.")
        self._start_store_task(f"Checking {len(paths)} store(s) for new arrivals...",
                               self._refresh_listings, paths)

    def open_accounts(self):
        """Show the noon accounts: what each holds, and how to add, re-sign or remove one."""
        if self.is_running:
            return
        if getattr(self, "_accounts_window", None) is not None:
            self._accounts_window.lift()
            return
        window = tk.Toplevel(self.main_app.root)
        self._accounts_window = window
        window.title("noon Accounts")
        window.transient(self.main_app.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_accounts)

        self._accounts_body = ttk.Frame(window, padding=12)
        self._accounts_body.pack(fill="both", expand=True)
        self._fill_accounts()

        # Centred on the main window, a little above the middle, where a dialog is looked for
        window.update_idletasks()
        root = self.main_app.root
        x = root.winfo_x() + (root.winfo_width() - window.winfo_width()) // 2
        y = root.winfo_y() + (root.winfo_height() - window.winfo_height()) // 3
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        # No grab: a sign-in runs for minutes with the dialog still up, and a grabbing window would
        # swallow the clicks meant for the log and the Stop button underneath it.

    def _close_accounts(self):
        window, self._accounts_window = getattr(self, "_accounts_window", None), None
        self._accounts_widgets = []
        if window is not None:
            window.destroy()

    def _fill_accounts(self):
        """Draw a row per account. Drawn again after a removal, so the list stays true."""
        import noon_seller_stores          # imported here: the GUI starts without playwright

        for child in self._accounts_body.winfo_children():
            child.destroy()
        self._accounts_widgets = []
        views, orphans = noon_seller_stores.account_overview(self.store_choices)

        ttk.Label(self._accounts_body, font=("Segoe UI", 9, "italic"),
                  text="Each account signs into a Chrome window of its own. "
                       "Removing one deletes its sign-in from this computer.").pack(anchor="w", pady=(0, 10))

        if not views:
            ttk.Label(self._accounts_body, text="No noon account yet — Add Account opens a window "
                                                "to sign into one.").pack(anchor="w")

        for view in views:
            row = ttk.Frame(self._accounts_body)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=view.title, width=42, anchor="w").pack(side="left")
            ttk.Label(row, text=view.status, width=14, anchor="w",
                      font=("Segoe UI", 9)).pack(side="left")
            remove = ttk.Button(row, text="Remove", width=9,
                                command=lambda v=view: self._remove_account(v))
            remove.pack(side="right", padx=(5, 0))
            again = ttk.Button(row, text="Sign in again", width=13,
                               command=lambda v=view: self._sign_in_again(v))
            again.pack(side="right")
            self._accounts_widgets += [remove, again]

        if orphans:
            # Saved with a store box from an account since removed: nothing can fetch these
            ttk.Label(self._accounts_body, font=("Segoe UI", 8), foreground="#b06000", wraplength=560,
                      text=f"{len(orphans)} store(s) belong to no account here and cannot be fetched: "
                           f"{', '.join(orphans)}").pack(anchor="w", pady=(10, 0))

        footer = ttk.Frame(self._accounts_body)
        footer.pack(fill="x", pady=(14, 0))
        add = ttk.Button(footer, text="Add Account", command=self._add_from_dialog)
        add.pack(side="left")
        self._accounts_widgets.append(add)
        ttk.Button(footer, text="Close", command=self._close_accounts).pack(side="right")

        # Drawn again whenever the accounts change, which can happen while a task is still running:
        # freshly built controls start enabled, and would offer to start a second one.
        self._enable_account_controls(not self.is_running)

    def _add_from_dialog(self):
        """The dialog stays open while the sign-in runs: its controls go dead, not the window itself."""
        self.start_add_account()

    def _sign_in_again(self, view):
        if self.is_running:
            return
        self.accounts_btn.config(state="disabled")
        self._enable_account_controls(False)
        self._store_log(f"Opening a window to sign into {view.title}...")
        threading.Thread(target=self._do_sign_in, args=(view.profile,), daemon=True).start()

    def _do_sign_in(self, profile):
        import noon_seller_stores

        try:
            stores = noon_seller_stores.sign_in_account(profile, log=self._log_from_worker)
            added, error = {store.label: store.url for store in stores}, ""
        except Exception as e:
            added, error = {}, str(e)
        self.main_app.root.after(0, self._account_added, added, error)

    def _remove_account(self, view):
        import noon_seller_stores

        held = ", ".join(label for label, _ in view.stores) or "no stores yet"
        if not messagebox.askokcancel(
                "Remove Account",
                f"Remove {view.title}?\n\nIts saved sign-in is deleted from this computer and its "
                f"stores ({held}) come off the list.\n\nThis cannot be undone — getting the account "
                f"back means signing into it again.", parent=self._accounts_window):
            return
        noon_seller_stores.remove_account(view.profile)
        for label, _ in view.stores:
            self.store_choices.pop(label, None)
        self._redraw_stores()
        config = load_config()
        config["noon_stores"] = [{"label": label, "url": link}
                                 for label, link in self.store_choices.items()]
        save_config(config)
        self._store_log(f"{view.title} removed, along with its sign-in and {len(view.stores)} stores.")
        self._log_accounts()
        self._fill_accounts()

    def start_refresh_listing(self):
        if self.is_running:
            return
        self.refresh_excel_list()
        paths = [os.path.join("input_data", name) for name in self.excel_options]
        paths = [path for path in paths if noon_store.is_listing(path)]
        if not paths:
            messagebox.showinfo("Refresh", "No noon stores saved yet. Use Fetch Store to add one first.")
            return
        self._start_store_task("Checking stores for new arrivals...", self._refresh_listings, paths)

    def _start_store_task(self, status, task, argument):
        self.is_running = True
        self.stop_event = threading.Event()
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id} ⏳")
        for button in (self.run_btn, self.stores_btn,
                       self.accounts_btn, self.refresh_files_btn):
            button.config(state="disabled")
        # The dialog is left standing so its log can be watched, but nothing in it can be started twice
        self._enable_store_box(False)
        self.stop_btn.config(state="normal")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)
        self._start_clock()
        self.status_var.set(status)
        self.log_text.delete("1.0", tk.END)
        thread = threading.Thread(target=self._run_store_task, args=(task, argument))
        thread.daemon = True
        thread.start()

    def _run_store_task(self, task, argument):
        tid = threading.get_ident()
        redirector = CustomStdout(self.main_app.root, self.log_text, self.status_var, self.progress)
        thread_safe_stdout.redirectors[tid] = redirector
        thread_safe_stderr.redirectors[tid] = redirector
        try:
            outcome = ("done",) + task(argument)
        except noon_store.StopRequested:
            outcome = ("stopped", "Stopped by user.", [])
        except Exception as e:
            outcome = ("error", str(e), [])
        finally:
            thread_safe_stdout.redirectors.pop(tid, None)
            thread_safe_stderr.redirectors.pop(tid, None)
            self.is_running = False
        self.main_app.root.after(0, self._on_store_task_done, *outcome)

    def _fetch_stores(self, urls):
        """Fetch several stores in turn. One that fails costs only itself, not the stores after it."""
        saved, failed = [], []
        for number, url in enumerate(urls, start=1):
            if self.stop_event.is_set():
                raise noon_store.StopRequested()
            print(f"Store {number} of {len(urls)}...")
            try:
                message, paths = self._fetch_store(url)
                print(message)
                saved += paths
            except noon_store.StopRequested:
                raise
            except Exception as e:
                failed.append(f"{url}: {e}")
                print(f"Skipped {url}: {e}")
        if failed and not saved:
            raise noon_store.StoreError("; ".join(failed))
        return f"Saved {len(saved)} store listing(s).", saved

    def _fetch_store(self, url):
        def show_progress(done, total):
            self.main_app.root.after(0, self._show_fetch_progress, done, total)
        result = noon_store.fetch_store(url, "input_data", on_progress=show_progress, should_stop=self.stop_event.is_set)
        return f"Saved {result.product_count:,} products from '{result.store_name}'.", [result.location]

    def _show_fetch_progress(self, done, total):
        if self.progress["mode"] != "determinate":
            self.progress.stop()
            self.progress.config(mode="determinate")
        self.progress.config(maximum=total, value=min(done, total))
        self.status_var.set(f"Fetching store products: {done:,} of {total:,}")

    def _refresh_listings(self, paths):
        listings = []
        for path in paths:
            if noon_store.is_listing(path):
                listings.append(path)
            else:
                print(f"Skipping '{os.path.basename(path)}': it wasn't made by Fetch Store.")
        if not listings:
            raise noon_store.StoreError("None of the selected files is a noon store. Use Fetch Store to add one first.")
        # Every store goes through one browser: starting it is almost the whole cost of a refresh.
        results = noon_store.refresh_stores(listings, should_stop=self.stop_event.is_set)
        refreshed = [result.location for result in results]
        added = sum(result.added for result in results)
        return f"Added {added:,} new products to {len(refreshed)} store listing(s).", refreshed

    def _on_store_task_done(self, outcome, message, paths):
        self.progress.stop()
        self._stop_clock()
        self.progress.config(mode="indeterminate", maximum=100, value=0)  # searches report progress out of 100
        for button in (self.run_btn, self.stores_btn,
                       self.accounts_btn, self.refresh_files_btn):
            button.config(state="normal")
        self._enable_store_box(True)
        self.stop_btn.config(state="disabled")
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id}")
        self.status_var.set(message)
        if outcome == "error":
            self.append_log(f"\n[ERROR] {message}\n")
            messagebox.showerror("Noon Store", message)
            return
        self.append_log(f"\n[{'SUCCESS' if outcome == 'done' else 'STOPPED'}] {message}\n")
        for tab in self.main_app.tabs:
            tab.refresh_excel_list()
        self.select_sources(paths)
        if outcome == "done":
            messagebox.showinfo("Noon Store", message)

    def close_tab(self):
        if self.is_running:
            if not messagebox.askyesno("Confirm Close", f"Search is currently running in Tab #{self.tab_id}.\nAre you sure you want to stop the search and close this tab?"):
                return
            self.stop_matching()
            
        if self in self.main_app.tabs:
            self.main_app.tabs.remove(self)
        try:
            self.main_app.notebook.forget(self)
        except Exception:
            pass
        self.destroy()

class DuplicateFinderGUI:
    def __init__(self, root):
        self.root = root
        match_image_ai.setup_global_input_data_dir()
        self.root.title("AI Product Duplicate Finder")
        
        # Full screen height, centred horizontally at the top of the screen
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        window_width = min(1100, screen_width)
        self.root.geometry(f"{window_width}x{screen_height}+{(screen_width - window_width) // 2}+0")
        
        # Add new tab button header
        top_bar = ttk.Frame(root)
        top_bar.pack(fill="x", padx=15, pady=5)
        
        style = ttk.Style()
        style.configure("AddTab.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("CloseTab.TButton", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        style.configure("Stop.TButton", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        
        self.add_tab_btn = ttk.Button(top_bar, text="+ Add New Search Tab", command=self.add_search_tab, style="AddTab.TButton")
        self.add_tab_btn.pack(side="left", padx=5, pady=5)
        
        self.close_tab_btn = ttk.Button(top_bar, text="✕ Close Current Tab", command=self.close_current_tab, style="CloseTab.TButton")
        self.close_tab_btn.pack(side="left", padx=5, pady=5)
        
        # Notebook Layout
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=15, pady=5)
        
        self.tabs = []
        self.tab_counter = 0
        
        # Add initial search tab
        self.add_search_tab()

    def add_search_tab(self):
        self.tab_counter += 1
        new_tab = SearchTab(self.notebook, self.tab_counter, self)
        self.tabs.append(new_tab)
        self.notebook.add(new_tab, text=f"Search Tab #{self.tab_counter}")
        self.notebook.select(new_tab)

    def close_current_tab(self):
        try:
            current_index = self.notebook.index(self.notebook.select())
            if current_index < len(self.tabs):
                tab_widget = self.tabs[current_index]
                tab_widget.close_tab()
        except Exception:
            pass


if __name__ == "__main__":
    # Apply standard native look and feel styling configurations
    root = tk.Tk()
    set_app_icon(root)      # decoration only: a missing or unreadable icon changes nothing else
    style = ttk.Style(root)
    # Use native theme based on operating system
    if sys.platform.startswith("darwin"):
        style.theme_use("aqua")
    elif sys.platform.startswith("win"):
        style.theme_use("vista")
    else:
        style.theme_use("clam")
        
    app = DuplicateFinderGUI(root)
    root.mainloop()