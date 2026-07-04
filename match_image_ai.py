from downloader import download_missing_images
import os
import argparse
import json
import re
import pandas as pd

import threading

# Legacy global flag kept for backward compatibility with CLI usage.
# GUI tabs pass a per-tab threading.Event instead.
stop_requested = False
_stop_event_local = threading.local()

# Lock used ONLY for the one-time rclip indexing step.
# After indexing completes, all tabs search in parallel with no lock.
_rclip_lock = threading.Lock()
_rclip_indexed_dirs = set()

def check_stop():
    """Raise StopRequested if the current tab's stop event is set, or the global flag is True."""
    local_event = getattr(_stop_event_local, 'event', None)
    if local_event is not None:
        if local_event.is_set():
            raise RuntimeError("StopRequested")
    elif stop_requested:
        raise RuntimeError("StopRequested")

def clean_title(title):
    """Clean and normalize product titles for keyword overlap comparison."""
    if not title or not isinstance(title, str):
        return ""
    t = title.lower()
    t = re.sub(r'[^a-z0-9\s-]', ' ', t)
    t = t.replace("rear view", "rearview")
    t = t.replace("rearviewmirror", "rearview mirror")
    return t

def extract_models(text):
    """Extract alphanumeric model identifiers (e.g., X6, D007, R36S)."""
    words = re.findall(r'\b[a-z0-9-]+\b', text)
    models = set()
    for w in words:
        if w.isdigit():
            continue
        has_digit = any(c.isdigit() for c in w)
        has_alpha = any(c.isalpha() for c in w)
        if has_digit and has_alpha:
            models.add(w)
    return models

def is_generic_mismatch(title_a, title_b):
    """Check if there is a generic model or category mismatch between two product titles."""
    t_a = clean_title(title_a)
    t_b = clean_title(title_b)
    
    # 1. Alphanumeric model identifier mismatch (e.g., X6 vs D007 vs R36S vs M21)
    models_a = extract_models(t_a)
    models_b = extract_models(t_b)
    if models_a and models_b:
        # If both contain models but they don't overlap, it's a mismatch
        if not models_a.intersection(models_b):
            return True

    # 2. Number/Model differences (normalized as integers to handle commas/zeros)
    numbers_a = {int(num) for num in re.findall(r'\b\d+\b', t_a)}
    numbers_b = {int(num) for num in re.findall(r'\b\d+\b', t_b)}
    diff_numbers = numbers_a.symmetric_difference(numbers_b)
    
    # Ignore common spec numbers (game count, storage, battery, dimensions, etc.)
    spec_numbers = {
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 20, 22, 23, 24, 25, 26, 30, 32, 36, 40, 45, 50, 60, 64, 80, 128, 152, 203, 256, 268, 
        500, 512, 520, 666, 1000, 3500, 4000, 6000, 10000, 15000, 18000, 20000, 30000, 40000
    }
    diff_numbers = {num for num in diff_numbers if num not in spec_numbers}
    if diff_numbers:
        return True
        
    # 3. Generic model modifiers
    modifiers = {
        'pro', 'max', 'plus', 'ultra', 'mini', 'lite', 'se', 'air', 'series', 
        'generation', 'gen', 'active', 'sport'
    }
    words_a = set(t_a.split())
    words_b = set(t_b.split())
    for mod in modifiers:
        if (mod in words_a) != (mod in words_b):
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
    overlap_ratio = len(intersection) / min(len(words_a), len(words_b))
    return overlap_ratio


def normalize_dataframe(df):
    """Normalize column names from the final Excel layout format."""
    mapping = {
        'sku': 'SKU',
        'Sku': 'SKU',
        'Product Title': 'Title',
        'Main Image URL': 'Image URL'
    }
    
    rename_dict = {}
    assigned_targets = set(df.columns)
    
    preferred_order = ['sku', 'Sku', 'Product Title', 'Main Image URL']
    
    for col in preferred_order:
        if col in df.columns and col in mapping:
            target = mapping[col]
            if target not in assigned_targets:
                rename_dict[col] = target
                assigned_targets.add(target)
                
    if rename_dict:
        df = df.rename(columns=rename_dict)
    return df

