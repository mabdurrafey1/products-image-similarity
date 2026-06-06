import os
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

def download_missing_images(df, image_dir="downloaded_images", max_workers=30):
    """
    Checks the loaded pandas DataFrame for product SKU and Image URL values,
    and concurrently downloads any images that are not cached locally.
    """
    if not os.path.exists(image_dir):
        os.makedirs(image_dir, exist_ok=True)

    print("Checking for missing images in database...")
    
    # Identify items to download
    download_tasks = []
    for idx, row in df.iterrows():
        sku = str(row.get('SKU', '')).strip()
        url = str(row.get('Image URL', '')).strip()
        if not sku or not url or url.lower() == 'nan':
            continue
        
        # Check if image already exists
        img_name = f"{sku}.jpg"
        img_path = os.path.join(image_dir, img_name)
        if not os.path.exists(img_path):
            download_tasks.append((sku, url, img_path))
            
    if download_tasks:
        print(f"Found {len(download_tasks)} missing images. Starting download using {max_workers} workers...")
        def download_single(task):
            sku, url, dest = task
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    with open(dest, 'wb') as f:
                        f.write(r.content)
                else:
                    print(f"Failed downloading SKU {sku}: status code {r.status_code}")
            except Exception as e:
                print(f"Failed downloading SKU {sku}: {e}")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(download_single, download_tasks)
        print("Image download complete.\n")
    else:
        print("All database images are already cached locally.\n")
