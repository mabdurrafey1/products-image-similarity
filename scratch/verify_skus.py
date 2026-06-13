import pandas as pd
import json
import glob
import os

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
        df['SKU_clean'] = df[sku_col].astype(str).str.strip().str.upper()
        for s in skus_to_check:
            match = df[df['SKU_clean'] == s.upper()]
            if not match.empty:
                title_val = match.iloc[0].get('Best_Title', match.iloc[0].get('Title', ''))
                price_val = match.iloc[0].get('Best_Price', match.iloc[0].get('Price', ''))
                print(f"Found {s} in database: Title={str(title_val)[:60]}... Price={price_val}")
            else:
                print(f"SKU {s} NOT found in database")
    except Exception as e:
        print("Error reading excel:", e)
