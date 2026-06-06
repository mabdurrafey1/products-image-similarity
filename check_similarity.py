from PIL import Image
import numpy as np

def main():
    q_path = "/Users/mabdurrafey/.gemini/antigravity-ide/brain/111e293e-93ca-42ba-b384-92e0074a1dc7/uploaded_media_1780427019892.jpg"
    db_path = "downloaded_images/Z97FD3ADA5F046E6391DDZ.jpg"
    
    img_q = Image.open(q_path)
    img_db = Image.open(db_path)
    
    print(f"Query size: {img_q.size}, Database size: {img_db.size}")
    
    # Calculate simple pixel correlation on raw grayscale
    q_gray = np.array(img_q.convert('L').resize((128, 128)), dtype=np.float32) / 255.0
    db_gray = np.array(img_db.convert('L').resize((128, 128)), dtype=np.float32) / 255.0
    
    q_mean, db_mean = np.mean(q_gray), np.mean(db_gray)
    q_diff, db_diff = q_gray - q_mean, db_gray - db_mean
    num = np.sum(q_diff * db_diff)
    den = np.sqrt(np.sum(q_diff ** 2) * np.sum(db_diff ** 2))
    corr = num / den if den > 0 else 0.0
    
    print(f"Raw Grayscale Correlation: {corr:.4f}")

if __name__ == "__main__":
    main()
