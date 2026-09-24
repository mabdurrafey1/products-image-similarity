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

# Lock used ONLY for re-indexing when new images appear.
# Search is always fully parallel with independent DB connections.
_rclip_lock = threading.Lock()
_rclip_file_count = {}  # abs_image_dir → file count at time of last index

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
        'Main Image URL': 'Image URL',
        'PartnerSKU': 'psku',
        'PartnerSku': 'psku',
        'partner_sku': 'psku',
        'Partner SKU': 'psku',
        'PSKU': 'psku',
        'psku': 'psku'
    }
    
    rename_dict = {}
    assigned_targets = set(df.columns)
    
    preferred_order = ['sku', 'Sku', 'Product Title', 'Main Image URL', 'PartnerSKU', 'PartnerSku', 'partner_sku', 'Partner SKU', 'PSKU', 'psku']
    
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
    """Load dataset Excel files from a single file, a directory, or multiple files
    (either a list of paths or a single ';'-separated string of paths)."""
    if isinstance(input_path, (list, tuple)):
        paths = list(input_path)
    else:
        paths = [p.strip() for p in str(input_path).split(";") if p.strip()]

    if len(paths) > 1:
        print(f"Loading {len(paths)} selected Excel files...")
        dfs = []
        for p in paths:
            try:
                dfs.append(load_dataset(p))
            except Exception as e:
                print(f"Warning: Could not load '{p}': {e}")
        if not dfs:
            raise ValueError("Could not load any of the selected dataset files.")
        return pd.concat(dfs, ignore_index=True)

    input_path = paths[0] if paths else input_path

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
    
    Uses a stale-check index, search-parallel strategy:
    - Checks if directory file count has changed since last index.
    - If changed, index is updated incrementally (serialized via lock).
    - If not changed, skips indexing and runs search in parallel with no lock.
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
        # Step 1: Check if new files have been added since the last indexing run.
        current_count = 0
        if os.path.exists(abs_image_dir):
            try:
                current_count = len(os.listdir(abs_image_dir))
            except Exception:
                pass
                
        last_indexed_count = _rclip_file_count.get(abs_image_dir, -1)
        
        if not no_indexing and current_count != last_indexed_count:
            with _rclip_lock:
                # Double-check inside lock
                current_count = 0
                if os.path.exists(abs_image_dir):
                    try:
                        current_count = len(os.listdir(abs_image_dir))
                    except Exception:
                        pass
                last_indexed_count = _rclip_file_count.get(abs_image_dir, -1)
                
                if current_count != last_indexed_count:
                    print(f"[Index] Images count changed ({last_indexed_count} -> {current_count}). Running incremental index...")
                    idx_rclip, idx_model, idx_db = init_rclip(
                        working_directory=abs_image_dir,
                        indexing_batch_size=32,
                        no_indexing=False
                    )
                    idx_model.close()
                    idx_db.close()
                    _rclip_file_count[abs_image_dir] = current_count
                    print(f"[Index] Indexing complete.")

        # Step 2: Search in parallel — each tab opens its own DB connection
        # with no_indexing=True so ensure_index() is skipped entirely.
        # Wrapped in _rclip_lock to prevent concurrent ONNX/CoreML model loading deadlocks.
        with _rclip_lock:
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

