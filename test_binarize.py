import numpy as np
from PIL import Image
import imagehash
import os

# Helper to compute edge outlines
def get_edges_bin(img):
    gray = img.convert('L')
    arr = np.array(gray, dtype=np.float32)
    gx = np.zeros_like(arr)
    gy = np.zeros_like(arr)
    gx[:, :-1] = np.diff(arr, axis=1)
    gy[:-1, :] = np.diff(arr, axis=0)
    magnitude = np.sqrt(gx**2 + gy**2)
    mag_max = magnitude.max()
    if mag_max > 0:
        # Binarize: any pixel with gradient > 15% of max gradient is 255, rest 0
        bin_img = (magnitude > (0.15 * mag_max)) * 255.0
        return Image.fromarray(bin_img.astype(np.uint8))
    return gray

def main():
    # Green Case (Row 73) and Black Case (Row 83)
    path_green = "downloaded_images/Z67C39555FE9CC623CE6FZ.jpg"
    path_black = "downloaded_images/ZE47F9782EBDE6C5A3BE5Z.jpg"
    
    if not os.path.exists(path_green) or not os.path.exists(path_black):
        print("Images not found. Run duplicate_finder.py first.")
        return
        
    img_green = Image.open(path_green)
    img_black = Image.open(path_black)
    
    # Calculate hashes of binary edges
    edge_g = get_edges_bin(img_green)
    edge_b = get_edges_bin(img_black)
    
    ph_g = imagehash.phash(edge_g)
    ph_b = imagehash.phash(edge_b)
    
    dh_g = imagehash.dhash(edge_g)
    dh_b = imagehash.dhash(edge_b)
    
    ah_g = imagehash.average_hash(edge_g)
    ah_b = imagehash.average_hash(edge_b)
    
    print("Hashes using Binarized Edges:")
    print(f"pHash distance: {ph_g - ph_b}")
    print(f"dHash distance: {dh_g - dh_b}")
    print(f"aHash distance: {ah_g - ah_b}")
    
    # Correlation & MSE
    g_resized = np.array(edge_g.resize((128, 128)), dtype=np.float32) / 255.0
    b_resized = np.array(edge_b.resize((128, 128)), dtype=np.float32) / 255.0
    
    mse = np.mean((g_resized - b_resized) ** 2)
    
    g_mean, b_mean = np.mean(g_resized), np.mean(b_resized)
    g_diff, b_diff = g_resized - g_mean, b_resized - b_mean
    num = np.sum(g_diff * b_diff)
    den = np.sqrt(np.sum(g_diff ** 2) * np.sum(b_diff ** 2))
    correlation = num / den if den > 0 else 0.0
    
    print(f"Correlation: {correlation:.4f}")
    print(f"MSE: {mse:.4f}")

if __name__ == "__main__":
    main()