def load_excel_with_sheets(file_path):
    """Load the Best_One_Row_Per_SKU sheet from an Excel file, falling back to default loading."""
    try:
        xls = pd.ExcelFile(file_path)
        sheet_name = None
        if 'Best_One_Row_Per_SKU' in xls.sheet_names:
            sheet_name = 'Best_One_Row_Per_SKU'
            
        if sheet_name:
            print(f"Detected multi-sheet Excel in '{os.path.basename(file_path)}'. Loading sheet: '{sheet_name}'")
            df = pd.read_excel(xls, sheet_name=sheet_name)
        else:
            df = pd.read_excel(xls)
        return normalize_dataframe(df)
    except Exception as e:
        print(f"Error reading Excel file '{file_path}': {e}")
        # Fallback to direct reading
        df = pd.read_excel(file_path)
        return normalize_dataframe(df)

def load_dataset(input_path):
    """Load dataset Excel files from a single file or a directory of files."""
    if os.path.isdir(input_path):
        import glob
        excel_files = glob.glob(os.path.join(input_path, "*.xlsx"))
        if not excel_files:
            raise FileNotFoundError(f"No Excel (.xlsx) files found in directory '{input_path}'")
        print(f"Loading {len(excel_files)} Excel files from '{input_path}'...")
        dfs = []
        for f in sorted(excel_files):
            try:
                temp_df = load_excel_with_sheets(f)
                temp_df['Source File'] = os.path.basename(f)
                dfs.append(temp_df)
            except Exception as e:
                print(f"Warning: Could not read '{f}': {e}")
        if not dfs:
            raise ValueError(f"Could not load any Excel files from directory '{input_path}'")
        return pd.concat(dfs, ignore_index=True)
    else:
        df = load_excel_with_sheets(input_path)
        df['Source File'] = os.path.basename(input_path)
        return df

def resolve_reference_title(df, query_path, query_title, visual_scores=None):
    """Retrieve or fallback to baseline reference title for similarity checks."""
    if query_title:
        print(f"Baseline model reference specified by user: '{query_title}'\n")
        return query_title
        
    # Split query_path to check multiple SKUs in case of multiple query images
    query_list = [q.strip() for q in query_path.split(";") if q.strip()]
    for q in query_list:
        query_basename = os.path.splitext(os.path.basename(q))[0]
        query_matching_rows = df[df['SKU'].astype(str) == query_basename]
        if not query_matching_rows.empty:
            reference_title = str(query_matching_rows.iloc[0].get('Title', ''))
            print(f"Baseline model reference determined from query filename ({query_basename}): '{reference_title}'\n")
            return reference_title

    # Fallback to the highest visual match title if not resolved yet
    if visual_scores:
        sorted_visual = sorted(visual_scores.items(), key=lambda x: x[1], reverse=True)
        for sku, score in sorted_visual:
            matching_rows = df[df['SKU'].astype(str) == sku]
            if not matching_rows.empty:
                reference_title = str(matching_rows.iloc[0].get('Title', ''))
                print(f"Baseline model reference determined from Rank 1 visual match: '{reference_title}'\n")
                return reference_title
                
    return None

def run_visual_search(image_dir, query_path, no_indexing=False):
    """Run rclip visual search in-process to get visual similarity scores.
    
    Uses an index-once-search-parallel strategy:
    - The first tab to reach here runs ensure_index() (serialized via lock).
    - All tabs then open independent read-only DB connections and search
      concurrently with no lock.
    """
    from rclip.main import init_rclip
    
    # Split query_path by semicolon to support multiple reference images
    query_list = [q.strip() for q in query_path.split(";") if q.strip()]
    if not query_list:
        print("Error: No valid query paths provided.")
        return {}
        
    abs_image_dir = os.path.abspath(image_dir)
    abs_query_paths = [os.path.abspath(q) for q in query_list]
    visual_scores = {}
    print(f"Querying AI model for visual similarity scores (in-process) using {len(abs_query_paths)} reference images...")
    try:
        # Step 1: Ensure the image directory is indexed exactly once (serialized).
        # Uses double-checked locking so only the very first caller blocks.
        if not no_indexing and abs_image_dir not in _rclip_indexed_dirs:
            with _rclip_lock:
                if abs_image_dir not in _rclip_indexed_dirs:
                    print(f"[Index] Running one-time image index for directory...")
                    idx_rclip, idx_model, idx_db = init_rclip(
                        working_directory=abs_image_dir,
                        indexing_batch_size=32,
                        no_indexing=False
                    )
                    idx_model.close()
                    idx_db.close()
                    _rclip_indexed_dirs.add(abs_image_dir)
                    print(f"[Index] Indexing complete.")

        # Step 2: Search in parallel — each tab opens its own DB connection
        # with no_indexing=True so ensure_index() is skipped entirely.
        rclip_instance, rclip_model, rclip_db = init_rclip(
            working_directory=abs_image_dir,
            indexing_batch_size=32,
            no_indexing=True
        )
        try:
            for q_path in abs_query_paths:
                check_stop()
                print(f"Processing query image: {os.path.basename(q_path)}")
                search_results = rclip_instance.search(
                    query=q_path,
                    directory=abs_image_dir,
                    top_k=2000
                )
                for item in search_results:
                    filename = os.path.basename(item.filepath)
                    sku = os.path.splitext(filename)[0].strip().upper()
                    # Take the maximum similarity score across all query images
                    if sku not in visual_scores or item.score > visual_scores[sku]:
                        visual_scores[sku] = item.score
        finally:
            rclip_model.close()
            rclip_db.close()
        print(f"Successfully loaded {len(visual_scores)} visual similarity scores.")
    except Exception as e:
        print(f"Warning: Could not run rclip visual search: {e}")
    return visual_scores

