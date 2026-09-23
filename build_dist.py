import os
import shutil
import subprocess
import sys

def stamp_version():
    """Write the tag being built into _version.py so the running app knows what it is.

    The tag is the only place a version is declared, so rather than keep a copy in the repo that
    could drift from it, the build takes the tag it was triggered by and writes it out. A local
    build with no tag says "dev", which the updater reads as "not a release" and leaves alone.
    """
    tag = (os.environ.get("APP_VERSION") or os.environ.get("GITHUB_REF_NAME") or "").strip()
    if not tag.startswith("v"):
        tag = "dev"
    with open("_version.py", "w") as handle:
        handle.write(f'VERSION = "{tag}"\n')
    print(f"Stamped version: {tag}")
    return tag


def build():
    stamp_version()
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
        "--hidden-import=onnxruntime",
        "--hidden-import=huggingface_hub",
        # Imported inside a function so a dev checkout can do without it, which means PyInstaller's
        # scan never sees it; named here or the built app would report no version at all.
        "--hidden-import=_version",
        "--name=AI_Product_Duplicate_Finder",
        "gui.py"
    ]

    # Both are optional: PyInstaller refuses to build at all if pointed at a file that isn't there
    icon = os.path.join("assets", "app_icon.icns" if sys.platform == "darwin" else "app_icon.ico")
    if os.path.exists(icon):
        cmd.insert(-1, f"--icon={icon}")
    else:
        print(f"No {icon} found - building without an icon.")
    if os.path.exists("assets"):
        # The Dock tile is set at runtime from the PNG, so the PNG has to travel with the build
        cmd.insert(-1, f"--add-data=assets{os.pathsep}assets")
    
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

    print("\n=== Step 3: Zipping the final distribution ===")
    for dist_dir in target_dirs:
        archive_base = dist_dir + "_dist"
        print(f"Zipping {dist_dir} into {archive_base}.zip...")
        shutil.make_archive(archive_base, 'zip', dist_dir)
        print(f"Successfully created zip archive: {archive_base}.zip")

    print("\n=== Build Completed Successfully! ===")

if __name__ == "__main__":
    build()