def run_semantic_text_search(df, reference_title, visual_scores, min_text_sim, strict=False,
                             min_strong_text=0.85):
    """Find products matching text criteria using semantic text similarity."""
    text_matches = []
    
    # Initialize CLIP model for semantic text similarity comparison
    print("Initializing CLIP text encoder for semantic text similarity...")
    try:
        with _rclip_lock:
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
        # Deliberately not requiring a visual score here. A product whose image was never downloaded
        # has no score at all, and skipping it meant its title was never even read -- so a listing
        # that named the same product word for word could not be found for want of a picture.
        title = str(row.get('Title', ''))
        if not title:
            continue
        
        # Simple keyword overlap pre-filter
        if get_title_similarity(reference_title, title) > 0.0:
            candidates.append((idx, row, title))
    
    print(f"Found {len(candidates)} candidate products with keyword overlap. Computing semantic similarity...")
    
    # Pre-extract model codes from the query reference
    query_models = extract_models(clean_title(reference_title))

    # Step 2: Batch compute text embeddings for candidates
    # A title good enough to be kept on its own has to get through this stage first, because the
    # Keep-on-Title bar is not applied until save_and_display_results. Collecting at the text bar
    # alone meant the lower of the two sliders could never be reached: setting Keep-on-Title below
    # the text threshold did nothing, because those rows were already gone.
    text_bar = min_text_sim if min_text_sim > 0.0 else 0.70
    threshold = min(text_bar, min_strong_text)
    batch_size = 128
    # Embedding every candidate title is the longest silent stretch of a run on a large catalog,
    # so it reports as it goes. The carriage return keeps it redrawing one line, which is what the
    # GUI reads the counts off.
    total_candidates = len(candidates)
    processed = 0
    for i in range(0, len(candidates), batch_size):
        check_stop()
        batch_candidates = candidates[i:i+batch_size]
        batch_titles = [item[2] for item in batch_candidates]

        processed += len(batch_candidates)
        pct = int(processed * 100 / total_candidates) if total_candidates else 100
        print(f"\rMatching titles: {processed}/{total_candidates} ({pct}%)", end="", flush=True)
        
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

    if total_candidates:
        print()
    return text_matches

def find_visual_only_matches(df, visual_scores, covered_skus, min_score):
    """Products whose picture alone clears the bar, however their title reads.

    run_semantic_text_search never even considers a title with zero keyword overlap with the
    reference title -- so this is the only path back for a listing that is the same product,
    photographed the same way, but described in different words. Every row here carries no text
    similarity at all (never computed, not zero), which is what lets the report say so honestly
    instead of implying a title was checked and failed.
    """
    if not visual_scores:
        return []
    sku_to_idx = {}
    for idx, row in df.iterrows():
        sku = str(row.get('SKU', '')).strip().upper()
        if sku and sku not in sku_to_idx:
            sku_to_idx[sku] = idx
    matches = []
    for sku, score in visual_scores.items():
        if score < min_score or sku in covered_skus:
            continue
        idx = sku_to_idx.get(sku)
        if idx is None:
            continue
        matches.append({"row": df.loc[idx], "idx": idx, "semantic_sim": None})
    return matches

