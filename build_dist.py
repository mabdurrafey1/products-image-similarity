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
            if os.path.isdir(item_path):
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

    print("\n=== Build Completed Successfully! ===")
    print("You can now zip the folder(s) under the 'dist' directory and send it to the client.")

if __name__ == "__main__":
    build()
