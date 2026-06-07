import datetime
import sys

# Check if the date is after June 9, 2026
if datetime.date.today() > datetime.date(2026, 6, 9):
    sys.exit("This version of the program has expired. It is not available after June 9, 2026.")

from downloader import download_missing_images
import os
import argparse
import json
import re
import pandas as pd
from rclip.model import Model as RClipModel


def clean_title(title):
    """Clean and normalize product titles for keyword overlap comparison."""
    if not title or not isinstance(title, str):
        return ""
    t = title.lower()
    t = re.sub(r'[^a-z0-9\s-]', ' ', t)
    t = t.replace("rear view", "rearview")
    t = t.replace("rearviewmirror", "rearview mirror")
    return t

def is_generic_mismatch(title_a, title_b):
    """Check if there is a generic model or category mismatch between two product titles."""
    t_a = clean_title(title_a)
    t_b = clean_title(title_b)
    
    # 1. Alphanumeric model identifier mismatch (e.g., X6 vs D007 vs R36S vs M21)
    def extract_models(text):
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


def main():
    parser = argparse.ArgumentParser(description="AI-powered duplicate listing search using rclip (CLIP).")
    parser.add_argument("--query", required=True, help="Path to local query image")
    parser.add_argument("--input", default="input_data", help="Dataset Excel path or directory containing Excel files (default: input_data)")
    parser.add_argument("--output", default="search_results_ai.json", help="Path to save search results JSON")
    parser.add_argument("--top", type=int, default=10, help="Number of top visual matches to retrieve (default: 10)")
    parser.add_argument("--min-score", type=float, default=0.20, help="Minimum AI similarity score threshold (default: 0.20)")
    parser.add_argument("--min-text-sim", type=float, default=0.0, help="Minimum semantic text similarity score (default: 0.70, set to 0.0 to disable)")
    parser.add_argument("--strict", action="store_true", help="Enforce strict alphanumeric model code matching")
    parser.add_argument("--query-title", default="", help="Pasted title text to use as reference baseline for semantic text similarity")
    parser.add_argument("--image-dir", default="downloaded_images", help="Directory where database images are stored")
    parser.add_argument("--workers", type=int, default=30, help="Number of download workers")
    parser.add_argument("--no-indexing", action="store_true", help="Skip checking/indexing images in the target directory")
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

    if not os.path.exists(args.image_dir):
        os.makedirs(args.image_dir, exist_ok=True)

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

    # Automatically download missing images from dataset
    download_missing_images(df, image_dir=args.image_dir, max_workers=args.workers)

    # Retrieve baseline title for similarity checks
    reference_title = None
    if args.query_title:
        reference_title = args.query_title
        print(f"Baseline model reference specified by user: '{reference_title}'\n")
    else:
        # Try resolving reference title from query filename first if it corresponds to a dataset SKU
        query_basename = os.path.splitext(os.path.basename(args.query))[0]
        query_matching_rows = df[df['SKU'].astype(str) == query_basename]
        if not query_matching_rows.empty:
            reference_title = str(query_matching_rows.iloc[0].get('Title', ''))
            print(f"Baseline model reference determined from query filename: '{reference_title}'\n")

    # Run rclip visual search in-process to get visual similarity scores
    from rclip.main import init_rclip
    abs_query_path = os.path.abspath(args.query)
    visual_scores = {}
    print("Querying AI model for visual similarity scores (in-process)...")
    try:
        rclip_instance, rclip_model, rclip_db = init_rclip(
            working_directory=os.path.abspath(args.image_dir),
            indexing_batch_size=32,
            no_indexing=args.no_indexing
        )
        try:
            search_results = rclip_instance.search(
                query=abs_query_path,
                directory=os.path.abspath(args.image_dir),
                top_k=500
            )
            for item in search_results:
                filename = os.path.basename(item.filepath)
                sku = os.path.splitext(filename)[0]
                visual_scores[sku] = item.score
        finally:
            rclip_model.close()
            rclip_db.close()
        print(f"Successfully loaded {len(visual_scores)} visual similarity scores.")
    except Exception as e:
        print(f"Warning: Could not run rclip visual search: {e}")

    # Fallback to the highest visual match title if not resolved yet
    if not reference_title and visual_scores:
        sorted_visual = sorted(visual_scores.items(), key=lambda x: x[1], reverse=True)
        for sku, score in sorted_visual:
            matching_rows = df[df['SKU'].astype(str) == sku]
            if not matching_rows.empty:
                reference_title = str(matching_rows.iloc[0].get('Title', ''))
                print(f"Baseline model reference determined from Rank 1 visual match: '{reference_title}'\n")
                break

    # Initialize CLIP model for semantic text similarity comparison
    clip_model = None
    ref_emb = None
    if reference_title:
        print("Initializing CLIP text encoder for semantic text similarity...")
        try:
            clip_model = RClipModel()
            clip_model.ensure_downloaded()
            ref_emb = clip_model.compute_text_features([reference_title])[0]
            print("CLIP text encoder successfully initialized.\n")
        except Exception as e:
            print(f"Warning: Could not initialize CLIP text model: {e}")

    # First find SKUs based on text similarity
    text_matches = []
    if reference_title and clip_model is not None and ref_emb is not None:
        print("Performing text similarity search across all products in Excel...")
        
        # Step 1: Pre-filter by quick keyword overlap and check if visual score is available to reduce candidates
        candidates = []
        for idx, row in df.iterrows():
            sku = str(row.get('SKU', ''))
            if sku not in visual_scores:
                continue
            title = str(row.get('Title', ''))
            if not title:
                continue
            
            # Simple keyword overlap pre-filter
            if get_title_similarity(reference_title, title) > 0.0:
                candidates.append((idx, row, title))
        
        print(f"Found {len(candidates)} candidate products with keyword overlap and visual score. Computing semantic similarity...")
        
        # Step 2: Batch compute text embeddings for candidates
        threshold = args.min_text_sim if args.min_text_sim > 0.0 else 0.70
        batch_size = 128
        for i in range(0, len(candidates), batch_size):
            batch_candidates = candidates[i:i+batch_size]
            batch_titles = [item[2] for item in batch_candidates]
            
            try:
                batch_embs = clip_model.compute_text_features(batch_titles)
                for j, emb in enumerate(batch_embs):
                    semantic_sim = float(ref_emb @ emb.T)
                    
                    if semantic_sim >= threshold:
                        idx, row, title = batch_candidates[j]
                        
                        # Apply strict model check if enabled
                        if args.strict and is_generic_mismatch(reference_title, title):
                            continue
                            
                        text_matches.append({
                            "row": row,
                            "idx": idx,
                            "semantic_sim": semantic_sim
                        })
            except Exception as e:
                print(f"Warning: Error processing batch: {e}")

    # Second, assign visual scores and build merged results
    results_data = []
    if text_matches:
        print(f"Found {len(text_matches)} products matching text criteria. Attaching visual similarity scores...")
        for match in text_matches:
            row = match["row"]
            idx = match["idx"]
            semantic_sim = match["semantic_sim"]
            sku = str(row.get('SKU', ''))
            
            # Look up score from rclip visual search
            score = visual_scores.get(sku, None)
            if score is None:
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
            
        # Sort results:
        # 1. Products with visual scores (AI Score is not None) sorted descending by AI Score.
        # 2. Products without visual scores (AI Score is None) sorted descending by Text Similarity.
        results_data.sort(
            key=lambda x: (
                0 if x["AI Score"] is None else 1, 
                x["AI Score"] if x["AI Score"] is not None else 0.0, 
                x["Text Similarity"]
            ),
            reverse=True
        )
        
        # Assign rank based on final sorted order
        for rank_idx, item in enumerate(results_data, 1):
            item["Rank"] = rank_idx

    # Print top results
    print("\nAI Search Results (Text Search First, Then Visual Rank):")
    print("-" * 80)
    for item in results_data[:args.top]:
        ai_score_str = f"{item['AI Score']:.3f}" if item['AI Score'] is not None else "None"
        print(f"Rank: {item['Rank']} | Source: {item['Source File']} | Row: {item['Row']} | SKU: {item['SKU']} | Price: {item['Price']} | AI Score: {ai_score_str} | Text Sim: {item['Text Similarity']:.3f}")
        print(f"Title: {item['Title']}")
        print(f"Image: {item['Source File']} (SKU: {item['SKU']})")
        print("-" * 80)

    if not results_data:
        print("No duplicate listings matching the criteria were found.")

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=4, ensure_ascii=False)
    print(f"Saved AI search results to {args.output}")

if __name__ == "__main__":
    main()