def run_semantic_text_search(df, reference_title, visual_scores, min_text_sim, strict=False):
    """Find products matching text criteria using semantic text similarity."""
    text_matches = []
    
    # Initialize CLIP model for semantic text similarity comparison
    print("Initializing CLIP text encoder for semantic text similarity...")
    try:
        from rclip.model import Model as RClipModel
        clip_model = RClipModel()
        clip_model.ensure_downloaded()
        ref_emb = clip_model.compute_text_features([reference_title])[0]
        print("CLIP text encoder successfully initialized.\n")
    except Exception as e:
        print(f"Warning: Could not initialize CLIP text model: {e}")
        return text_matches

    print("Performing text similarity search across all products in Excel...")
    
    # Step 1: Pre-filter by quick keyword overlap and check if visual score is available to reduce candidates
    candidates = []
    for idx, row in df.iterrows():
        check_stop()
        sku = str(row.get('SKU', '')).strip().upper()
        if sku not in visual_scores:
            continue
        title = str(row.get('Title', ''))
        if not title:
            continue
        
        # Simple keyword overlap pre-filter
        if get_title_similarity(reference_title, title) > 0.0:
            candidates.append((idx, row, title))
    
    print(f"Found {len(candidates)} candidate products with keyword overlap and visual score. Computing semantic similarity...")
    
    # Pre-extract model codes from the query reference
    query_models = extract_models(clean_title(reference_title))

    # Step 2: Batch compute text embeddings for candidates
    threshold = min_text_sim if min_text_sim > 0.0 else 0.70
    batch_size = 128
    for i in range(0, len(candidates), batch_size):
        check_stop()
        batch_candidates = candidates[i:i+batch_size]
        batch_titles = [item[2] for item in batch_candidates]
        
        try:
            batch_embs = clip_model.compute_text_features(batch_titles)
            for j, emb in enumerate(batch_embs):
                semantic_sim = float(ref_emb @ emb.T)
                idx, row, title = batch_candidates[j]
                
                # Check for exact model code overlap (Idea 1)
                candidate_models = extract_models(clean_title(title))
                model_match = bool(query_models.intersection(candidate_models))
                
                # Keep if semantic sim is high OR if it is an exact model match
                if semantic_sim >= threshold or model_match:
                    # Apply strict model check if enabled
                    if strict and is_generic_mismatch(reference_title, title):
                        continue
                        
                    text_matches.append({
                        "row": row,
                        "idx": idx,
                        "semantic_sim": semantic_sim
                    })
        except Exception as e:
            print(f"Warning: Error processing batch: {e}")
            
    return text_matches

