import json
import os
import pandas as pd

def normalize_dataframe(df):
    """Normalize column names from different Excel formats (old scrape format vs new report format)."""
    mapping = {
        'Input_SKU': 'SKU',
        'Best_ZSKU': 'SKU',
        'Standard_ZSKU': 'SKU',
        
        'Best_Title': 'Title',
        'Standard_Title': 'Title',
        
        'Best_Price': 'Price',
        'Standard_Price': 'Price',
        
        'Best_Main_Image_URL': 'Image URL',
        'Standard_Main_Image_URL': 'Image URL'
    }
    
    rename_dict = {}
    assigned_targets = set(df.columns)
    
    # Process column mappings in order of preference to avoid duplicates
    preferred_order = [
        'Input_SKU', 'Best_ZSKU', 'Standard_ZSKU',
        'Best_Title', 'Standard_Title',
        'Best_Price', 'Standard_Price',
        'Best_Main_Image_URL', 'Standard_Main_Image_URL'
    ]
    
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
            df = pd.read_excel(xls, sheet_name=sheet_name)
        else:
            df = pd.read_excel(xls)
        return normalize_dataframe(df)
    except Exception as e:
        print(f"Error reading Excel file '{file_path}': {e}")
        # Fallback to direct reading
        df = pd.read_excel(file_path)
        return normalize_dataframe(df)

