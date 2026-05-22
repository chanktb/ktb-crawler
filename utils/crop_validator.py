import cv2
import numpy as np

def verify_edge(img):
    """
    Tính phương sai mép viền để phát hiện chém họa tiết.
    - Nếu ảnh cắt chừa được phần vải trơn ở mép: Std Deviation cực thấp.
    - Nếu ảnh cắt lẹm vào chữ, hình vẽ: Std Deviation tăng vọt (>= 25).
    """
    h, w = img.shape[:2]
    if h < 10 or w < 10: return True, 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    top = gray[0:5, :]
    bottom = gray[h-5:h, :]
    left = gray[:, 0:5]
    right = gray[:, w-5:w]
    
    variance_scores = [np.std(edge) for edge in [top, bottom, left, right]]
    max_variance = max(variance_scores)
    
    if max_variance > 25.0:
        return False, max_variance
    return True, max_variance


def process_image(img_path, coords_dict):
    """
    Nhận vào đường dẫn ảnh gốc tải tạm, và tọa độ quy định trong config (x, y, w, h).
    Thực hiện cắt ảnh, kiểm duyệt phương sai viền, và GHI ĐÈ file đó thành ảnh đã cắt.
    Trả về bộ tuple: (Is_Safe_Boolean, Status_String, Variance_Value)
    """
    img = cv2.imread(img_path)
    if img is None:
        return False, "BAD_IMAGE", 0
        
    h_img, w_img = img.shape[:2]
    
    x = coords_dict.get("x", 0)
    y = coords_dict.get("y", 0)
    w = coords_dict.get("w", w_img)
    h = coords_dict.get("h", h_img)
    
    # Cắt lấy +5px an toàn
    c_x = max(0, x - 5)
    c_y = max(0, y - 5)
    c_w = min(w_img - c_x, w + 10)
    c_h = min(h_img - c_y, h + 10)
    
    cropped = img[c_y:c_y+c_h, c_x:c_x+c_w]
    
    # Cảm biến dò viền
    is_safe, variance = verify_edge(cropped)
    
    # Ghi đè file
    cv2.imwrite(img_path, cropped, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    
    if is_safe:
        return True, "PASS", variance
    else:
        return False, "MOCKUP_CHANGED", variance