def save_and_display_results(text_matches, visual_scores, output_path, top_limit, min_score=0.20):
    """Format, sort, display, and save results to JSON."""
    results_data = []
    if text_matches:
        print(f"Found {len(text_matches)} products matching text criteria. Attaching visual similarity scores...")
        for match in text_matches:
            row = match["row"]
            idx = match["idx"]
            semantic_sim = match["semantic_sim"]
            sku = str(row.get('SKU', '')).strip()
            sku_lookup = sku.upper()
            
            # Look up score from rclip visual search
            score = visual_scores.get(sku_lookup, None)
            if score is None or score < min_score:
                continue
            
            price = row.get('Price', '')
            source_file = row.get('Source File', 'Unknown')
            
            results_data.append({
                "Source File": str(source_file),
                "Row": int(idx + 1),
                "SKU": str(sku),
                "Title": str(row.get('Title', '')),
                "Price": float(price) if not pd.isna(price) else None,
                "AI Score": score,
                "Text Similarity": semantic_sim,
                "Image Filename": f"{sku}.jpg"
            })
            
        # Calculate max values to normalize both scores to [0, 1]
        max_visual = max(x["AI Score"] for x in results_data if x["AI Score"] is not None) if results_data else 1.0
        if max_visual <= 0:
            max_visual = 1.0
            
        max_text = max(x["Text Similarity"] for x in results_data if x["Text Similarity"] is not None) if results_data else 1.0
        if max_text <= 0:
            max_text = 1.0
 
        for item in results_data:
            vis = item["AI Score"] if item["AI Score"] is not None else 0.0
            norm_vis = vis / max_visual
            text_sim = item["Text Similarity"] if item["Text Similarity"] is not None else 0.0
            
            combined = (norm_vis * 0.5) + (text_sim * 0.5)
            item["Combined Score"] = combined
            
            # Tier-based sorting:
            # Tier 3 (very high visual match, score >= 1.2): sorted strictly by visual score
            # Tier 2 (very high text match, score >= 0.9): sorted strictly by text similarity
            # Tier 1 (otherwise): sorted by combined visual + text similarity
            if vis >= 1.2:
                item["Sort Key"] = (3, vis)
            elif text_sim >= 0.9:
                item["Sort Key"] = (2, text_sim)
            else:
                item["Sort Key"] = (1, combined)
 
        # Sort results descending by Sort Key tuple
        results_data.sort(key=lambda x: x["Sort Key"], reverse=True)
        
        # Limit the results saved to the user's requested top_limit
        results_data = results_data[:top_limit]
        
        # Assign rank based on final sorted order
        for rank_idx, item in enumerate(results_data, 1):
            item["Rank"] = rank_idx

    # Print top results
    print("\nAI Search Results (Text Search First, Then Visual Rank):")
    print("-" * 80)
    for item in results_data[:top_limit]:
        ai_score_str = f"{item['AI Score']:.3f}" if item['AI Score'] is not None else "None"
        print(f"Rank: {item['Rank']} | Source: {item['Source File']} | Row: {item['Row']} | SKU: {item['SKU']} | Price: {item['Price']} | AI Score: {ai_score_str} | Text Sim: {item['Text Similarity']:.3f}")
        print(f"Title: {item['Title']}")
        print(f"Image: {item['Source File']} (SKU: {item['SKU']})")
        print("-" * 80)

    if not results_data:
        print("No duplicate listings matching the criteria were found.")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=4, ensure_ascii=False)
    print(f"Saved AI search results to {output_path}")

def setup_global_input_data_dir():
    """
    Ensure the global input_data folder exists in the persistent user directory,
    and create a local symbolic link/junction in the current workspace so the
    relative 'input_data' paths resolve to the global folder transparently.
    """
    import platform
    sys_name = platform.system()
    
    home = os.path.expanduser("~")
    if sys_name == "Darwin":
        target_path = os.path.join(home, "Library", "Application Support", "DuplicateFinder", "input_data")
    elif sys_name == "Windows":
        target_path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder", "input_data")
    else:
        target_path = os.path.join(home, ".local", "share", "DuplicateFinder", "input_data")

    target_path = os.path.abspath(target_path)
    os.makedirs(target_path, exist_ok=True)

    local_path = os.path.abspath("input_data")
    
    # If local_path exists but is a real directory (not a symlink/junction), migrate its contents
    if os.path.exists(local_path) and not os.path.islink(local_path):
        # On Windows, junctions can also report isdir=True, but they are links
        # Check if it's not a junction by trying to readlink or check attribute
        is_real_dir = True
        if sys_name == "Windows":
            try:
                # If it can readlink or is a junction, it's not a real directory
                os.readlink(local_path)
                is_real_dir = False
            except Exception:
                pass
        
        if is_real_dir:
            import shutil
            try:
                for item in os.listdir(local_path):
                    s = os.path.join(local_path, item)
                    d = os.path.join(target_path, item)
                    if os.path.isdir(s):
                        if not os.path.exists(d):
                            shutil.copytree(s, d)
                    else:
                        if not os.path.exists(d):
                            shutil.copy(s, d)
                shutil.rmtree(local_path)
            except Exception as e:
                print(f"Warning: Could not migrate local input_data to global: {e}")

    # Recreate the symlink/junction if it doesn't exist
    if not os.path.exists(local_path) and not os.path.islink(local_path):
        try:
            if sys_name == "Windows":
                import subprocess
                subprocess.run(f'mklink /J "{local_path}" "{target_path}"', shell=True, check=True)
            else:
                os.symlink(target_path, local_path)
            print(f"Created local symlink 'input_data' -> '{target_path}'")
        except Exception as e:
            print(f"Warning: Could not create local link to input_data folder: {e}.")

    return target_path

