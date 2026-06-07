import os
import glob
import pandas as pd

def main():
    input_dir = "input_data"
    output_file = "combined_listings.xlsx"
    
    excel_files = glob.glob(os.path.join(input_dir, "*.xlsx"))
    if not excel_files:
        print(f"Error: No Excel files found in '{input_dir}' directory.")
        return
        
    print(f"Found {len(excel_files)} Excel files in '{input_dir}'. Combining them...")
    
    dfs = []
    for f in sorted(excel_files):
        try:
            print(f"Reading {os.path.basename(f)}...")
            df = pd.read_excel(f)
            # Add Source File column just like in the application
            df['Source File'] = os.path.basename(f)
            dfs.append(df)
        except Exception as e:
            print(f"Error reading {f}: {e}")
            
    if not dfs:
        print("Error: Could not load any dataframes.")
        return
        
    combined_df = pd.concat(dfs, ignore_index=True)
    print(f"\nCombined dataframe shape: {combined_df.shape}")
    print(f"Total rows: {len(combined_df)}")
    
    if 'SKU' in combined_df.columns:
        unique_skus = combined_df['SKU'].nunique()
        print(f"Unique SKUs: {unique_skus}")
        # Let's drop duplicates by SKU to make search faster and cleaner if there are duplicate listings across sheets,
        # but let's first check if we want to save all or drop duplicates. Let's save all rows, but we can print duplicates.
        duplicate_skus = len(combined_df) - unique_skus
        print(f"Duplicate SKUs found: {duplicate_skus}")
        
    print(f"Saving combined listings to '{output_file}'...")
    combined_df.to_excel(output_file, index=False)
    print("Combined Excel successfully created!")

if __name__ == "__main__":
    main()
