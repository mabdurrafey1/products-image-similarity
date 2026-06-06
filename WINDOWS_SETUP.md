# Windows Setup Guide

Follow these steps to run the AI-Powered Duplicate Listing Search program on a Windows PC.

---

### Step 1: Install Python
Ensure that Python **3.8 or newer** (recommended: Python 3.11 or 3.12) is installed.
1. Download Python from the [Official Website](https://www.python.org/downloads/).
2. **CRITICAL:** When running the installer, make sure to check the box that says **"Add Python.exe to PATH"** at the bottom of the first screen.

---

### Step 2: Open Command Prompt / PowerShell
Navigate to the project root directory where the files (`match_image_ai.py`, `combined_listings.xlsx`, and `downloaded_images/`) are located.
* Tip: You can type `cmd` in the File Explorer address bar when viewing this folder and press Enter to open Command Prompt directly here.

---

### Step 3: Set Up Virtual Environment & Dependencies
Run the following commands in order:

```cmd
:: 1. Create a virtual environment
python -m venv .venv

:: 2. Activate the virtual environment
.venv\Scripts\activate

:: 3. Upgrade pip
python -m pip install --upgrade pip

:: 4. Install dependencies (pandas, openpyxl, torch, and rclip)
pip install pandas openpyxl numpy torch rclip
```

---

### Step 4: Run the AI Duplicate Finder
With the virtual environment activated, run the matching script.

#### Format:
```cmd
python match_image_ai.py --query "path\to\your\query_image.png" --query-title "Pasted Title Description Here" --strict --top 50
```

#### Example using a sample console query:
```cmd
python match_image_ai.py --query "downloaded_images\Z5422F2E75BE0B7C13392Z.jpg" --query-title "New X6 Handheld Game Console - HD Display Portable" --strict --top 50
```

---

### Step 5: Generate the HTML Report
After running the search above, it will save the matches in `search_results_ai.json`. Run this command to compile the matches into a rich, interactive HTML dashboard:

```cmd
python generate_report.py
```

---

### Step 6: Run via Graphical User Interface (GUI)
If you prefer not to use the command line parameters, you can launch our built-in graphical interface:

```cmd
python gui.py
```

This will open a window where you can:
1. Click **Browse...** to select your query image file.
2. Paste your **Query Title** into the text box.
3. Choose whether to enforce strict model matching.
4. Click **Find Duplicate Listings** to run the program and view your report automatically.