def setup_global_image_dir(image_dir):
    """
    Resolve image_dir (custom or default 'downloaded_images') and ensure a local 
    symlink 'downloaded_images' is created pointing to it for relative HTML references.
    """
    import platform
    sys_name = platform.system()
    
    if image_dir == "downloaded_images":
        home = os.path.expanduser("~")
        if sys_name == "Darwin":
            target_path = os.path.join(home, "Library", "Application Support", "DuplicateFinder", "downloaded_images")
        elif sys_name == "Windows":
            target_path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder", "downloaded_images")
        else:
            target_path = os.path.join(home, ".local", "share", "DuplicateFinder", "downloaded_images")
    else:
        target_path = image_dir

    target_path = os.path.abspath(target_path)
    os.makedirs(target_path, exist_ok=True)

    local_path = os.path.abspath("downloaded_images")
    # If a symlink or file exists but points to a different location, recreate it
    # to match the newly selected user directory
    is_link = os.path.islink(local_path) or (sys_name == "Windows" and os.path.exists(local_path) and os.path.isdir(local_path) and not os.listdir(local_path))
    
    if os.path.exists(local_path) or os.path.islink(local_path):
        # If it's a symlink/junction, check if it points to target_path; if not, recreate it
        try:
            current_target = os.readlink(local_path) if os.path.islink(local_path) else ""
            if current_target and os.path.abspath(current_target) != target_path:
                if os.path.islink(local_path):
                    os.remove(local_path)
                else:
                    try:
                        os.rmdir(local_path)
                    except Exception:
                        os.remove(local_path)
        except Exception:
            pass

    if not os.path.exists(local_path) and not os.path.islink(local_path):
        try:
            if sys_name == "Windows":
                import subprocess
                subprocess.run(f'mklink /J "{local_path}" "{target_path}"', shell=True, check=True)
            else:
                os.symlink(target_path, local_path)
            print(f"Created local symlink 'downloaded_images' -> '{target_path}'")
        except Exception as e:
            print(f"Warning: Could not create local link to images folder: {e}. Indexing target path directly.")

    return target_path

