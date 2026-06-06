import os
import pandas as pd

def main():
    excel_path = "www_noon_com_LOCKED_filtered_scrape_20260518_155920.xlsx"
    if os.path.exists(excel_path):
        df = pd.read_excel(excel_path)
        print("Data preview:")
        filtered = df[df['Title'].astype(str).str.contains('Massager', case=False, na=False)]
        for idx, row in filtered.iterrows():
            print(f"Row {idx+1}: SKU={row.get('SKU')}, Title={row.get('Title')}, Image URL={row.get('Image URL')}")
    else:
        print("File not found.")

if __name__ == "__main__":
    main()
