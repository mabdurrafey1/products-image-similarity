import os
import sys
import datetime

# Check if the date is after June 9, 2026
if datetime.date.today() > datetime.date(2026, 6, 9):
    sys.exit("This version of the program has expired. It is not available after June 9, 2026.")

import subprocess
import webbrowser
import argparse

def main():
    parser = argparse.ArgumentParser(description="AI-Powered Duplicate Listing Finder")
    parser.add_argument("--query", required=True, help="Path to local query image")
    parser.add_argument("--query-title", default="", help="Query Title text")
    parser.add_argument("--top", type=int, default=50, help="Number of top matches")
    parser.add_argument("--strict", action="store_true", help="Enable strict model filtering")
    args = parser.parse_args()

    # Determine command to run match_image_ai.py
    # If packaged via PyInstaller, sys._MEIPASS holds the temp resource directory
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    match_script = os.path.join(base_dir, "match_image_ai.py")
    report_script = os.path.join(base_dir, "generate_report.py")

    print(f"--- Running Duplicate Visual Matcher ---")
    search_cmd = [
        sys.executable, match_script,
        "--query", args.query,
        "--query-title", args.query_title,
        "--top", str(args.top)
    ]
    if args.strict:
        search_cmd.append("--strict")

    # Run the search
    try:
        subprocess.run(search_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running search matching: {e}")
        sys.exit(1)

    print(f"\n--- Generating HTML Report ---")
    try:
        subprocess.run([sys.executable, report_script], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error generating report: {e}")
        sys.exit(1)

    # Open HTML page in default browser
    html_path = os.path.abspath("search_results.html")
    if os.path.exists(html_path):
        print(f"Opening report: {html_path}")
        webbrowser.open(f"file:///{html_path}")
    else:
        print("Error: search_results.html was not generated.")

if __name__ == "__main__":
    main()