def main(args=None, stop_event=None):
    """
    Run the AI duplicate search.

    Parameters
    ----------
    args : argparse.Namespace or None
        Pre-built argument namespace. When None (default / CLI usage), arguments
        are parsed from sys.argv as usual.
    stop_event : threading.Event or None
        Per-tab stop signal for GUI usage. When set the search aborts at the
        next check_stop() call without touching the global stop_requested flag.
        Pass None for CLI usage.
    """
    # Install per-thread stop event so check_stop() picks it up without globals
    _stop_event_local.event = stop_event
    setup_global_input_data_dir()

    try:
        if args is None:
            # CLI path — parse from sys.argv as before
            parser = argparse.ArgumentParser(description="AI-powered duplicate listing search using rclip (CLIP).")
            parser.add_argument("--query", default="/Users/mabdurrafey/Downloads/61ec5bb0-fe2c-4245-89fd-2f3e341e1e46.avif;/Users/mabdurrafey/Downloads/04429c3c-f63c-47a5-b709-06892999e7da.avif", help="Path to local query image")
            import glob
            excel_files = sorted(glob.glob("input_data/*.xlsx"))
            default_input = excel_files[0] if excel_files else "input_data"
            parser.add_argument("--input", default=default_input, help=f"Dataset Excel path or directory containing Excel files (default: {default_input})")
            parser.add_argument("--output", default="temp/search_results_ai.json", help="Path to save search results JSON")
            parser.add_argument("--top", type=int, default=500, help="Number of top visual matches to retrieve (default: 500)")
            parser.add_argument("--min-score", type=float, default=0.20, help="Minimum AI similarity score threshold (default: 0.20)")
            parser.add_argument("--min-text-sim", type=float, default=0.70, help="Minimum semantic text similarity score (default: 0.70, set to 0.0 to disable)")
            parser.add_argument("--strict", action="store_true", help="Enforce strict alphanumeric model code matching")
            parser.add_argument("--query-title", default="", help="Pasted title text to use as reference baseline for semantic text similarity")
            parser.add_argument("--image-dir", default="downloaded_images", help="Directory where database images are stored")
            parser.add_argument("--workers", type=int, default=10, help="Number of download workers")
            parser.add_argument("--no-indexing", action="store_true", help="Skip checking/indexing images in the target directory")
            parser.add_argument("--min-price", type=float, default=None, help="Minimum product price threshold")
            parser.add_argument("--max-price", type=float, default=None, help="Maximum product price threshold")
            args = parser.parse_args()

        # Ensure output parent directory exists if a path is specified
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Verify each query path exists individually
        query_paths = [q.strip() for q in args.query.split(";") if q.strip()]
        for q in query_paths:
            if not os.path.exists(q):
                print(f"Error: Query image '{q}' not found.")
                return

        # Check if input path exists, or try falling back to input_data folder
        input_path = args.input
        if not os.path.exists(input_path):
            fallback_path = os.path.join("input_data", input_path)
            if os.path.exists(fallback_path):
                input_path = fallback_path
            else:
                print(f"Error: Input dataset path '{input_path}' not found.")
                return

        args.image_dir = setup_global_image_dir(args.image_dir)

        # 1. Load spreadsheet database
        try:
            df = load_dataset(input_path)
        except Exception as e:
            print(f"Error loading dataset: {e}")
            return

        # Filter by Price Range if specified
        if (args.min_price is not None) or (args.max_price is not None):
            if 'Price' in df.columns:
                df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
                original_len = len(df)
                if args.min_price is not None:
                    print(f"Filtering database: Min Price >= {args.min_price} AED")
                    df = df[df['Price'] >= args.min_price]
                if args.max_price is not None:
                    print(f"Filtering database: Max Price <= {args.max_price} AED")
                    df = df[df['Price'] <= args.max_price]
                print(f"Price filtering complete: kept {len(df)} of {original_len} products.")
            else:
                print("Warning: 'Price' column not found in dataset. Price filtering skipped.")

        # 2. Resolve query reference title for similarity checks
        reference_title = resolve_reference_title(df, args.query, args.query_title)

        check_stop()

        # 4. Filter downloader queue by title overlap
        download_df = df
        if reference_title:
            print("Pre-filtering database to download images only for keyword-overlapping products...")
            matching_indices = []
            for idx, row in df.iterrows():
                check_stop()
                title = str(row.get('Title', ''))
                if title and get_title_similarity(reference_title, title) > 0.0:
                    matching_indices.append(idx)
            if matching_indices:
                download_df = df.loc[matching_indices]
                print(f"Filtered download queue: {len(download_df)} products with keyword overlap (down from {len(df)} total).")
            else:
                print("Warning: No products found with keyword overlap. Downloading all missing images as fallback.")

        check_stop()

        # 5. Automatically download missing images
        download_missing_images(download_df, image_dir=args.image_dir, max_workers=args.workers)

        check_stop()

        # 6. Run visual similarity search (in-process rclip)
        visual_scores = run_visual_search(args.image_dir, args.query, no_indexing=args.no_indexing)

        check_stop()

        # 7. Fallback to Rank 1 match if reference title wasn't found earlier
        if not reference_title and visual_scores:
            reference_title = resolve_reference_title(df, args.query, args.query_title, visual_scores)

        check_stop()

        # 8. Run semantic text search
        text_matches = []
        if reference_title:
            text_matches = run_semantic_text_search(df, reference_title, visual_scores, args.min_text_sim, args.strict)

        check_stop()

        # 9. Format, sort, save and print results
        max_score = max(visual_scores.values()) if visual_scores else 0.0
        dynamic_min_score = max(args.min_score, max_score - 0.45)
        print(f"Top visual score: {max_score:.3f} | Dynamic visual threshold: {dynamic_min_score:.3f}")
        save_and_display_results(text_matches, visual_scores, args.output, args.top, dynamic_min_score)

    finally:
        # Always clear the per-thread stop event when done
        _stop_event_local.event = None

if __name__ == "__main__":
    main()
