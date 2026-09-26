import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def _build_download_tasks_by_row(df, image_dir):
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
    return download_tasks

def build_download_tasks(df, image_dir):
    """[(sku, url, destination)] for every row whose image isn't on disk yet, in row order.

    Reads the two columns whole and checks names against one listing of the folder, instead of a
    row-by-row walk with a stat per row. Only a plain file the listing names counts as present on
    its own; any other name is asked of os.path.exists, exactly as the row walk did.
    """
    try:
        import match_image_ai
        if not match_image_ai._rows_are_objects(df):
            return _build_download_tasks_by_row(df, image_dir)
    except Exception:
        return _build_download_tasks_by_row(df, image_dir)
    n = len(df)
    skus = df['SKU'].tolist() if 'SKU' in df.columns else [''] * n
    urls = df['Image URL'].tolist() if 'Image URL' in df.columns else [''] * n
    present = set()
    try:
        with os.scandir(image_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        present.add(entry.name)
                except OSError:
                    pass
    except OSError:
        present = set()
    download_tasks = []
    for sku, url in zip(skus, urls):
        sku = str(sku).strip()
        url = str(url).strip()
        if not sku or not url or url.lower() == 'nan':
            continue
        img_name = f"{sku}.jpg"
        img_path = os.path.join(image_dir, img_name)
        if img_name in present and os.sep not in img_name and (os.altsep is None or os.altsep not in img_name):
            continue
        if not os.path.exists(img_path):
            download_tasks.append((sku, url, img_path))
    return download_tasks

def download_missing_images(df, image_dir="downloaded_images", max_workers=10, should_stop=None):
    """
    Checks the loaded pandas DataFrame for product SKU and Image URL values,
    and concurrently downloads any images that are not cached locally.

    `should_stop` is a callable asked between downloads whether to give up. A GUI tab passes its own
    stop event's `is_set` here rather than raising the module-level `match_image_ai.stop_requested`,
    which is process-wide: setting that to stop one tab's sync would abort every other tab's search.
    """
    if not os.path.exists(image_dir):
        os.makedirs(image_dir, exist_ok=True)

    print("Checking for missing images in database...")
    
    # Identify items to download
    download_tasks = build_download_tasks(df, image_dir)
            
    if download_tasks:
        print(f"Found {len(download_tasks)} missing images. Starting download using {max_workers} workers...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
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
        
        total = len(download_tasks)

        # Runs in a worker thread; returns an error message instead of printing it, because the GUI shows only what
        # the calling thread prints
        def download_single(task):
            sku, url, dest = task
            try:
                if should_stop is not None and should_stop():
                    return None
                try:
                    import match_image_ai
                    if getattr(match_image_ai, "stop_requested", False):
                        return None
                except Exception:
                    pass
                r = session.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    # Check if the response is actually an image and not an HTML error page
                    content_type = r.headers.get('Content-Type', '').lower()
                    if 'html' in content_type:
                        return f"Failed downloading SKU {sku}: CDN returned HTML instead of image"

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
                    return f"Failed downloading SKU {sku}: status code {r.status_code}"
            except Exception as e:
                return f"Failed downloading SKU {sku}: {e}"
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(download_single, task) for task in download_tasks]
            for completed, future in enumerate(as_completed(futures), 1):
                error = future.result()
                if error:
                    print(f"\n{error}")
                pct = int((completed / total) * 100)
                print(f"\r[Download Progress] {pct}% ({completed}/{total})", end="", flush=True)
        print("\nImage download complete.\n")
    else:
        print("All database images are already cached locally.\n")

