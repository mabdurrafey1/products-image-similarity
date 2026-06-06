import pandas as pd

def main():
    df = pd.read_excel("www_noon_com_LOCKED_filtered_scrape_20260518_155920.xlsx")
    # Search for iPhone 7, 8, SE, 7 Plus, 8 Plus
    matches = df[df['Title'].str.contains("iPhone 7|iPhone 8|iPhone SE", case=False, na=False)]
    for idx, row in matches.iterrows():
        print(f"Row {idx+1}: SKU={row['SKU']}, Title={row['Title'][:80]}, Image={row['Image URL']}")

if __name__ == "__main__":
    main()
