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

def generate_html_report(json_path="search_results_ai.json", output_html="search_results.html", images_dir="downloaded_images", excel_path=None):
    if excel_path is None:
        import glob
        excel_files = sorted(glob.glob("input_data/*.xlsx"))
        excel_path = excel_files[0] if excel_files else "combined_listings.xlsx"

    if not os.path.exists(json_path):
        print(f"Error: JSON file '{json_path}' not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    # Sort results by price (descending, N/A prices at the end)
    results.sort(key=lambda x: (
        x.get('Price') is None or pd.isna(x.get('Price')),
        -float(x.get('Price')) if x.get('Price') is not None and not pd.isna(x.get('Price')) else 0.0
    ))

    prices = []
    for item in results:
        p = item.get('Price')
        if p is not None:
            try:
                prices.append(float(p))
            except (ValueError, TypeError):
                pass
    min_db_price = int(min(prices)) if prices else 0
    max_db_price = int(max(prices)) if prices else 999

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
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #f3f4f6;
            --bg-card: #ffffff;
            --text-primary: #111827;
            --text-secondary: #4b5563;
            --border-color: #e5e7eb;
            
            --color-blue: #2563eb;
            --color-green: #10b981;
            --color-grey-dark: #374151;
            --color-grey-light: #f3f4f6;
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
            margin: 0;
            padding: 0;
            line-height: 1.5;
        }

        .app-layout {
            display: flex;
            min-height: 100vh;
        }

        .sidebar {
            width: 280px;
            background: #ffffff;
            border-right: 1px solid var(--border-color);
            padding: 1.5rem 1rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            flex-shrink: 0;
            position: sticky;
            top: 0;
            height: 100vh;
            overflow-y: auto;
            box-shadow: 2px 0 8px rgba(0, 0, 0, 0.02);
        }

        .sidebar-section {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .sidebar-label {
            font-size: 0.75rem;
            font-weight: 700;
            color: #374151;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }

        .sidebar-select {
            width: 100%;
            padding: 0.6rem 0.8rem;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.85rem;
            background: #ffffff;
            color: var(--text-primary);
            cursor: pointer;
            outline: none;
        }

        .sidebar-subtext {
            font-size: 0.75rem;
            color: #6b7280;
            line-height: 1.4;
        }

        .price-inputs {
            display: flex;
            gap: 8px;
        }

        .price-input {
            width: 50%;
            padding: 0.6rem;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-size: 0.85rem;
            font-family: inherit;
            outline: none;
        }

        .price-buttons {
            display: flex;
            gap: 8px;
            margin-top: 4px;
        }

        .sidebar-btn {
            flex: 1;
            border: none;
            border-radius: 8px;
            padding: 0.6rem;
            font-size: 0.8rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            text-align: center;
        }

        .btn-sidebar-primary {
            background: var(--color-blue);
            color: white;
        }
        .btn-sidebar-primary:hover {
            background: #1d4ed8;
        }

        .btn-sidebar-secondary {
            background: #e5e7eb;
            color: var(--text-secondary);
        }
        .btn-sidebar-secondary:hover {
            background: #d1d5db;
        }

        .main-content {
            flex-grow: 1;
            padding: 1.5rem;
            overflow-y: auto;
            max-width: calc(100vw - 280px);
        }

        .container {
            max-width: 100%;
            margin: 0 auto;
        }

        header {
            text-align: center;
            margin-bottom: 2rem;
            margin-top: 1rem;
        }

        header h1 {
            font-size: 2.2rem;
            font-weight: 800;
            color: var(--text-primary);
            margin-bottom: 0.5rem;
            letter-spacing: -0.025em;
        }

        header p {
            color: var(--text-secondary);
            font-size: 1rem;
            font-weight: 400;
        }

        /* Floating Selection Panel */
        .selection-panel {
            position: fixed;
            top: 1rem;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(255, 255, 255, 0.95);
            border: 1px solid var(--border-color);
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
            border-radius: 50px;
            padding: 0.6rem 1.5rem;
            display: flex;
            align-items: center;
            gap: 1rem;
            z-index: 1000;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
            display: none; /* Hidden by default, shown via JS */
        }

        .selection-count {
            font-weight: 700;
            color: var(--color-blue);
            font-size: 0.95rem;
        }

        .selection-btn {
            border: none;
            padding: 0.4rem 1rem;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: background 0.2s ease;
        }

        .selection-btn-copy {
            background: var(--color-blue);
            color: white;
        }
        .selection-btn-copy:hover {
            background: #1d4ed8;
        }

        .selection-btn-clear {
            background: var(--color-grey-light);
            color: var(--text-secondary);
            border: 1px solid var(--border-color);
        }
        .selection-btn-clear:hover {
            background: #e5e7eb;
        }

        /* Grid Layout */
        .results-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }

        /* Card Style */
        .match-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 10px;
            display: flex;
            flex-direction: column;
            position: relative;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            min-width: 0;
        }

        .match-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        }

        /* Top Checkbox Row */
        .card-select-row {
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }

        .card-select-row label {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--text-primary);
            cursor: pointer;
            user-select: none;
        }

        .card-select-row input[type="checkbox"] {
            width: 15px;
            height: 15px;
            accent-color: var(--color-blue);
            cursor: pointer;
        }

        /* Image Display */
        .image-container {
            width: 100%;
            height: 160px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 6px;
            background: #ffffff;
            margin-bottom: 8px;
            position: relative;
        }

        .image-container img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }

        /* Badges Directly Under Image */
        .pills-row {
            display: flex;
            gap: 6px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }

        .pill-badge {
            font-size: 0.75rem;
            font-weight: 700;
            padding: 0.25rem 0.6rem;
            border-radius: 4px;
            text-transform: uppercase;
        }

        .pill-price {
            background: var(--color-green);
            color: white;
        }

        .pill-dark {
            background: var(--color-grey-dark);
            color: white;
        }

        /* Product Title */
        .product-title {
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.4;
            margin-bottom: 8px;
            height: 3.4rem;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* Category & Metadata Pills */
        .tags-row {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 12px;
        }

        .tag-pill {
            background: var(--color-grey-light);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 0.72rem;
            font-weight: 600;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
        }

        /* SKU Table Container */
        .sku-box {
            background: var(--color-grey-light);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 6px;
            margin-bottom: 8px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .sku-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.7rem;
            font-weight: 500;
        }

        .sku-label {
            color: var(--text-secondary);
            font-weight: 700;
            width: 32px;
        }

        .sku-val {
            font-family: monospace;
            color: var(--text-primary);
            flex-grow: 1;
            margin-right: 6px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .copy-btn {
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 0.1rem 0.3rem;
            font-size: 0.65rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .copy-btn:hover {
            background: #e5e7eb;
            color: var(--text-primary);
        }

        /* Verification Row */
        .verification-row {
            display: flex;
            gap: 6px;
            margin-bottom: 12px;
        }

        .verify-badge {
            font-size: 0.7rem;
            font-weight: 700;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            background: #d1fae5;
            color: #065f46;
            display: inline-flex;
            align-items: center;
        }

        /* Thumbnail previews */
        .thumbnails-row {
            display: flex;
            gap: 6px;
            margin-bottom: 14px;
            overflow-x: auto;
            padding-bottom: 4px;
        }

        .thumb-img {
            width: 32px;
            height: 32px;
            min-width: 32px;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 2px;
            background: #ffffff;
            cursor: pointer;
            object-fit: contain;
            transition: border-color 0.15s ease;
        }

        .thumb-img:hover, .thumb-img.active {
            border-color: var(--color-blue);
        }

        /* Bottom Action Buttons */
        .actions-row {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 8px;
            margin-top: auto;
        }

        .action-btn {
            border: none;
            border-radius: 6px;
            padding: 0.35rem;
            font-size: 0.72rem;
            font-weight: 700;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            transition: background 0.2s ease;
        }

        .btn-view {
            background: var(--color-blue);
            color: white;
        }
        .btn-view:hover {
            background: #1d4ed8;
        }

        .btn-open {
            background: var(--color-green);
            color: white;
        }
        .btn-open:hover {
            background: #059669;
        }

        .btn-copy {
            background: var(--color-grey-light);
            color: var(--color-grey-dark);
            border: 1px solid var(--border-color);
        }
        .btn-copy:hover {
            background: #e5e7eb;
        }

        /* Alert/Notification Box */
        #toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: var(--color-grey-dark);
            color: white;
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            z-index: 2000;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            display: none;
            animation: fadeInUp 0.2s ease;
        }

        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
</head>
<body>
    <!-- Floating actions bar for selected items -->
    <div id="selectionPanel" class="selection-panel">
        <span class="selection-count">Selected: <span id="selectedCount">0</span> items</span>
        <button class="selection-btn selection-btn-copy" onclick="copySelected('sku')">Copy SKUs</button>
        <button class="selection-btn selection-btn-copy" onclick="copySelected('zsku')">Copy ZSKUs</button>
        <button class="selection-btn selection-btn-clear" onclick="clearSelection()">Clear</button>
    </div>

    <div class="app-layout">
        <aside class="sidebar">

            <!-- START / END PRICE -->
            <div class="sidebar-section">
                <label class="sidebar-label">START / END PRICE</label>
                <div class="price-inputs">
                    <input type="number" id="startPrice" class="price-input" placeholder="Min">
                    <input type="number" id="endPrice" class="price-input" placeholder="Max">
                </div>
                <div class="price-buttons">
                    <button class="sidebar-btn btn-sidebar-secondary" onclick="resetPriceFilter()">Use full</button>
                    <button class="sidebar-btn btn-sidebar-primary" onclick="applyFilters()">Apply</button>
                </div>
                <p class="sidebar-subtext">Database price range: {min_db_price} to {max_db_price} (strict filter)</p>
            </div>
        </aside>

        <main class="main-content">
            <div class="container">
                <header>
                    <h1>AI Listing Matching Results</h1>
                    <p>Database Sheet: Best_One_Row_Per_SKU | Matches Found: {total_matches}</p>
                </header>

                <div class="results-grid">
"""

    best_visual = 0.0
    best_text = 0.0

    for rank, item in enumerate(results, 1):
        sku = item.get('SKU', '').strip()
        title = item.get('Title', '')
        price = item.get('Price', 0.0)
        ai_score = item.get('AI Score', None)
        text_sim = item.get('Text Similarity', 0.0)
        source_file = item.get('Source File', 'Unknown')
        row_num = item.get('Row', '')

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

        # Lookup extra attributes from the loaded Excel sheet
        sku_lookup = sku.upper()
        matched_sku_key = next((k for k in extra_attrs if k.upper() == sku_lookup), None)
        attrs = extra_attrs.get(matched_sku_key, {}) if matched_sku_key else {}

        # Fetch extra fields
        zsku = attrs.get('Best_ZSKU', sku)
        if pd.isna(zsku):
            zsku = sku
        brand = attrs.get('Best_Brand', 'Generic')
        if pd.isna(brand):
            brand = 'Generic'
        color = attrs.get('Best_Color', '')
        stock = attrs.get('Best_Stock', '')
        product_url = attrs.get('Best_Product_URL', '')
        match_count = str(attrs.get('Match_Count', '1')).split('.')[0]  # Format float to int string

        # Build category/brand/stock pills
        tags_html = ""
        tags_html += f'<span class="tag-pill">{brand}</span>'
        if color and not pd.isna(color):
            tags_html += f'<span class="tag-pill">{color}</span>'
        if stock and not pd.isna(stock):
            tags_html += f'<span class="tag-pill">{stock}</span>'

        # Generate list of thumbnail previews from Combined_All_Image_URLs
        all_imgs_str = attrs.get('Combined_All_Image_URLs', '')
        thumbnails_html = ""
        img_urls_list = []
        if all_imgs_str and not pd.isna(all_imgs_str):
            # Split by comma or semicolon
            split_char = ';' if ';' in str(all_imgs_str) else ','
            img_urls_list = [u.strip() for u in str(all_imgs_str).split(split_char) if u.strip()]
            
            # Show up to 6 thumbnails
            for i, u in enumerate(img_urls_list[:6]):
                thumbnails_html += f'<img class="thumb-img" src="{u}" alt="thumb" onclick="swapMainImage(this, \'{sku}\')">'
        else:
            # If no combined urls, just show the main image in thumbnail row
            thumbnails_html += f'<img class="thumb-img active" src="{image_path}" alt="thumb">'

        price_str = f"AED {price:.0f}" if price else "N/A"
        match_badge_str = f"{match_count} SKU" if match_count else "1 SKU"

        # Build card template
        html_content += f"""
            <div class="match-card" data-sku="{sku}" data-zsku="{zsku}">
                <!-- Checkbox Row -->
                <div class="card-select-row" style="display: flex; justify-content: space-between; align-items: center;">
                    <label>
                        <input type="checkbox" class="select-checkbox" data-sku="{sku}" data-zsku="{zsku}" onchange="updateSelection()"> Select
                    </label>
                    <span style="font-size: 0.75rem; font-weight: 800; color: var(--color-blue); background: #eff6ff; padding: 2px 6px; border-radius: 4px;">#{item.get('Rank', rank)}</span>
                </div>

                <!-- Main Image -->
                <div class="image-container">
                    <img id="mainImg_{sku}" src="{image_path}" alt="{title}" onerror="this.src='https://placehold.co/300x300/121829/ffffff?text=Image+Not+Found'">
                </div>

                <!-- Pills directly under image -->
                <div class="pills-row">
                    <span class="pill-badge pill-price">{price_str}</span>
                    <span class="pill-badge pill-dark">SKU</span>
                    <span class="pill-badge pill-dark">{match_badge_str}</span>
                </div>

                <!-- Product Title -->
                <div class="product-title" title="{title}">{title}</div>

                <!-- Tags / Custom attributes -->
                <div class="tags-row">
                    {tags_html}
                    <span class="tag-pill">{len(img_urls_list)} imgs</span>
                </div>

                <!-- SKU Table Box -->
                <div class="sku-box">
                    <div class="sku-row">
                        <span class="sku-label">SKU</span>
                        <span class="sku-val" id="skuText_{sku}">{sku}</span>
                        <button class="copy-btn" onclick="copyText('skuText_{sku}')">Copy</button>
                    </div>
                    <div class="sku-row">
                        <span class="sku-label">ZSKU</span>
                        <span class="sku-val" id="zskuText_{sku}">{zsku}</span>
                        <button class="copy-btn" onclick="copyText('zskuText_{sku}')">Copy</button>
                    </div>
                </div>

                <!-- Verification Badges -->
                <div class="verification-row">
                    <span class="verify-badge" style="background:#d1fae5; color:#065f46;">URL Ready</span>
                    <span class="verify-badge" style="background:#d1fae5; color:#065f46;">Image Ready</span>
                </div>

                <!-- Thumbnails Row -->
                <div class="thumbnails-row">
                    {thumbnails_html}
                </div>

                <!-- Action Buttons -->
                <div class="actions-row">
                    <button class="action-btn btn-view" onclick="viewImage('{image_path}')">View</button>
                    <a class="action-btn btn-open" href="{product_url}" target="_blank">Open</a>
                    <button class="action-btn btn-copy" onclick="copyTextDirect('{sku}')">Copy</button>
                </div>
            </div>
        """

    html_content += """
        </div>
    </div>
</div>
</div>

    <!-- Notification Toast Box -->
    <div id="toast">Copied to clipboard!</div>

    <!-- Modals or full image viewer if needed -->
    <script>
        function swapMainImage(thumb, skuId) {
            const mainImg = document.getElementById("mainImg_" + skuId);
            if (mainImg) {
                mainImg.src = thumb.src;
            }
            
            // Toggle active border class in parent row
            const parent = thumb.parentElement;
            const children = parent.getElementsByClassName("thumb-img");
            for (let i = 0; i < children.length; i++) {
                children[i].classList.remove("active");
            }
            thumb.classList.add("active");
        }

        function viewImage(imgPath) {
            window.open(imgPath, '_blank');
        }

        function showToast(message) {
            const toast = document.getElementById('toast');
            toast.innerText = message || "Copied to clipboard!";
            toast.style.display = 'block';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 1500);
        }

        function copyText(elemId) {
            const textVal = document.getElementById(elemId).innerText;
            navigator.clipboard.writeText(textVal).then(() => {
                showToast("Copied: " + textVal);
            });
        }

        function copyTextDirect(text) {
            navigator.clipboard.writeText(text).then(() => {
                showToast("Copied SKU: " + text);
            });
        }

        /* Selection Feature Logic */
        let selectedSKUs = new Set();
        let selectedZSKUs = new Set();

        function updateSelection() {
            const checkboxes = document.querySelectorAll('.select-checkbox');
            selectedSKUs.clear();
            selectedZSKUs.clear();

            checkboxes.forEach(cb => {
                if (cb.checked) {
                    selectedSKUs.add(cb.getAttribute('data-sku'));
                    selectedZSKUs.add(cb.getAttribute('data-zsku'));
                }
            });

            const countSpan = document.getElementById('selectedCount');
            const panel = document.getElementById('selectionPanel');
            
            countSpan.innerText = selectedSKUs.size;
            
            if (selectedSKUs.size > 0) {
                panel.style.display = 'flex';
            } else {
                panel.style.display = 'none';
            }
        }

        function clearSelection() {
            const checkboxes = document.querySelectorAll('.select-checkbox');
            checkboxes.forEach(cb => cb.checked = false);
            updateSelection();
        }

        function copySelected(type) {
            const items = type === 'sku' ? Array.from(selectedSKUs) : Array.from(selectedZSKUs);
            if (items.length === 0) return;
            
            const textToCopy = items.join('\\n');
            navigator.clipboard.writeText(textToCopy).then(() => {
                showToast("Copied " + items.length + " selected " + type.toUpperCase() + "s!");
            });
        }

        function applyFilters() {
            const startPrice = parseFloat(document.getElementById('startPrice').value) || 0;
            const endPrice = parseFloat(document.getElementById('endPrice').value) || Infinity;

            const cards = document.querySelectorAll('.match-card');
            cards.forEach(card => {
                const priceBadge = card.querySelector('.pill-price');
                const priceText = priceBadge ? priceBadge.innerText.replace('AED', '').trim() : '';
                const price = parseFloat(priceText) || 0;
                
                let show = true;
                
                // Price filter
                if (price < startPrice || price > endPrice) {
                    show = false;
                }

                if (show) {
                    card.style.display = 'flex';
                } else {
                    card.style.display = 'none';
                }
            });
            
            // Clear selections that are now hidden
            updateSelection();
        }

        function resetPriceFilter() {
            document.getElementById('startPrice').value = '';
            document.getElementById('endPrice').value = '';
            applyFilters();
        }
    </script>
</body>
</html>
"""

    # Format the header values
    html_content = html_content.replace("{total_matches}", str(len(results)))
    html_content = html_content.replace("{best_visual}", f"{best_visual:.3f}" if best_visual else "N/A")
    html_content = html_content.replace("{best_text}", f"{best_text:.3f}" if best_text else "N/A")
    html_content = html_content.replace("{min_db_price}", str(min_db_price))
    html_content = html_content.replace("{max_db_price}", str(max_db_price))

    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Successfully generated HTML report at: {output_html}")

if __name__ == "__main__":
    generate_html_report()
