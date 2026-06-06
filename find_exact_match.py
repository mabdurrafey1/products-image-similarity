from PIL import Image
import numpy as np
import os
import glob

def main():
    q_path = "/Users/mabdurrafey/.gemini/antigravity-ide/brain/111e293e-93ca-42ba-b384-92e0074a1dc7/uploaded_media_1780427019892.jpg"
    img_q = Image.open(q_path)
    q_gray = np.array(img_q.convert('L').resize((128, 128)), dtype=np.float32) / 255.0
    q_mean = np.mean(q_gray)
    q_diff = q_gray - q_mean
    
    best_corr = -1.0
    best_file = None
    
    # Check all downloaded images
    for path in glob.glob("downloaded_images/*"):
        if not path.lower().endswith(('.jpg', '.jpeg', '.png')):
            continue
        try:
            img = Image.open(path)
            gray = np.array(img.convert('L').resize((128, 128)), dtype=np.float32) / 255.0
            mean = np.mean(gray)
            diff = gray - mean
            num = np.sum(q_diff * diff)
            den = np.sqrt(np.sum(q_diff ** 2) * np.sum(diff ** 2))
            corr = num / den if den > 0 else 0.0
            
            if corr > best_corr:
                best_corr = corr
                best_file = path
        except Exception as e:
            pass
            
    print(f"Best match in database: {best_file}")
    print(f"Correlation: {best_corr:.4f}")

if __name__ == "__main__":
    main()
