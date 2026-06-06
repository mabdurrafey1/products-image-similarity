import pandas as pd

def main():
    df = pd.read_excel("www_noon_com_LOCKED_filtered_scrape_20260518_155920.xlsx")
    matches = df[df['Title'].str.contains("Mirror Phone|Rearview Mirror|Rear View Mirror", case=False, na=False)]
    for idx, row in matches.iterrows():
        print(f"Row {idx+1}: SKU={row['SKU']}, Title={row['Title'][:80]}, Image={row['Image URL']}")

if __name__ == "__main__":
    main()
