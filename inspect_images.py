from PIL import Image
import numpy as np
import os

def check_rotation_similarities(path_a, path_b):
    img_a = Image.open(path_a).convert('L').resize((128, 128))
    img_b = Image.open(path_b).convert('L').resize((128, 128))
    
    a = np.array(img_a, dtype=np.float32) / 255.0
    
    # Try all rotations and flips of image B
    arr_b = np.array(img_b, dtype=np.float32) / 255.0
    orientations = [
        ("Original", arr_b),
        ("Rotate 90", np.rot90(arr_b, 1)),
        ("Rotate 180", np.rot90(arr_b, 2)),
        ("Rotate 270", np.rot90(arr_b, 3)),
        ("Flip LR", np.fliplr(arr_b)),
        ("Flip UD", np.flipud(arr_b)),
        ("Flip LR + Rotate 90", np.rot90(np.fliplr(arr_b), 1)),
    ]
    
    print(f"Comparing {os.path.basename(path_a)} vs {os.path.basename(path_b)}")
    print(f"Image A size: {Image.open(path_a).size}, Image B size: {Image.open(path_b).size}")
    
    for name, b in orientations:
        # Compute correlation
        a_mean, b_mean = np.mean(a), np.mean(b)
        a_diff, b_diff = a - a_mean, b - b_mean
        num = np.sum(a_diff * b_diff)
        den = np.sqrt(np.sum(a_diff ** 2) * np.sum(b_diff ** 2))
        corr = num / den if den > 0 else 0.0
        print(f"Orientation: {name:<20} | Correlation: {corr:.4f}")

if __name__ == "__main__":
    check_rotation_similarities("downloaded_images/Z4CEFF12E7FDCDC9787B6Z.jpg", "downloaded_images/Z4087DA66081A1F17C983Z.jpg")