def save_and_display_results(text_matches, visual_scores, output_path, top_limit, min_score=0.20,
                             strong_text=0.85, priority_keywords=None):
    """Format, sort, display, and save results to JSON.

    A result survives on either evidence, not on the picture alone. `min_score` is the visual bar,
    raised as the run goes on to sit near the best image match; `strong_text` is the bar a title has
    to clear to be kept in spite of its picture -- in spite of it, so a title at or above that bar
    is kept whatever the picture scored, including nothing at all. Holding it to the visual bar as
    well meant the only product a good title ever rescued was one whose image had never downloaded.

    `priority_keywords` are the words the user highlighted (Ctrl+B) in the query title -- a result
    whose title contains one is pinned ahead of every non-matching result, before the Top N cutoff
    below, so a highlighted match can never be truncated away in favor of an unhighlighted one.
    """
    priority_keywords = [str(k).strip().lower() for k in (priority_keywords or []) if str(k).strip()]
    results_data = []
    if text_matches:
        print(f"Evaluating {len(text_matches)} candidate products (matched by title or by picture). Attaching visual similarity scores...")
        for match in text_matches:
            row = match["row"]
            idx = match["idx"]
            semantic_sim = match["semantic_sim"]
            sku = str(row.get('SKU', '')).strip()
            sku_lookup = sku.upper()
            
            # Look up score from rclip visual search
            score = visual_scores.get(sku_lookup, None)
            visual_ok = score is not None and score >= min_score
            # Kept on the strength of the title even though the image disagrees, or is missing
            # entirely. This is what makes the "very high text match" tier below reachable: before,
            # those rows were dropped here, and the tier could never fire.
            text_ok = semantic_sim is not None and semantic_sim >= strong_text
            if not (visual_ok or text_ok):
                continue
            
            price = row.get('Price', '')
            source_file = row.get('Source File', 'Unknown')
            
            psku = row.get('psku', '')
            if pd.isna(psku):
                psku = ''
            else:
                psku = str(psku).strip()
            
            results_data.append({
                "Source File": str(source_file),
                "Row": int(idx + 1),
                "SKU": str(sku),
                "psku": psku,
                "Title": str(row.get('Title', '')),
                "Price": float(price) if not pd.isna(price) else None,
                "AI Score": score,
                "Text Similarity": semantic_sim,
                # Said plainly so a title match is never read as a picture match. "title" here means
                # the image disagreed or there was no image to compare.
                "Matched On": "image" if visual_ok else "title",
                "Image Filename": f"{sku}.jpg"
            })
            
        # Calculate max values to normalize both scores to [0, 1]
        # Defaulted rather than taken straight from max(), because every surviving row can now be a
        # title match with no image score at all, and max() of nothing raises.
        scored = [x["AI Score"] for x in results_data if x["AI Score"] is not None]
        max_visual = max(scored) if scored else 1.0
        if max_visual <= 0:
            max_visual = 1.0

        texts = [x["Text Similarity"] for x in results_data if x["Text Similarity"] is not None]
        max_text = max(texts) if texts else 1.0
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
                tier = (3, vis)
            elif text_sim >= 0.9:
                tier = (2, text_sim)
            else:
                tier = (1, combined)
            title_lower = item["Title"].lower()
            is_priority = any(kw in title_lower for kw in priority_keywords)
            item["Sort Key"] = (int(is_priority),) + tier
 
        # Sort results descending by Sort Key tuple
        results_data.sort(key=lambda x: x["Sort Key"], reverse=True)
        
        # Limit the results saved to the user's requested top_limit -- said out loud, because a
        # cap that trims silently reads identically to a run that only ever found this many.
        total_kept = len(results_data)
        if total_kept > top_limit:
            print(f"{total_kept} products passed the thresholds; showing the top {top_limit} "
                  f"(raise the Top N setting to see the rest).")
        results_data = results_data[:top_limit]
        
        # Assign rank based on final sorted order
        for rank_idx, item in enumerate(results_data, 1):
            item["Rank"] = rank_idx

    # Print top results
    print("\nAI Search Results (Text Search First, Then Visual Rank):")
    print("-" * 80)
    for item in results_data[:top_limit]:
        ai_score_str = f"{item['AI Score']:.3f}" if item['AI Score'] is not None else "None"
        text_sim_str = f"{item['Text Similarity']:.3f}" if item['Text Similarity'] is not None else "None"
        print(f"Rank: {item['Rank']} | Source: {item['Source File']} | Row: {item['Row']} | SKU: {item['SKU']} | Price: {item['Price']} | AI Score: {ai_score_str} | Text Sim: {text_sim_str}")
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
            parser.add_argument("--min-strong-text", type=float, default=0.85, help="Text similarity at which a product is kept despite a poor or missing image (default: 0.85, set to 1.1 to disable)")
            parser.add_argument("--strict", action="store_true", help="Enforce strict alphanumeric model code matching")
            parser.add_argument("--query-title", default="", help="Pasted title text to use as reference baseline for semantic text similarity")
            parser.add_argument("--image-dir", default="downloaded_images", help="Directory where database images are stored")
            parser.add_argument("--workers", type=int, default=10, help="Number of download workers")
            parser.add_argument("--no-indexing", action="store_true", help="Skip checking/indexing images in the target directory")
            parser.add_argument("--min-price", type=float, default=None, help="Minimum product price threshold")
            parser.add_argument("--max-price", type=float, default=None, help="Maximum product price threshold")
            parser.add_argument("--priority-keywords", default="", help="Comma-separated keywords (highlighted in the query title) that pin a match to the top, ahead of the Top N cutoff")
            args = parser.parse_args()

        # The GUI builds a Namespace directly and may pass a list; the CLI parser always hands
        # back a comma-separated string. Normalize both into the list save_and_display_results wants.
        raw_priority_keywords = getattr(args, "priority_keywords", "") or ""
        if isinstance(raw_priority_keywords, str):
            priority_keywords = [k.strip() for k in raw_priority_keywords.split(",") if k.strip()]
        else:
            priority_keywords = [str(k).strip() for k in raw_priority_keywords if str(k).strip()]

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

        # Check if input path(s) exist, or try falling back to input_data folder.
        # args.input may be a single path or multiple ';'-separated paths (multi-file selection).
        raw_input_paths = [p.strip() for p in str(args.input).split(";") if p.strip()]
        if not raw_input_paths:
            print("Error: No input dataset path provided.")
            return

        resolved_input_paths = []
        for p in raw_input_paths:
            if os.path.exists(p):
                resolved_input_paths.append(p)
            else:
                fallback_path = os.path.join("input_data", p)
                if os.path.exists(fallback_path):
                    resolved_input_paths.append(fallback_path)
                else:
                    print(f"Error: Input dataset path '{p}' not found.")
                    return
        input_path = resolved_input_paths

        args.image_dir = setup_global_image_dir(args.image_dir)

        # 1. Load spreadsheet database
        try:
            df = load_dataset(input_path)
        except Exception as e:
            print(f"Error loading dataset: {e}")
            return
        print(f"Loaded {len(df)} products from the database.")

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

        # 4. Download every product's image, not just the keyword-overlapping ones. A picture
        # match is the only way back for a product whose title shares no word with the reference
        # title (see find_visual_only_matches below) -- pre-filtering the download queue by keyword
        # would make that recovery only work when the image happened to already be cached from an
        # earlier, differently-worded search. download_missing_images skips whatever is already on
        # disk, so a repeat run against the same store only ever pays for what actually changed.
        print(f"Downloading images for all {len(df)} products in the database...")

        check_stop()

        # 5. Automatically download missing images
        download_missing_images(df, image_dir=args.image_dir, max_workers=args.workers)

        check_stop()

        # 6. Run visual similarity search (in-process rclip)
        visual_scores = run_visual_search(args.image_dir, args.query, no_indexing=args.no_indexing)

        check_stop()

        # 7. Fallback to Rank 1 match if reference title wasn't found earlier
        if not reference_title and visual_scores:
            reference_title = resolve_reference_title(df, args.query, args.query_title, visual_scores)

        check_stop()

        # 8. Run semantic text search
        strong_text = getattr(args, "min_strong_text", 0.85)
        text_matches = []
        if reference_title:
            text_matches = run_semantic_text_search(df, reference_title, visual_scores,
                                                    args.min_text_sim, args.strict,
                                                    min_strong_text=strong_text)

        check_stop()

        # 9. Format, sort, save and print results
        max_score = max(visual_scores.values()) if visual_scores else 0.0
        dynamic_min_score = max(args.min_score, max_score - 0.45)

        # Recover products whose picture matches well enough on its own, however their title reads
        # -- the other half of "never miss a product": step 8 never even considers a title with no
        # keyword overlap with the reference title, so this is the only way one of those comes
        # back. Uses the same visual bar as everything else in the report, so "matched by image"
        # means the same thing everywhere it appears.
        covered_skus = {str(m["row"].get("SKU", "")).strip().upper() for m in text_matches}
        visual_only_matches = find_visual_only_matches(df, visual_scores, covered_skus, dynamic_min_score)
        if visual_only_matches:
            print(f"Found {len(visual_only_matches)} more products by picture alone -- "
                  f"their titles never shared a keyword with '{reference_title}'.")

        print(f"Top visual score: {max_score:.3f} | Dynamic visual threshold: {dynamic_min_score:.3f} "
              f"| Strong-title threshold: {strong_text:.2f}")
        save_and_display_results(text_matches + visual_only_matches, visual_scores, args.output, args.top,
                                 dynamic_min_score, strong_text=strong_text, priority_keywords=priority_keywords)

    finally:
        # Always clear the per-thread stop event when done
        _stop_event_local.event = None

if __name__ == "__main__":
    main()
