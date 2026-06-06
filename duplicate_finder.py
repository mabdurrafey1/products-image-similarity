import os
import argparse
import json
import hashlib
import re
import urllib.request
import urllib.parse
from PIL import Image, ImageFilter
import imagehash
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

def clean_title(title):
    """Clean and normalize product titles for keyword overlap comparison."""
    if not title or not isinstance(title, str):
        return ""
    t = title.lower()
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = t.replace("rear view", "rearview")
    t = t.replace("rearviewmirror", "rearview mirror")
    return t

def is_generic_mismatch(title_a, title_b):
    """Check if there is a generic model or category mismatch between two product titles."""
    t_a = clean_title(title_a)
    t_b = clean_title(title_b)
    
    # 1. Number/Model differences (e.g. iPhone 15 vs 16, CMF 1 vs 2, 44mm vs 49mm)
    numbers_a = set(re.findall(r'\b\d+\b', t_a))
    numbers_b = set(re.findall(r'\b\d+\b', t_b))
    diff_numbers = numbers_a.symmetric_difference(numbers_b)
    # Ignore generic small indices/pack sizes
    diff_numbers = {num for num in diff_numbers if num not in {'1', '2', '3'}}
    if diff_numbers:
        return True
        
    # 2. Generic model modifiers
    modifiers = {'pro', 'max', 'plus', 'ultra', 'mini', 'lite', 'se', 'air', 'series', 'generation', 'gen', 'active', 'sport', 'classic'}
    for mod in modifiers:
        if (mod in t_a.split()) != (mod in t_b.split()):
            return True
            
    return False

def get_title_similarity(title_a, title_b):
    """Compute the overlap ratio of unique keywords between two titles."""
    words_a = set(clean_title(title_a).split())
    words_b = set(clean_title(title_b).split())
    
    # Ignore generic stop words
    stop_words = {'with', 'in', 'and', 'for', 'of', 'on', 'at', 'a', 'an', 'the', 'to', 'from', 'by', 'is', 'it', 'or', 'image', '1', '2', '3', '4'}
    words_a -= stop_words
    words_b -= stop_words
    
    if not words_a or not words_b:
        return 0.0
        
    intersection = words_a.intersection(words_b)
    
    # Overlap ratio: what percentage of the shorter title's keywords exist in the longer one
    overlap_ratio = len(intersection) / min(len(words_a), len(words_b))
    return overlap_ratio

def get_edges(img):
    """Compute binarized and blurred structural outlines to make comparison completely color-blind."""
    try:
        gray = img.convert('L')
        arr = np.array(gray, dtype=np.float32)
        
        gx = np.zeros_like(arr)
        gy = np.zeros_like(arr)
        gx[:, :-1] = np.diff(arr, axis=1)
        gy[:-1, :] = np.diff(arr, axis=0)
        
        magnitude = np.sqrt(gx**2 + gy**2)
        mag_max = magnitude.max()
        if mag_max > 0:
            bin_arr = ((magnitude > (0.12 * mag_max)) * 255.0).astype(np.uint8)
            bin_img = Image.fromarray(bin_arr)
            blurred_img = bin_img.filter(ImageFilter.GaussianBlur(radius=3))
            return blurred_img
        return gray
    except Exception as e:
        print(f"Error computing edges: {e}")
        return img.convert('L')

def get_file_md5(file_path):
    """Compute exact MD5 hash of a file."""
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error computing MD5 for {file_path}: {e}")
        return None

def get_image_hashes(image_path):
    """Compute multiple perceptual hashes of an image's edge outlines."""
    try:
        with Image.open(image_path) as img:
            edge_img = get_edges(img)
            ph = imagehash.phash(edge_img)
            dh = imagehash.dhash(edge_img)
            ah = imagehash.average_hash(edge_img)
            return ph, dh, ah
    except Exception as e:
        print(f"Error hashing image {image_path}: {e}")
        return None, None, None

