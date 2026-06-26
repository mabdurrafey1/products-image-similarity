import os
import requests
from concurrent.futures import ThreadPoolExecutor

def download_missing_images(df, image_dir="downloaded_images", max_workers=10):
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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        import threading
        from urllib3.util import Retry
        from requests.adapters import HTTPAdapter
        
        # Set up a thread-safe requests Session with retries
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries, pool_maxsize=max_workers, pool_block=False)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        progress_lock = threading.Lock()
        completed = 0
        total = len(download_tasks)

        def download_single(task):
            nonlocal completed
            sku, url, dest = task
            try:
                try:
                    import match_image_ai
                    if getattr(match_image_ai, "stop_requested", False):
                        return
                except Exception:
                    pass
                r = session.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    # Check if the response is actually an image and not an HTML error page
                    content_type = r.headers.get('Content-Type', '').lower()
                    if 'html' in content_type:
                        print(f"Failed downloading SKU {sku}: CDN returned HTML instead of image")
                        return
                    
                    # Try to load, resize, and compress the image
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(r.content))
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        img.thumbnail((300, 300))
                        img.save(dest, "JPEG", quality=80)
                    except Exception:
                        # Fallback to saving raw bytes if image processing fails
                        with open(dest, 'wb') as f:
                            f.write(r.content)
                else:
                    print(f"Failed downloading SKU {sku}: status code {r.status_code}")
            except Exception as e:
                print(f"Failed downloading SKU {sku}: {e}")
            finally:
                with progress_lock:
                    completed += 1
                    pct = int((completed / total) * 100)
                    print(f"\r[Download Progress] {pct}% ({completed}/{total})", end="", flush=True)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(download_single, download_tasks)
        print("Image download complete.\n")
    else:
        print("All database images are already cached locally.\n")

