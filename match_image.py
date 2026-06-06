import os
import argparse
import json
import hashlib
import urllib.request
import urllib.parse
from PIL import Image, ImageFilter
import imagehash
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def get_image_hashes_from_image(img):
    """Compute multiple perceptual hashes of a PIL image's edge outlines."""
    try:
        edge_img = get_edges(img)
        ph = imagehash.phash(edge_img)
        dh = imagehash.dhash(edge_img)
        ah = imagehash.average_hash(edge_img)
        return ph, dh, ah
    except Exception as e:
        print(f"Error hashing image: {e}")
        return None, None, None

def get_image_hashes(image_path):
    """Compute multiple perceptual hashes of a file's edge outlines."""
    try:
        with Image.open(image_path) as img:
            return get_image_hashes_from_image(img)
    except Exception as e:
        print(f"Error hashing image {image_path}: {e}")
        return None, None, None

def get_pixel_similarity(img_a, path_b):
    """Compute pixel-level correlation and MSE between edge outline maps of a query image and a database image."""
    try:
        with Image.open(path_b) as img_b:
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
        print(f"Error comparing pixels: {e}")
        return 0.0, 1.0

def download_image(url, save_path):
    """Download image from URL with custom User-Agent."""
    if not url or not isinstance(url, str):
        return False
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Find duplicates in the dataset for a specific local query image.")
    parser.add_argument("--query", required=True, help="Path to your local query image file")
    parser.add_argument("--input", default="input_data", help="Dataset Excel path or directory containing Excel files (default: input_data)")
    parser.add_argument("--output", default="search_results.json", help="Path to save search results JSON")
    parser.add_argument("--threshold", type=int, default=6, help="Hamming distance threshold for edge hashes (default: 6)")
    parser.add_argument("--min-correlation", type=float, default=0.70, help="Minimum outline correlation coefficient (default: 0.70)")
    parser.add_argument("--max-mse", type=float, default=0.08, help="Maximum outline Mean Squared Error (default: 0.08)")
    parser.add_argument("--image-dir", default="downloaded_images", help="Directory to cache downloaded images")
    parser.add_argument("--workers", type=int, default=10, help="Number of download workers")
    args = parser.parse_args()

    if not os.path.exists(args.query):
        print(f"Error: Query image '{args.query}' not found.")
        return

    # Check if input path exists, or try falling back to input_data folder
    if not os.path.exists(args.input):
        fallback_path = os.path.join("input_data", args.input)
        if os.path.exists(fallback_path):
            args.input = fallback_path
        else:
            print(f"Error: Input dataset path '{args.input}' not found.")
            return

    print("Analyzing query image...")
    query_md5 = get_file_md5(args.query)
    try:
        query_img = Image.open(args.query)
    except Exception as e:
        print(f"Error opening query image: {e}")
        return

    q_ph, q_dh, q_ah = get_image_hashes_from_image(query_img)
    if q_ph is None:
        print("Error: Could not compute signatures for the query image.")
        return

    print(f"Query loaded. MD5: {query_md5} | pHash: {q_ph}")

    # Load dataset to map filenames back to SKUs/rows
    if os.path.isdir(args.input):
        import glob
        excel_files = glob.glob(os.path.join(args.input, "*.xlsx"))
        if not excel_files:
            print(f"Error: No Excel (.xlsx) files found in directory '{args.input}'")
            return
        print(f"Loading {len(excel_files)} Excel files from '{args.input}'...")
        dfs = []
        for f in sorted(excel_files):
            try:
                temp_df = pd.read_excel(f)
                temp_df['Source File'] = os.path.basename(f)
                dfs.append(temp_df)
            except Exception as e:
                print(f"Warning: Could not read '{f}': {e}")
        if not dfs:
            print(f"Error: Could not load any Excel files from directory '{args.input}'")
            return
        df = pd.concat(dfs, ignore_index=True)
    else:
        df = pd.read_excel(args.input)
        df['Source File'] = os.path.basename(args.input)
    
    if 'Image URL' not in df.columns:
        print("Error: 'Image URL' column not found in Excel.")
        return
        
    os.makedirs(args.image_dir, exist_ok=True)
    
    # Download missing database images
    download_tasks = []
    df['local_image_path'] = None
    for idx, row in df.iterrows():
        url = row['Image URL']
        sku = str(row.get('SKU', f"row_{idx}"))
        if pd.isna(url):
            continue
            
        parsed = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed.path)[1]
        if not ext or len(ext) > 5:
            ext = '.jpg'
            
        local_path = os.path.join(args.image_dir, f"{sku}{ext}")
        df.at[idx, 'local_image_path'] = local_path
        
        if not os.path.exists(local_path):
            download_tasks.append((url, local_path))

    if download_tasks:
        print(f"Downloading {len(download_tasks)} missing images...")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            executor.map(lambda t: download_image(t[0], t[1]), download_tasks)

    print("Searching for visually similar listings in the dataset...")
    matches = []
    
    for idx, row in df.iterrows():
        local_path = row['local_image_path']
        if not local_path or not os.path.exists(local_path):
            continue
            
        # Stage 1: Exact MD5 Match
        local_md5 = get_file_md5(local_path)
        if query_md5 and local_md5 == query_md5:
            print(f"-> Exact file match found at row {idx+1} (SKU: {row.get('SKU')})")
            matches.append((idx, 1.0, 0.0))
            continue
            
        # Stage 2: Perceptual Hashing check
        ph, dh, ah = get_image_hashes(local_path)
        if ph is None:
            continue
            
        dist_p = q_ph - ph
        dist_d = q_dh - dh
        dist_a = q_ah - ah
        
        close_hashes = 0
        if dist_p <= args.threshold: close_hashes += 1
        if dist_d <= args.threshold: close_hashes += 1
        if dist_a <= args.threshold: close_hashes += 1
        
        if close_hashes >= 2:
            # Stage 3: Pixel-level outline correlation check
            correlation, mse = get_pixel_similarity(query_img, local_path)
            if correlation >= args.min_correlation and mse <= args.max_mse:
                print(f"-> Visual match found at row {idx+1} (SKU: {row.get('SKU')}, Correlation: {correlation:.2f})")
                matches.append((idx, correlation, mse))

    print(f"\nSearch complete. Found {len(matches)} matching listings.")

    results_data = []
    if matches:
        print("\nMatching Listings:")
        print("-" * 80)
        for idx, corr, mse in matches:
            row = df.loc[idx]
            sku = row.get('SKU', '')
            title = row.get('Title', '')
            price = row.get('Price', '')
            img_url = row.get('Image URL', '')
            source_file = row.get('Source File', 'Unknown')
            print(f"Source: {source_file} | Row: {idx+1} | SKU: {sku} | Price: {price} | Match Confidence: {corr:.2f}")
            print(f"Title: {title}")
            print(f"Image: {img_url}")
            print("-" * 80)
            
            results_data.append({
                "Source File": str(source_file),
                "Row": int(idx + 1),
                "SKU": str(sku),
                "Title": str(title),
                "Price": float(price) if not pd.isna(price) else None,
                "Image URL": str(img_url),
                "Match Correlation": float(corr),
                "MSE": float(mse)
            })
    else:
        print("No duplicate listings found for this image.")

    # Save to file
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=4, ensure_ascii=False)
    print(f"Saved results to {args.output}")

if __name__ == "__main__":
    main()
