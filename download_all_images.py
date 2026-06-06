import os
import urllib.request
import urllib.parse
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

def download_image(url, save_path):
    """Download image from URL with custom User-Agent to bypass simple blocks."""
    if not url or not isinstance(url, str):
        return False
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        # Silently fail or log minimal info to keep terminal readable
        return False

def main():
    input_dir = "input_data"
    image_dir = "downloaded_images"
    os.makedirs(image_dir, exist_ok=True)
    
    if not os.path.exists(input_dir):
        print(f"Error: Directory '{input_dir}' not found.")
        return
        
    excel_files = [f for f in os.listdir(input_dir) if f.endswith('.xlsx')]
    if not excel_files:
        print(f"No .xlsx files found in '{input_dir}'")
        return
        
    print(f"Found {len(excel_files)} Excel files in '{input_dir}'. Extracting image URLs...")
    
    download_tasks = {} # local_path -> url
    
    for filename in excel_files:
        filepath = os.path.join(input_dir, filename)
        try:
            df = pd.read_excel(filepath)
            if 'Image URL' not in df.columns:
                print(f"Skipping {filename}: 'Image URL' column missing.")
                continue
                
            for idx, row in df.iterrows():
                url = row['Image URL']
                sku = row.get('SKU')
                if pd.isna(url) or pd.isna(sku):
                    continue
                
                sku = str(sku)
                parsed = urllib.parse.urlparse(url)
                ext = os.path.splitext(parsed.path)[1]
                if not ext or len(ext) > 5:
                    ext = '.jpg'
                    
                local_path = os.path.join(image_dir, f"{sku}{ext}")
                
                if not os.path.exists(local_path):
                    download_tasks[local_path] = url
        except Exception as e:
            print(f"Error reading {filename}: {e}")

    total_tasks = len(download_tasks)
    if total_tasks == 0:
        print("All images are already downloaded and cached locally.")
        return
        
    print(f"Starting download of {total_tasks} unique missing images using 70 workers...")
    
    completed = 0
    success = 0
    with ThreadPoolExecutor(max_workers=70) as executor:
        future_to_path = {
            executor.submit(download_image, url, path): path 
            for path, url in download_tasks.items()
        }
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            completed += 1
            if future.result():
                success += 1
            if completed % 100 == 0 or completed == total_tasks:
                print(f"Progress: {completed}/{total_tasks} completed ({success} successful).")
                
    print(f"Finished downloads. {success} out of {total_tasks} successfully downloaded.")

if __name__ == "__main__":
    main()
