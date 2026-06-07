import os
import sys
import datetime

# Check if the date is after June 9, 2026
if datetime.date.today() > datetime.date(2026, 6, 9):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Expired", "This version of the program has expired. It is not available after June 9, 2026.")
        root.destroy()
    except Exception:
        pass
    sys.exit("This version of the program has expired. It is not available after June 9, 2026.")

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
                    self.progress_bar.config(mode="determinate")
                self.progress_bar["value"] = percentage
                self.status_var.set(f"Scanning & indexing images: {percentage}%...")
            
            # Print minimal progress info to log to avoid bloating the text box
            # only if it contains actual stats and not just empty spaces
            clean = text.replace('\r', '').strip()
            if clean and ('%' in clean or 'it/s' in clean):
                # We update the last line if possible, or just skip log-spam entirely to prevent freeze
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

class DuplicateFinderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Product Duplicate Finder")
        self.root.geometry("680x700")
        self.root.geometry("680x700")
        
        # Main Layout Container
        main_frame = tk.Frame(root)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Form Card Frame
        form_card = tk.LabelFrame(main_frame, text=" AI Search Parameters ", font=("Segoe UI", 10, "bold"), padx=15, pady=15)
        form_card.pack(fill="x", pady=5)
        
        # 1. Query Image Selection Row
        image_label = tk.Label(form_card, text="Query Image:")
        image_label.grid(row=0, column=0, sticky="w", padx=5, pady=10)
        
        self.image_path_var = tk.StringVar()
        image_entry = tk.Entry(form_card, textvariable=self.image_path_var, width=40)
        image_entry.grid(row=0, column=1, padx=5, pady=10, sticky="we")
        
        browse_btn = tk.Button(form_card, text="Browse...", command=self.browse_image)
        browse_btn.grid(row=0, column=2, padx=5, pady=10)
        
        # 1.5 Image Preview Panel (placed on the right side spanning rows 0, 1, 2)
        self.preview_frame = tk.LabelFrame(form_card, text=" Image Preview ", font=("Segoe UI", 9, "bold"), padx=5, pady=5)
        self.preview_frame.grid(row=0, column=3, rowspan=3, padx=(20, 5), pady=5, sticky="nsew")
        
        self.preview_label = tk.Label(self.preview_frame, text="No Image\nSelected", font=("Segoe UI", 9, "italic"), width=18, height=8, relief="groove", bd=1)
        self.preview_label.pack(fill="both", expand=True)
        
        # Trace variable changes to automatically update preview
        self.image_path_var.trace_add("write", lambda *args: self.update_preview())
        
        # 2. Query Title Row
        title_label = tk.Label(form_card, text="Query Title:")
        title_label.grid(row=1, column=0, sticky="nw", padx=5, pady=10)
        
        self.title_text = tk.Text(form_card, height=3, width=40, font=("Segoe UI", 10))
        self.title_text.grid(row=1, column=1, columnspan=2, padx=5, pady=10, sticky="we")
        
        # 3. Settings Row
        settings_frame = tk.Frame(form_card)
        settings_frame.grid(row=2, column=0, columnspan=3, pady=10, sticky="w", padx=5)
        
        self.strict_var = tk.BooleanVar(value=True)
        strict_cb = tk.Checkbutton(settings_frame, text="Enforce Strict Model Matching", variable=self.strict_var)
        strict_cb.pack(side="left", padx=5)
        
        self.no_indexing_var = tk.BooleanVar(value=False)
        no_indexing_cb = tk.Checkbutton(settings_frame, text="Skip Image Index Check", variable=self.no_indexing_var)
        no_indexing_cb.pack(side="left", padx=5)
        
        top_lbl = tk.Label(settings_frame, text="Limit Matches:")
        top_lbl.pack(side="left", padx=(10, 5))
        
        self.top_var = tk.StringVar(value="50")
        top_spinner = tk.Spinbox(settings_frame, from_=5, to=200, width=5, textvariable=self.top_var)
        top_spinner.pack(side="left", padx=5)
        
        # Progress Bar & Status Row
        self.progress_frame = tk.Frame(main_frame)
        self.progress_frame.pack(fill="x", pady=10)
        
        self.status_var = tk.StringVar(value="Ready to start search.")
        self.status_lbl = tk.Label(self.progress_frame, textvariable=self.status_var, font=("Segoe UI", 9, "italic"))
        self.status_lbl.pack(anchor="w", pady=2)
        
        self.progress = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=2)
        
        # Action Buttons
        btn_frame = tk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=5)
        
        self.run_btn = tk.Button(btn_frame, text="Find Duplicate Listings", command=self.start_matching_thread, font=("Segoe UI", 11, "bold"), height=2)
        self.run_btn.pack(fill="x", side="left", expand=True, padx=5)
        
        self.view_btn = tk.Button(btn_frame, text="View Last Results (HTML)", command=self.open_last_results, font=("Segoe UI", 11, "bold"), height=2)
        self.view_btn.pack(fill="x", side="left", expand=True, padx=5)
        
        # Log Panel
        log_lbl = tk.Label(main_frame, text="Execution Log:", font=("Segoe UI", 9, "bold"))
        log_lbl.pack(anchor="w", pady=(15, 2))
        
        log_frame = tk.Frame(main_frame, bd=1, relief="sunken")
        log_frame.pack(fill="both", expand=True, pady=5)
        
        self.log_text = tk.Text(log_frame, font=("Consolas", 9), wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # Grid weight settings for responsiveness
        form_card.columnconfigure(1, weight=1)

    def browse_image(self):
        file_path = filedialog.askopenfilename(
            title="Select Product Query Image",
            filetypes=[
                ("Image Files", "*.png *.jpg *.jpeg *.webp *.gif *.avif *.heic *.bmp *.tif *.tiff"),
                ("RAW Images", "*.cr2 *.nef *.arw *.dng *.orf *.rw2 *.pef *.x3f"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self.image_path_var.set(file_path)

    def update_preview(self):
        file_path = self.image_path_var.get().strip()
        if not file_path or not os.path.exists(file_path):
            self.preview_label.config(image="", text="No Image\nSelected", relief="groove")
            return
            
        try:
            # Load and scale the image to fit in a 140x140 box
            img = Image.open(file_path)
            img.thumbnail((140, 140))
            
            # Convert to ImageTk PhotoImage
            self.photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.photo, text="", relief="flat")
        except Exception as e:
            self.preview_label.config(image="", text="Error\nLoading Preview", relief="groove")
            print(f"Error loading preview: {e}")

    def open_last_results(self):
        html_path = os.path.abspath("search_results.html")
        if os.path.exists(html_path):
            webbrowser.open(f"file:///{html_path}")
        else:
            messagebox.showwarning("No Results", "No generated search_results.html file was found. Run a search first.")

    def start_matching_thread(self):
        query_image = self.image_path_var.get().strip()
        query_title = self.title_text.get("1.0", tk.END).strip()
        
        if not query_image:
            messagebox.showerror("Error", "Please select a product query image first.")
            return
            
        if not os.path.exists(query_image):
            messagebox.showerror("Error", f"Selected image path does not exist:\n{query_image}")
            return
            
        # Disable inputs and clear log
        self.run_btn.config(state="disabled")
        self.progress.start(10)
        self.status_var.set("Initializing AI search model & calculating embeddings...")
        self.log_text.delete("1.0", tk.END)
        self.append_log("Starting AI Product Duplicate Finder...\n")
        
        # Start executing the python script in a background thread
        thread = threading.Thread(target=self.run_matching_search, args=(query_image, query_title))
        thread.daemon = True
        thread.start()

    def append_log(self, text):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def run_matching_search(self, image_path, query_title):
        try:
            # Set up command line arguments for match_image_ai
            old_argv = sys.argv
            sys.argv = [
                "match_image_ai.py",
                "--query", image_path,
                "--query-title", query_title,
                "--top", self.top_var.get()
            ]
            if self.strict_var.get():
                sys.argv.append("--strict")
            if self.no_indexing_var.get():
                sys.argv.append("--no-indexing")

            self.root.after(0, self.status_var.set, "Running AI visual search...")
            
            # Redirect stdout/stderr to the GUI log window
            redirector = CustomStdout(self.root, self.log_text, self.status_var, self.progress)
            
            try:
                sys.stdout = redirector
                sys.stderr = redirector
                
                # Execute match_image_ai main method
                match_image_ai.main()
                
                # Execute generate_report html generator
                self.root.after(0, self.status_var.set, "Compiling search matches into HTML dashboard...")
                self.root.after(0, self.append_log, "Generating search_results.html report...\n")
                
                generate_report.generate_html_report()
            finally:
                sys.argv = old_argv
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

            # Succeeded!
            self.root.after(0, self.on_search_success)
            
        except Exception as e:
            self.root.after(0, lambda err=str(e): self.on_search_error(err))

    def on_search_success(self):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.status_var.set("Search complete! Matches saved to search_results.html")
        self.append_log("\n[SUCCESS] AI Duplicate Finder completed successfully.\n")
        
        # Prompt to show results
        if messagebox.askyesno("Search Complete", "AI search matching finished successfully!\n\nWould you like to open the HTML results dashboard in your browser?"):
            self.open_last_results()

    def on_search_error(self, error_msg):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.status_var.set("Error occurred during search matching.")
        self.append_log(f"\n[ERROR] Process failed:\n{error_msg}\n")
        messagebox.showerror("Error During Matching", f"An error occurred:\n\n{error_msg}")

if __name__ == "__main__":
    root = tk.Tk()
    app = DuplicateFinderGUI(root)
    root.mainloop()