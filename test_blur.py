import numpy as np
from PIL import Image, ImageFilter
import imagehash
import os

def get_edges_blurred(img):
    gray = img.convert('L')
    arr = np.array(gray, dtype=np.float32)
    gx = np.zeros_like(arr)
    gy = np.zeros_like(arr)
    gx[:, :-1] = np.diff(arr, axis=1)
    gy[:-1, :] = np.diff(arr, axis=0)
    magnitude = np.sqrt(gx**2 + gy**2)
    mag_max = magnitude.max()
    if mag_max > 0:
        bin_arr = ((magnitude > (0.12 * mag_max)) * 255.0).astype(np.uint8)
        bin_img = Image.fromarray(bin_arr)
        blurred_img = bin_img.filter(ImageFilter.GaussianBlur(radius=3))
        return blurred_img
    return gray

def main():
    path_5 = "downloaded_images/Z4CEFF12E7FDCDC9787B6Z.jpg"
    path_12 = "downloaded_images/Z4087DA66081A1F17C983Z.jpg"
    
    if not os.path.exists(path_5) or not os.path.exists(path_12):
        print("Images not found.")
        return
        
    img_5 = Image.open(path_5)
    img_12 = Image.open(path_12)
    
    edge_5 = get_edges_blurred(img_5)
    edge_12 = get_edges_blurred(img_12)
    
    ph_5 = imagehash.phash(edge_5)
    ph_12 = imagehash.phash(edge_12)
    
    dh_5 = imagehash.dhash(edge_5)
    dh_12 = imagehash.dhash(edge_12)
    
    ah_5 = imagehash.average_hash(edge_5)
    ah_12 = imagehash.average_hash(edge_12)
    
    print(f"pHash distance: {ph_5 - ph_12}")
    print(f"dHash distance: {dh_5 - dh_12}")
    print(f"aHash distance: {ah_5 - ah_12}")
    
    g_resized = np.array(edge_5.resize((128, 128)), dtype=np.float32) / 255.0
    b_resized = np.array(edge_12.resize((128, 128)), dtype=np.float32) / 255.0
    
    g_mean, b_mean = np.mean(g_resized), np.mean(b_resized)
    g_diff, b_diff = g_resized - g_mean, b_resized - b_mean
    num = np.sum(g_diff * b_diff)
    den = np.sqrt(np.sum(g_diff ** 2) * np.sum(b_diff ** 2))
    correlation = num / den if den > 0 else 0.0
    
    print(f"Correlation: {correlation:.4f}")

if __name__ == "__main__":
    main()
