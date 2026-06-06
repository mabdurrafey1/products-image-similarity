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
        self.root.geometry("680x520")
        self.root.configure(bg="#0f1423")
        
        # Style
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Colors
        self.bg_dark = "#0f1423"
        self.card_bg = "#121829"
        self.accent_indigo = "#6366f1"
        self.text_light = "#f3f4f6"
        self.text_gray = "#9ca3af"

        # General Styling Configurations
        self.style.configure(".", background=self.card_bg, foreground=self.text_light, font=("Segoe UI", 10))
        self.style.configure("TLabel", background=self.bg_dark, foreground=self.text_light)
        self.style.configure("TFrame", background=self.bg_dark)
        self.style.configure("Card.TFrame", background=self.card_bg, borderwidth=1, relief="solid")
        
        # Configure Checkbutton
        self.style.configure("TCheckbutton", background=self.card_bg, foreground=self.text_light)
        self.style.map("TCheckbutton",
            background=[('active', self.card_bg), ('pressed', self.card_bg)],
            foreground=[('active', '#ffffff')]
        )
        
        # Configure Spinbox and Progressbar
        self.style.configure("TSpinbox", fieldbackground="#1b233d", foreground="#ffffff", bordercolor="#1b233d")
        self.style.configure("Horizontal.TProgressbar", troughcolor="#121829", background=self.accent_indigo)

        # --- NEW: Button Styling ---
        self.style.configure("Browse.TButton", background=self.accent_indigo, foreground="#ffffff", borderwidth=0)
        self.style.map("Browse.TButton", background=[('active', '#4f46e5')])

        self.style.configure("Run.TButton", background=self.accent_indigo, foreground="#ffffff", font=("Segoe UI", 11, "bold"), borderwidth=0)
        self.style.map("Run.TButton",
            background=[('disabled', '#4b5563'), ('active', '#4f46e5')],
            foreground=[('disabled', '#9ca3af')]
        )

        self.style.configure("View.TButton", background="#10b981", foreground="#ffffff", font=("Segoe UI", 11, "bold"), borderwidth=0)
        self.style.map("View.TButton", background=[('active', '#059669')])
        # ---------------------------
        
        # Title Label
        title_lbl = tk.Label(root, text="AI Product Duplicate Finder", bg=self.bg_dark, fg="#ffffff", font=("Segoe UI", 18, "bold"))
        title_lbl.pack(pady=15)
        
        # Main Layout Container
        main_frame = ttk.Frame(root, style="TFrame")
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Form Card Frame
        form_card = ttk.Frame(main_frame, style="Card.TFrame")
        form_card.pack(fill="x", ipady=15, ipadx=15, pady=5)
        
        # 1. Query Image Selection Row
        image_label = tk.Label(form_card, text="Query Image:", bg=self.card_bg, fg=self.text_light, font=("Segoe UI", 10, "bold"))
        image_label.grid(row=0, column=0, sticky="w", padx=10, pady=10)
        
        self.image_path_var = tk.StringVar()
        image_entry = tk.Entry(form_card, textvariable=self.image_path_var, width=40, bg="#1b233d", fg="#ffffff", insertbackground="white", relief="flat")
        image_entry.grid(row=0, column=1, padx=5, pady=10, sticky="we")
        
        # macOS compatible ttk Browse Button
        browse_btn = ttk.Button(form_card, text="Browse...", command=self.browse_image, style="Browse.TButton")
        browse_btn.grid(row=0, column=2, padx=10, pady=10)
        
        # 2. Query Title Row
        title_label = tk.Label(form_card, text="Query Title:", bg=self.card_bg, fg=self.text_light, font=("Segoe UI", 10, "bold"))
        title_label.grid(row=1, column=0, sticky="nw", padx=10, pady=10)
        
        self.title_text = tk.Text(form_card, height=3, width=40, bg="#1b233d", fg="#ffffff", insertbackground="white", relief="flat", font=("Segoe UI", 10))
        self.title_text.grid(row=1, column=1, columnspan=2, padx=5, pady=10, sticky="we")
        
        # 3. Settings Row
        settings_frame = tk.Frame(form_card, bg=self.card_bg)
        settings_frame.grid(row=2, column=0, columnspan=3, pady=10, sticky="w", padx=10)
        
        self.strict_var = tk.BooleanVar(value=True)
        strict_cb = ttk.Checkbutton(settings_frame, text="Enforce Strict Model Matching", variable=self.strict_var, style="TCheckbutton")
        strict_cb.pack(side="left", padx=5)
        
        top_lbl = tk.Label(settings_frame, text="Limit Matches:", bg=self.card_bg, fg=self.text_light)
        top_lbl.pack(side="left", padx=(20, 5))
        
        self.top_var = tk.StringVar(value="50")
        top_spinner = ttk.Spinbox(settings_frame, from_=5, to=200, width=5, textvariable=self.top_var, style="TSpinbox")
        top_spinner.pack(side="left", padx=5)
        
        # Progress Bar & Status Row
        self.progress_frame = tk.Frame(main_frame, bg=self.bg_dark)
        self.progress_frame.pack(fill="x", pady=15)
        
        self.status_var = tk.StringVar(value="Ready to start search.")
        self.status_lbl = tk.Label(self.progress_frame, textvariable=self.status_var, bg=self.bg_dark, fg=self.text_gray, font=("Segoe UI", 9, "italic"))
        self.status_lbl.pack(anchor="w", pady=2)
        
        self.progress = ttk.Progressbar(self.progress_frame, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=2)
        
        # Action Buttons (Using ttk and ipady for height)
        btn_frame = tk.Frame(main_frame, bg=self.bg_dark)
        btn_frame.pack(fill="x", pady=5)
        
        self.run_btn = ttk.Button(btn_frame, text="Find Duplicate Listings", command=self.start_matching_thread, style="Run.TButton")
        self.run_btn.pack(fill="x", side="left", expand=True, padx=5, ipady=8)
        
        self.view_btn = ttk.Button(btn_frame, text="View Last Results (HTML)", command=self.open_last_results, style="View.TButton")
        self.view_btn.pack(fill="x", side="left", expand=True, padx=5, ipady=8)
        
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
            
        # Disable input; the style map automatically handles turning it gray (#4b5563)
        self.run_btn.config(state="disabled")
        self.progress.start(10)
        self.status_var.set("Initializing AI search model & calculating embeddings...")
        
        # Start executing the python script in a background thread
        thread = threading.Thread(target=self.run_matching_search, args=(query_image, query_title))
        thread.daemon = True
        thread.start()

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

            # Run Python duplicate visual matcher script
            self.status_var.set("Querying AI model for visual similarities across cached database...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise Exception(result.stderr or result.stdout)

            # Generate the HTML dashboard report
            self.status_var.set("Compiling final search matches into HTML dashboard...")
            report_cmd = [py_exe, "generate_report.py"]
            subprocess.run(report_cmd, check=True)
            
            # Succeeded!
            self.root.after(0, self.on_search_success)
            
        except Exception as e:
            self.root.after(0, lambda err=str(e): self.on_search_error(err))

    def on_search_success(self):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.status_var.set("Search complete! Matches saved to search_results.html")
        
        # Prompt to show results
        if messagebox.askyesno("Search Complete", "AI search matching finished successfully!\n\nWould you like to open the HTML results dashboard in your browser?"):
            self.open_last_results()

    def on_search_error(self, error_msg):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.status_var.set("Error occurred during search matching.")
        messagebox.showerror("Error During Matching", f"An error occurred:\n\n{error_msg}")

if __name__ == "__main__":
    root = tk.Tk()
    app = DuplicateFinderGUI(root)
    root.mainloop()