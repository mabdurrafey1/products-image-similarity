import pandas as pd
import json
import glob
import os
import re

skus_to_check = [
    "N47558655A",
    "Z36CB9EEB4250F657B1D9Z",
    "ZA43531763D73D07861A8Z",
    "ZB47ACBBE45197B22AB07Z",
    "ZF44C630DD15ADC53CD44Z",
    "Z8594756219F9B805BC39Z",
    "ZC48B77B312EB10E25E8DZ",
    "Z181E54754694E1405874Z",
    "Z521CF2C6065CE2947A6BZ",
    "Z82DDA282C98D5193C5FDZ",
    "Z1AA4CB9DB0D07DF52F24Z",
    "ZA931542454486C153520Z"
]

print("--- Checking in search_results_ai.json ---")
if os.path.exists("search_results_ai.json"):
    with open("search_results_ai.json", "r") as f:
        try:
            results = json.load(f)
            found_in_results = []
            for item in results:
                sku = item.get("SKU", "").strip()
                if any(x.upper() == sku.upper() for x in skus_to_check):
                    found_in_results.append(item)
            for item in found_in_results:
                print(f"SKU: {item['SKU']} | Rank: {item.get('Rank')} | VS: {item.get('AI Score')} | TX: {item.get('Text Similarity')} | SortKey: {item.get('Sort Key')}")
        except Exception as e:
            print("Error loading JSON:", e)
else:
    print("search_results_ai.json does not exist")

print("\n--- Checking in excel sheet ---")
excel_files = sorted(glob.glob("input_data/*.xlsx"))
excel_path = excel_files[0] if excel_files else "combined_listings.xlsx"
if os.path.exists(excel_path):
    try:
        xls = pd.ExcelFile(excel_path)
        print("Sheets in Excel:", xls.sheet_names)
        sheet_name = 'Best_One_Row_Per_SKU' if 'Best_One_Row_Per_SKU' in xls.sheet_names else xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=sheet_name)
        print("Columns in Excel:", list(df.columns))
        
        # normalize col names
        sku_col = None
        for col in ['Input_SKU', 'Best_ZSKU', 'Standard_ZSKU']:
            if col in df.columns:
                sku_col = col
                break
        if not sku_col:
            sku_col = df.columns[0]
            
        print(f"Using column '{sku_col}' as SKU reference")
        
        # Import the model mismatch function from match_image_ai
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from match_image_ai import is_generic_mismatch, clean_title, extract_models
        
        df['SKU_clean'] = df[sku_col].astype(str).str.strip().str.upper()
        query_title = "Double Folding Mobile and Tablet Holder – Adjustable Stand for Phones & Tablets, Portable Design, Anti-Slip Base, Space-Saving Holder for Desk, Bed, and Travel – Perfect for Viewing, Gaming & Video Calls (Black)"
        
        for s in skus_to_check:
            match = df[df['SKU_clean'] == s.upper()]
            if not match.empty:
                title_val = str(match.iloc[0].get('Best_Title', match.iloc[0].get('Title', '')))
                price_val = match.iloc[0].get('Best_Price', match.iloc[0].get('Price', ''))
                # Let's run a detailed mismatch check
                t_a = clean_title(query_title)
                t_b = clean_title(title_val)
                
                models_a = extract_models(t_a)
                models_b = extract_models(t_b)
                model_mismatch = bool(models_a and models_b and not models_a.intersection(models_b))
                
                numbers_a = {int(num) for num in re.findall(r'\b\d+\b', t_a)}
                numbers_b = {int(num) for num in re.findall(r'\b\d+\b', t_b)}
                spec_numbers = {
                    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 20, 22, 23, 24, 25, 26, 30, 32, 36, 40, 45, 50, 60, 64, 80, 128, 152, 203, 256, 268, 
                    500, 512, 520, 666, 1000, 3500, 4000, 6000, 10000, 15000, 18000, 20000, 30000, 40000
                }
                diff_numbers = {num for num in numbers_a.symmetric_difference(numbers_b) if num not in spec_numbers}
                number_mismatch = bool(diff_numbers)
                
                modifiers = {
                    'pro', 'max', 'plus', 'ultra', 'mini', 'lite', 'se', 'air', 'series', 
                    'generation', 'gen', 'active', 'sport'
                }
                words_a = set(t_a.split())
                words_b = set(t_b.split())
                mod_diffs = [mod for mod in modifiers if (mod in words_a) != (mod in words_b)]
                mod_mismatch = bool(mod_diffs)
                
                mismatch = is_generic_mismatch(query_title, title_val)
                print(f"SKU {s} | Mismatch? {mismatch} | ModelMismatch={model_mismatch} (A={models_a}, B={models_b}) | NumberMismatch={number_mismatch} ({diff_numbers}) | ModMismatch={mod_mismatch} ({mod_diffs})")
            else:
                print(f"SKU {s} NOT found in database")
    except Exception as e:
        print("Error reading excel:", e)
