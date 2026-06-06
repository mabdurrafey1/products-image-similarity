import os
import glob
import pandas as pd

def main():
    input_dir = "input_data"
    output_file = "combined_listings.xlsx"
    
    if not os.path.exists(input_dir):
        print(f"Error: Directory '{input_dir}' not found.")
        return
        
    excel_files = glob.glob(os.path.join(input_dir, "*.xlsx"))
    excel_files = [f for f in excel_files if not os.path.basename(f).startswith("~$")] # Ignore temp files
    
    if not excel_files:
        print(f"No Excel files found in '{input_dir}'.")
        return
        
    print(f"Found {len(excel_files)} Excel files to combine:")
    for f in sorted(excel_files):
        print(f" - {os.path.basename(f)}")
        
    dfs = []
    for f in sorted(excel_files):
        try:
            print(f"Reading {os.path.basename(f)}...")
            df = pd.read_excel(f)
            df["Source File"] = os.path.basename(f)
            dfs.append(df)
        except Exception as e:
            print(f"Error reading {f}: {e}")
            
    if not dfs:
        print("No data was successfully loaded.")
        return
        
    print("Combining datasets...")
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Save combined excel
    print(f"Saving combined dataset to '{output_file}' ({len(combined_df)} rows)...")
    combined_df.to_excel(output_file, index=False)
    print("Combined Excel sheet successfully created!")

if __name__ == "__main__":
    main()
