import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import webbrowser
import match_image_ai
import generate_report
from PIL import Image, ImageTk
import re

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
        # Check if this is a progress bar update line
        # tqdm updates usually end with \r or contain progress bars (e.g. 50%|███)
        is_progress = '\r' in text or '%' in text
        
        if is_progress:
            # Try to extract percentage
            match = self.pct_regex.search(text)
            if match:
                percentage = int(match.group(1))
                # Switch progressbar to determinate mode and show progress
                if self.progress_bar["mode"] != "determinate":
                    self.progress_bar.stop()
                    self.progress_bar.config(mode="determinate")
                self.progress_bar["value"] = percentage
                if "download" in text.lower():
                    self.status_var.set(f"Downloading images: {percentage}%...")
                else:
                    self.status_var.set(f"Scanning & indexing images: {percentage}%...")
            
            # Print minimal progress info to log to avoid bloating the text box
            clean = text.replace('\r', '').strip()
            if clean and ('%' in clean or 'it/s' in clean):
                pass
            return

        # Normal logs get written to the text widget
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
        
        # Configure stop button style
        style = ttk.Style()
        style.configure("Stop.TButton", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        
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
        
        # 3. Input Source Row
        source_label = ttk.Label(form_card, text="Input Source:")
        source_label.grid(row=2, column=0, sticky="w", padx=10, pady=10)
        
        import glob
        excel_files = sorted(glob.glob("input_data/*.xlsx"))
        self.excel_options = [os.path.basename(f) for f in excel_files]
        if not self.excel_options:
            self.excel_options = ["(No Excel files found)"]
            
        self.selected_excel_var = tk.StringVar(value=self.excel_options[0])
        self.source_dropdown = ttk.Combobox(form_card, textvariable=self.selected_excel_var, values=self.excel_options, state="readonly", width=40)
        self.source_dropdown.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        
        refresh_btn = ttk.Button(form_card, text="Refresh", command=self.refresh_excel_list)
        refresh_btn.grid(row=2, column=2, padx=10, pady=10, sticky="w")
        
        # 4. Price Range Row
        price_label = ttk.Label(form_card, text="Price Range:")
        price_label.grid(row=3, column=0, sticky="w", padx=10, pady=10)
        
        price_frame = ttk.Frame(form_card)
        price_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=10, pady=10)
        
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
        settings_frame.grid(row=4, column=0, columnspan=3, pady=10, sticky="w", padx=10)
        
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
        sim_label.grid(row=5, column=0, sticky="w", padx=10, pady=10)

        sim_frame = ttk.Frame(form_card)
        sim_frame.grid(row=5, column=1, columnspan=2, sticky="we", padx=10, pady=10)

        # Text Match Threshold
        text_frame = ttk.Frame(sim_frame)
        text_frame.pack(side="left", fill="x", expand=True, padx=(0, 15))

        text_lbl = ttk.Label(text_frame, text="Text:")
        text_lbl.pack(side="left", padx=(0, 5))

        self.text_sim_var = tk.DoubleVar(value=70.0)
        self.sim_value_lbl = ttk.Label(text_frame, text="70%", font=("Segoe UI", 9, "bold"), width=5)

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

        self.img_sim_var = tk.DoubleVar(value=0.20)
        self.img_sim_value_lbl = ttk.Label(img_frame, text="0.20", font=("Segoe UI", 9, "bold"), width=5)

        def update_img_sim_lbl(val):
            self.img_sim_value_lbl.config(text=f"{float(val):.2f}")

        self.img_sim_slider = ttk.Scale(img_frame, from_=0.0, to=2.0, variable=self.img_sim_var, orient="horizontal", command=update_img_sim_lbl)
        self.img_sim_slider.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.img_sim_value_lbl.pack(side="left")
        
        # Progress Bar & Status Row
        self.progress_frame = ttk.Frame(main_frame)
        self.progress_frame.pack(fill="x", pady=10)
        
        self.status_var = tk.StringVar(value="Ready to start search.")
        self.status_lbl = ttk.Label(self.progress_frame, textvariable=self.status_var, font=("Segoe UI", 9, "italic"))
        self.status_lbl.pack(anchor="w", pady=2)
        
        self.progress = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=2)
        
        # Action Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=5)
        
        self.run_btn = ttk.Button(btn_frame, text="Find Duplicate Listings", command=self.start_matching_thread)
        self.run_btn.pack(side="left", expand=True, fill="x", padx=5)
        
        self.view_btn = ttk.Button(btn_frame, text="View Results (HTML)", command=self.open_last_results)
        self.view_btn.pack(side="left", expand=True, fill="x", padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="Stop Execution", command=self.stop_matching, style="Stop.TButton")
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

    def refresh_excel_list(self):
        import glob
        excel_files = sorted(glob.glob("input_data/*.xlsx"))
        self.excel_options = [os.path.basename(f) for f in excel_files]
        if not self.excel_options:
            self.excel_options = ["(No Excel files found)"]
        
        self.source_dropdown["values"] = self.excel_options
        
        # Keep current selection if still valid, otherwise reset to default
        current_val = self.selected_excel_var.get()
        if current_val not in self.excel_options:
            self.selected_excel_var.set(self.excel_options[0])

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
            
        image_paths = [p.strip() for p in query_image.split(";") if p.strip()]
        for p in image_paths:
            if not os.path.exists(p):
                messagebox.showerror("Error", f"Selected image path does not exist:\n{p}")
                return
            
        self.is_running = True
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id}")
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.start(10)
        self.status_var.set("Initializing AI search model & calculating embeddings...")
        self.log_text.delete("1.0", tk.END)
        self.append_log(f"Starting AI Product Duplicate Finder (Tab #{self.tab_id})...\n")
        
        thread = threading.Thread(target=self.run_matching_search, args=(query_image, query_title))
        thread.daemon = True
        thread.start()

    def append_log(self, text):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def run_matching_search(self, image_path, query_title):
        tid = threading.get_ident()
        redirector = CustomStdout(self.main_app.root, self.log_text, self.status_var, self.progress)
        
        # Register thread redirection
        thread_safe_stdout.redirectors[tid] = redirector
        thread_safe_stderr.redirectors[tid] = redirector
        
        try:
            os.makedirs("temp", exist_ok=True)
            selected_excel_name = self.selected_excel_var.get().strip()
            excel_path = os.path.join("input_data", selected_excel_name)
            
            old_argv = sys.argv
            sys.argv = [
                "match_image_ai.py",
                "--query", image_path,
                "--query-title", query_title,
                "--input", excel_path,
                "--output", f"temp/search_results_ai_{self.tab_id}.json",
                "--workers", self.workers_var.get(),
                "--top", self.top_var.get(),
                "--min-text-sim", f"{self.text_sim_var.get() / 100.0:.2f}",
                "--min-score", f"{self.img_sim_var.get():.2f}"
            ]
            
            min_p = self.min_price_var.get().strip()
            max_p = self.max_price_var.get().strip()
            if min_p:
                sys.argv.extend(["--min-price", min_p])
            if max_p:
                sys.argv.extend(["--max-price", max_p])
            if self.strict_var.get():
                sys.argv.append("--strict")
            if self.no_indexing_var.get():
                sys.argv.append("--no-indexing")

            self.main_app.root.after(0, self.status_var.set, "Running AI visual search...")
            
            try:
                # Execute match_image_ai main method
                match_image_ai.main()
                
                import datetime
                # Slugify query_title for report filename
                slug = re.sub(r'[^a-zA-Z0-9_-]', '_', query_title).strip('_')
                if not slug:
                    slug = "search_results"
                slug = slug[:50]
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"{slug}_{timestamp}.html"
                report_path = os.path.join("reports", report_filename)
                os.makedirs("reports", exist_ok=True)
                self.last_report_path = report_path

                # Execute generate_report html generator
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
            finally:
                sys.argv = old_argv

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
        self.append_log("\n[Stop Request Received] Aborting search...\n")
        self.status_var.set("Stopping execution...")
        match_image_ai.stop_requested = True
        self.stop_btn.config(state="disabled")

    def on_search_success(self):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set(f"Search complete! Matches saved to {self.last_report_path}")
        self.append_log("\n[SUCCESS] AI Duplicate Finder completed successfully.\n")
        self.main_app.notebook.tab(self, text=f"Search Tab #{self.tab_id} ✅")
        
        if messagebox.askyesno("Search Complete", f"AI search matching finished successfully for Tab #{self.tab_id}!\n\nWould you like to open the HTML results dashboard in your browser?"):
            self.open_last_results()

    def on_search_error(self, error_msg):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        
        if "StopRequested" in error_msg or "stopped by user" in error_msg:
            self.status_var.set("Execution stopped by user.")
            self.append_log("\n[STOPPED] Search execution was stopped by user.\n")
        else:
            self.status_var.set("Error occurred during search matching.")
            self.append_log(f"\n[ERROR] Process failed:\n{error_msg}\n")
            messagebox.showerror("Error During Matching", f"An error occurred:\n\n{error_msg}")

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
        self.root.title("AI Product Duplicate Finder")
        
        screen_height = self.root.winfo_screenheight()
        window_height = max(700, screen_height - 100)
        self.root.geometry(f"1100x{window_height}")
        
        # Add new tab button header
        top_bar = ttk.Frame(root)
        top_bar.pack(fill="x", padx=15, pady=5)
        
        style = ttk.Style()
        style.configure("AddTab.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("CloseTab.TButton", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        
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