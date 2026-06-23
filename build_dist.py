import os
import shutil
import subprocess
import sys

def build():
    print("=== Step 1: Running PyInstaller to build gui.py ===")
    
    # Locate Python interpreter to run PyInstaller as a module
    py_exe = sys.executable
    
    # PyInstaller command
    # We use --onedir (default) or --onefile. 
    # Since we need to run match_image_ai.py as a script next to the exe, --onedir is cleaner,
    # but we can do --onefile and copy the scripts next to the generated .exe in dist/
    cmd = [
        py_exe, "-m", "PyInstaller",
        "--clean",
        "-y",
        "--noconsole",
        "--name=AI_Product_Duplicate_Finder",
        "gui.py"
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: PyInstaller build failed: {e}")
        sys.exit(1)
        
    print("\n=== Step 2: Copying required files to dist directory ===")
    
    # Determine dist folder dynamically based on whatever PyInstaller created
    target_dirs = []
    if os.path.exists("dist"):
        for item in os.listdir("dist"):
            item_path = os.path.join("dist", item)
            if os.path.isdir(item_path) and not item.endswith("_dist"):
                target_dirs.append(item_path)
                
    if not target_dirs:
        target_dirs = ["dist"]
        
    print(f"Target distribution folders found: {[os.path.abspath(d) for d in target_dirs]}")
    
    for dist_dir in target_dirs:
        print(f"\n--- Copying files to: {dist_dir} ---")
        
        # Copy input_data folder if it exists
        if os.path.exists("input_data"):
            dest_input = os.path.join(dist_dir, "input_data")
            if os.path.exists(dest_input):
                shutil.rmtree(dest_input)
            shutil.copytree("input_data", dest_input)
            print(f"Copied folder: input_data -> {dest_input}")
        else:
            print("Warning: input_data folder not found, skipping.")
        
        # Copy runner scripts next to the binary/executable
        for script in ["match_image_ai.py", "generate_report.py", "downloader.py"]:
            if os.path.exists(script):
                dest_script = os.path.join(dist_dir, script)
                shutil.copy2(script, dest_script)
                print(f"Copied script: {script} -> {dest_script}")
            else:
                print(f"Warning: Script {script} not found, skipping.")

        # Create output temp directory next to the binary
        dest_temp = os.path.join(dist_dir, "temp")
        os.makedirs(dest_temp, exist_ok=True)
        print(f"Created temp directory inside build folder: {dest_temp}")

    print("\n=== Step 3: Zipping the final distribution ===")
    for dist_dir in target_dirs:
        archive_base = dist_dir + "_dist"
        print(f"Zipping {dist_dir} into {archive_base}.zip...")
        shutil.make_archive(archive_base, 'zip', dist_dir)
        print(f"Successfully created zip archive: {archive_base}.zip")

    print("\n=== Build Completed Successfully! ===")

if __name__ == "__main__":
    build()