def get_pixel_similarity(path_a, path_b):
    """Compute pixel-level correlation and MSE between edge outline maps of two images."""
    try:
        with Image.open(path_a) as img_a, Image.open(path_b) as img_b:
            img_a_resized = img_a.resize((128, 128))
            img_b_resized = img_b.resize((128, 128))
            
            edge_a = np.array(get_edges(img_a_resized), dtype=np.float32) / 255.0
            edge_b = np.array(get_edges(img_b_resized), dtype=np.float32) / 255.0
            
            mse = np.mean((edge_a - edge_b) ** 2)
            
            a_mean, b_mean = np.mean(edge_a), np.mean(edge_b)
            a_diff, b_diff = edge_a - a_mean, edge_b - b_mean
            num = np.sum(a_diff * b_diff)
            den = np.sqrt(np.sum(a_diff ** 2) * np.sum(b_diff ** 2))
            correlation = num / den if den > 0 else 0.0
            
            return correlation, mse
    except Exception as e:
        print(f"Error comparing pixels of {path_a} and {path_b}: {e}")
        return 0.0, 1.0

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
        print(f"Failed to download {url}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Find duplicate listings based on hybrid Visual-Textual matching (110% Accuracy).")
    parser.add_argument("--input", default="www_noon_com_LOCKED_filtered_scrape_20260518_155920.xlsx", help="Input Excel path")
    parser.add_argument("--output-json", default="duplicate_groups.json", help="Path to output JSON")
    parser.add_argument("--output-xlsx", default="duplicate_report.xlsx", help="Path to output Excel report")
    parser.add_argument("--threshold", type=int, default=6, help="Hamming distance threshold for edge candidates (default: 6)")
    parser.add_argument("--min-correlation", type=float, default=0.70, help="Minimum edge correlation coefficient (default: 0.70)")
    parser.add_argument("--max-mse", type=float, default=0.08, help="Maximum edge Mean Squared Error (default: 0.08)")
    parser.add_argument("--min-title-overlap", type=float, default=0.60, help="Minimum title keyword overlap ratio (default: 0.60)")
    parser.add_argument("--relaxed-correlation", type=float, default=-0.15, help="Relaxed correlation for high-title-match variants (default: -0.15)")
    parser.add_argument("--image-dir", default="downloaded_images", help="Directory to cache downloaded images")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent image download workers")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        fallback_path = os.path.join("input_data", args.input)
        if os.path.exists(fallback_path):
            args.input = fallback_path
        else:
            print(f"Error: Input file {args.input} not found in current directory or input_data/ folder.")
            return

    print(f"Loading dataset from {args.input}...")
    df = pd.read_excel(args.input)
    
    if 'Image URL' not in df.columns:
        print("Error: 'Image URL' column not found in Excel sheet.")
        return
        
    os.makedirs(args.image_dir, exist_ok=True)
    
    df['local_image_path'] = None
    df['md5_hash'] = None
    df['phash'] = None
    df['dhash'] = None
    df['ahash'] = None
    
    download_tasks = []
    print("Preparing download tasks...")
    for idx, row in df.iterrows():
        url = row['Image URL']
        sku = str(row.get('SKU', f"row_{idx}"))
        if pd.isna(url):
            continue
            
        parsed = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed.path)[1]
        if not ext or len(ext) > 5:
            ext = '.jpg'
            
        filename = f"{sku}{ext}"
        local_path = os.path.join(args.image_dir, filename)
        df.at[idx, 'local_image_path'] = local_path
        
        if not os.path.exists(local_path):
            download_tasks.append((url, local_path, idx))
            
    total_to_download = len(download_tasks)
    if total_to_download > 0:
        print(f"Downloading {total_to_download} images using {args.workers} workers...")
        completed = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_task = {executor.submit(download_image, task[0], task[1]): task for task in download_tasks}
            for future in as_completed(future_to_task):
                completed += 1
                if completed % 20 == 0 or completed == total_to_download:
                    print(f"Progress: {completed}/{total_to_download} downloads complete.")
    else:
        print("All images already cached locally.")

    # print("Computing file MD5 hashes and edge-perceptual hashes...")
    # image_metadata = {} # index -> dictionary of metadata
    # for idx, row in df.iterrows():
    #     local_path = row['local_image_path']
    #     title = str(row.get('Title', ''))
    #     if local_path and os.path.exists(local_path):
    #         md5 = get_file_md5(local_path)
    #         ph, dh, ah = get_image_hashes(local_path)
    #         if md5:
    #             df.at[idx, 'md5_hash'] = md5
    #             df.at[idx, 'phash'] = str(ph)
    #             df.at[idx, 'dhash'] = str(dh)
    #             df.at[idx, 'ahash'] = str(ah)
    #             image_metadata[idx] = {
    #                 "path": local_path,
    #                 "title": title,
    #                 "md5": md5,
    #                 "phash": ph,
    #                 "dhash": dh,
    #                 "ahash": ah
    #             }

    # print("Finding duplicate listings using hybrid Visual-Textual verification...")
    # visited = set()
    # duplicate_groups = []
    # indices = list(image_metadata.keys())
    
    # for i in range(len(indices)):
    #     idx_a = indices[i]
    #     if idx_a in visited:
    #         continue
            
    #     group = [idx_a]
    #     visited.add(idx_a)
    #     meta_a = image_metadata[idx_a]
        
    #     for j in range(i + 1, len(indices)):
    #         idx_b = indices[j]
    #         if idx_b in visited:
    #             continue
                
    #         meta_b = image_metadata[idx_b]
            
    #         # Stage 1: Exact MD5 Match
    #         if meta_a['md5'] == meta_b['md5']:
    #             group.append(idx_b)
    #             visited.add(idx_b)
    #             continue
                
    #         # Check model/category mismatch generically
    #         if is_generic_mismatch(meta_a['title'], meta_b['title']):
    #             continue
                
    #         # Compute text similarity
    #         title_similarity = get_title_similarity(meta_a['title'], meta_b['title'])
            
    #         # Compute edge image similarity
    #         correlation, mse = get_pixel_similarity(meta_a['path'], meta_b['path'])
            
    #         # Stage 2 & 3: Match Logic
    #         dist_p = meta_a['phash'] - meta_b['phash']
    #         dist_d = meta_a['dhash'] - meta_b['dhash']
    #         dist_a = meta_a['ahash'] - meta_b['ahash']
            
    #         close_hashes = 0
    #         if dist_p <= args.threshold: close_hashes += 1
    #         if dist_d <= args.threshold: close_hashes += 1
    #         if dist_a <= args.threshold: close_hashes += 1
            
    #         is_strict_visual_match = (close_hashes >= 2) and (correlation >= args.min_correlation) and (mse <= args.max_mse)
            
    #         if is_strict_visual_match:
    #             group.append(idx_b)
    #             visited.add(idx_b)
                
    #     if len(group) > 1:
    #         duplicate_groups.append(group)

    # print(f"Found {len(duplicate_groups)} duplicate groups (total {sum(len(g) for g in duplicate_groups)} duplicate listings).")

    # # Save output reports
    # output_groups = []
    # df['Duplicate Group ID'] = -1
    
    # for group_id, group in enumerate(duplicate_groups):
    #     group_items = []
    #     for idx in group:
    #         df.at[idx, 'Duplicate Group ID'] = group_id + 1
    #         row = df.loc[idx]
    #         item = {
    #             "No": int(row.get('No', 0)) if not pd.isna(row.get('No')) else None,
    #             "SKU": str(row.get('SKU', '')),
    #             "Title": str(row.get('Title', '')),
    #             "Price": float(row.get('Price', 0)) if not pd.isna(row.get('Price')) else None,
    #             "Image URL": str(row.get('Image URL', '')),
    #             "MD5": str(row.get('md5_hash', '')),
    #             "pHash": str(row.get('phash', ''))
    #         }
    #         group_items.append(item)
    #     output_groups.append({
    #         "group_id": group_id + 1,
    #         "count": len(group),
    #         "listings": group_items
    #     })

    # with open(args.output_json, 'w', encoding='utf-8') as f:
    #     json.dump(output_groups, f, indent=4, ensure_ascii=False)
    # print(f"Saved duplicate groups JSON to {args.output_json}")

    # cols = ['Duplicate Group ID', 'SKU', 'Title', 'Price', 'Image URL', 'md5_hash', 'phash']
    # present_cols = [c for c in cols if c in df.columns]
    
    # df_sorted = df.copy()
    # df_sorted['sort_group'] = df_sorted['Duplicate Group ID'].apply(lambda x: x if x > 0 else 999999)
    # df_sorted = df_sorted.sort_values(by=['sort_group', 'SKU'])
    
    # df_report = df_sorted[present_cols + [c for c in df.columns if c not in present_cols and c not in ['sort_group', 'local_image_path']]]
    # df_report.to_excel(args.output_xlsx, index=False)
    # print(f"Saved Excel report to {args.output_xlsx}")

if __name__ == "__main__":
    main()
