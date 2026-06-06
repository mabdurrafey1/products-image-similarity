import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import threading
import webbrowser

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
        
        top_lbl = tk.Label(settings_frame, text="Limit Matches:")
        top_lbl.pack(side="left", padx=(20, 5))
        
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
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.webp *.gif"), ("All Files", "*.*")]
        )
        if file_path:
            self.image_path_var.set(file_path)

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
            # Construct execution parameters
            py_exe = os.path.join(".venv", "bin", "python")
            if sys.platform.startswith("win"):
                py_exe = os.path.join(".venv", "Scripts", "python.exe")
            if not os.path.exists(py_exe):
                py_exe = "python" # fallback

            cmd = [
                py_exe, "match_image_ai.py",
                "--query", image_path,
                "--query-title", query_title,
                "--top", self.top_var.get()
            ]
            if self.strict_var.get():
                cmd.append("--strict")

            # Run Python duplicate visual matcher script with line-by-line pipe
            self.root.after(0, self.status_var.set, "Running AI visual search...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Read stdout line by line
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                # Filter/clean output or update status dynamically
                clean_line = line.strip()
                if clean_line:
                    if "Checking for missing images" in clean_line:
                        self.root.after(0, self.status_var.set, "Checking for missing database images...")
                    elif "Starting download" in clean_line:
                        self.root.after(0, self.status_var.set, "Downloading missing database images...")
                    elif "Initializing CLIP text encoder" in clean_line:
                        self.root.after(0, self.status_var.set, "Loading CLIP model text encoder...")
                    elif "Performing text similarity search" in clean_line:
                        self.root.after(0, self.status_var.set, "Calculating semantic text matching...")
                    elif "Attaching visual similarity scores" in clean_line:
                        self.root.after(0, self.status_var.set, "Applying rclip visual ranks...")
                    self.root.after(0, self.append_log, line)
            
            process.wait()
            
            if process.returncode != 0:
                raise Exception(f"Visual matcher failed with exit code {process.returncode}")

            # Generate the HTML dashboard report
            self.root.after(0, self.status_var.set, "Compiling search matches into HTML dashboard...")
            self.root.after(0, self.append_log, "Generating search_results.html report...\n")
            
            report_process = subprocess.Popen(
                [py_exe, "generate_report.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            report_out, _ = report_process.communicate()
            if report_out:
                self.root.after(0, self.append_log, report_out)
                
            if report_process.returncode != 0:
                raise Exception(f"Report generator failed with exit code {report_process.returncode}")
            
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