def generate_html_report(json_path="search_results_ai.json", output_html="search_results.html", images_dir="downloaded_images", excel_path="input_data/somow_26971_sku_matched_noon_data.xlsx"):
    if not os.path.exists(json_path):
        print(f"Error: JSON file '{json_path}' not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    # Let's read attributes from the excel file to get any extra attributes for these SKUs
    # mapping SKU -> extra columns
    extra_attrs = {}
    try:
        df = load_excel_with_sheets(excel_path)
        # Identify columns other than basic ones
        standard_cols = {'SKU', 'Title', 'Price', 'Image URL', 'Source File'}
        extra_cols = [col for col in df.columns if col not in standard_cols]
        for _, row in df.iterrows():
            sku = str(row.get('SKU', ''))
            if sku:
                extra_attrs[sku] = {col: row[col] for col in extra_cols if not pd.isna(row[col])}
    except Exception as e:
        print(f"Warning: Could not extract extra attributes from Excel: {e}")

    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Search & Duplicate Listing Matches</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0f1d;
            --bg-secondary: #121829;
            --bg-card: #1b233d;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-primary: #6366f1;
            --accent-secondary: #3b82f6;
            --accent-gradient: linear-gradient(135deg, #6366f1, #3b82f6);
            --border-color: rgba(255, 255, 255, 0.08);
            --badge-green: #10b981;
            --badge-orange: #f59e0b;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            min-height: 100vh;
            padding: 2rem 1.5rem;
            line-height: 1.5;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            text-align: center;
            margin-bottom: 3rem;
            position: relative;
        }

        header h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 2.8rem;
            font-weight: 800;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
            letter-spacing: -0.025em;
        }

        header p {
            color: var(--text-secondary);
            font-size: 1.1rem;
            font-weight: 300;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }

        .stat-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            text-align: center;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            transition: transform 0.3s ease;
        }

        .stat-card:hover {
            transform: translateY(-2px);
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 4px;
            background: var(--accent-gradient);
        }

        .stat-val {
            font-family: 'Outfit', sans-serif;
            font-size: 2.2rem;
            font-weight: 800;
            color: #ffffff;
            margin-bottom: 0.25rem;
        }

        .stat-lbl {
            color: var(--text-secondary);
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .results-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1.5rem;
        }

        .match-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            overflow: hidden;
            display: flex;
            flex-direction: row;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
        }

        .match-card:hover {
            transform: translateY(-4px) scale(1.005);
            border-color: rgba(99, 102, 241, 0.4);
            box-shadow: 0 15px 35px rgba(99, 102, 241, 0.15);
        }

        .image-container {
            width: 260px;
            min-width: 260px;
            background: #0f1423;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
            position: relative;
            border-right: 1px solid var(--border-color);
        }

        .image-container img {
            max-width: 100%;
            max-height: 200px;
            object-fit: contain;
            border-radius: 12px;
            transition: transform 0.5s ease;
        }

        .match-card:hover .image-container img {
            transform: scale(1.05);
        }

        .rank-badge {
            position: absolute;
            top: 1rem;
            left: 1rem;
            background: var(--accent-gradient);
            color: white;
            padding: 0.4rem 1rem;
            border-radius: 30px;
            font-weight: 700;
            font-family: 'Outfit', sans-serif;
            font-size: 0.9rem;
            box-shadow: 0 4px 10px rgba(99, 102, 241, 0.4);
            z-index: 2;
        }

        .details-container {
            flex-grow: 1;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .details-header {
            margin-bottom: 1rem;
        }

        .details-header h2 {
            font-size: 1.35rem;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 0.75rem;
            line-height: 1.4;
        }

        .meta-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            margin-bottom: 1.5rem;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 0.35rem 0.85rem;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: var(--text-primary);
        }

        .badge-score {
            background: rgba(99, 102, 241, 0.15);
            border-color: rgba(99, 102, 241, 0.3);
            color: #818cf8;
        }

        .badge-text {
            background: rgba(59, 130, 246, 0.15);
            border-color: rgba(59, 130, 246, 0.3);
            color: #60a5fa;
        }

        .badge-price {
            background: rgba(16, 185, 129, 0.15);
            border-color: rgba(16, 185, 129, 0.3);
            color: #34d399;
        }

        .badge-source {
            background: rgba(245, 158, 11, 0.1);
            border-color: rgba(245, 158, 11, 0.2);
            color: #fbbf24;
        }

        .attributes-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1rem;
            background: rgba(255, 255, 255, 0.02);
            padding: 1.25rem;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.04);
            margin-top: auto;
        }

        .attr-item {
            font-size: 0.85rem;
        }

        .attr-label {
            color: var(--text-secondary);
            font-weight: 500;
            display: block;
            margin-bottom: 0.15rem;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }

        .attr-value {
            color: #ffffff;
            font-weight: 600;
        }

        @media (max-width: 768px) {
            .match-card {
                flex-direction: column;
            }

            .image-container {
                width: 100%;
                border-right: none;
                border-bottom: 1px solid var(--border-color);
            }

            .details-container {
                padding: 1.5rem;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Product Matching & Similar Listings</h1>
            <p>Interactive duplicate finder results ranked by visual and semantic relevance</p>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-val">{total_matches}</div>
                <div class="stat-lbl">Matches Found</div>
            </div>
            <div class="stat-card">
                <div class="stat-val">{best_visual}</div>
                <div class="stat-lbl">Best Visual Score</div>
            </div>
            <div class="stat-card">
                <div class="stat-val">{best_text}</div>
                <div class="stat-lbl">Best Text Score</div>
            </div>
        </div>

        <div class="results-grid">
"""

    best_visual = 0.0
    best_text = 0.0

    for idx, item in enumerate(results):
        sku = item.get("SKU", "")
        title = item.get("Title", "")
        price = item.get("Price", "")
        ai_score = item.get("AI Score", None)
        text_sim = item.get("Text Similarity", 0.0)
        source_file = item.get("Source File", "")
        row_num = item.get("Row", "")
        rank = item.get("Rank", idx + 1)

        if ai_score is not None and ai_score > best_visual:
            best_visual = ai_score
        if text_sim > best_text:
            best_text = text_sim

        # Find local image path or fallback
        image_path = os.path.join(images_dir, f"{sku}.jpg")
        if not os.path.exists(image_path):
            image_path = os.path.join(images_dir, f"{sku}.png")
        if not os.path.exists(image_path):
            image_path = "https://placehold.co/300x300/121829/ffffff?text=Image+Not+Found"

        ai_score_str = f"{ai_score:.3f}" if ai_score is not None else "N/A"
        price_str = f"AED {price:,.2f}" if price else "N/A"

        # Build extra attributes list
        attrs_html = ""
        attrs_html += f"""
            <div class="attr-item">
                <span class="attr-label">SKU / ID</span>
                <span class="attr-value">{sku}</span>
            </div>
            <div class="attr-item">
                <span class="attr-label">Excel Location</span>
                <span class="attr-value">Row {row_num}</span>
            </div>
        """
        
        # Add extra custom attributes from combined listing database if available
        if sku in extra_attrs:
            for col, val in extra_attrs[sku].items():
                attrs_html += f"""
                    <div class="attr-item">
                        <span class="attr-label">{col}</span>
                        <span class="attr-value">{val}</span>
                    </div>
                """

        html_content += f"""
            <div class="match-card">
                <div class="image-container">
                    <span class="rank-badge">Rank {rank}</span>
                    <img src="{image_path}" alt="{title}" onerror="this.src='https://placehold.co/300x300/121829/ffffff?text=Image+Not+Found'">
                </div>
                <div class="details-container">
                    <div class="details-header">
                        <h2>{title}</h2>
                        <div class="meta-badges">
                            <span class="badge badge-score">Visual Match: {ai_score_str}</span>
                            <span class="badge badge-text">Text Sim: {text_sim:.3f}</span>
                            <span class="badge badge-price">{price_str}</span>
                            <span class="badge badge-source">{source_file}</span>
                        </div>
                    </div>
                    <div class="attributes-grid">
                        {attrs_html}
                    </div>
                </div>
            </div>
        """

    html_content += """
        </div>
    </div>
</body>
</html>
"""

    # Format the header values
    html_content = html_content.replace("{total_matches}", str(len(results)))
    html_content = html_content.replace("{best_visual}", f"{best_visual:.3f}" if best_visual else "N/A")
    html_content = html_content.replace("{best_text}", f"{best_text:.3f}" if best_text else "N/A")

    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Successfully generated HTML report at: {output_html}")

if __name__ == "__main__":
    generate_html_report()
