from PIL import Image, ImageFilter
import numpy as np
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
    
    # Save the edge outline images to inspect them
    edge_5.save("edge_5.jpg")
    edge_12.save("edge_12.jpg")
    print("Saved edge_5.jpg and edge_12.jpg")
    
    # Print pixel coverage
    arr_5 = np.array(edge_5)
    arr_12 = np.array(edge_12)
    print(f"Edge 5 non-zero pixels: {np.sum(arr_5 > 0)} / {arr_5.size}")
    print(f"Edge 12 non-zero pixels: {np.sum(arr_12 > 0)} / {arr_12.size}")

if __name__ == "__main__":
    main()
