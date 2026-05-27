from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2
import numpy as np
import pywt
import math
import os
import pyswarms as ps
import base64
import tempfile
import shutil
from moviepy import VideoFileClip
from SecurityCheck import ImageSecurityScanner

app = FastAPI(title="Watermarking DWT-SVD-PSO & Video")
scanner = ImageSecurityScanner()
# Cấu hình CORS cho ReactJS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("temp", exist_ok=True)

# ==========================================
# CÁC HÀM TOÁN HỌC BỔ TRỢ
# ==========================================
def calculate_psnr(img1, img2):
    mse = np.mean((img1.astype(np.float64) - img2.astype(np.float64)) ** 2)
    if mse == 0: return 100.0
    return 20 * math.log10(255.0 / math.sqrt(mse))

def calculate_nc(wm1, wm2):
    wm1_flat = wm1.flatten().astype(np.float64)
    wm2_flat = wm2.flatten().astype(np.float64)
    den = np.linalg.norm(wm1_flat) * np.linalg.norm(wm2_flat)
    return np.dot(wm1_flat, wm2_flat) / den if den != 0 else 0.0

# ==========================================
# CÁC KỊCH BẢN TẤN CÔNG (ROBUSTNESS)
# ==========================================
def attack_jpeg(img, quality=80):
    _, encimg = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if len(img.shape) == 2: return cv2.imdecode(encimg, cv2.IMREAD_GRAYSCALE)
    else: return cv2.imdecode(encimg, cv2.IMREAD_COLOR)

def attack_noise(img, variance=0.05):
    sigma = variance ** 0.5
    gauss = np.random.normal(0, sigma, img.shape)
    noisy = img.astype(np.float64) + gauss * 255
    return np.clip(noisy, 0, 255).astype(np.uint8)

def attack_crop(img, percent=0.1):
    safe_percent = min(float(percent), 0.45) 
    h, w = img.shape[:2]
    ch, cw = int(h * safe_percent), int(w * safe_percent)
    cropped = np.copy(img)
    cv2.rectangle(cropped, (0, 0), (w, ch), (0, 0, 0), -1)          
    cv2.rectangle(cropped, (0, h - ch), (w, h), (0, 0, 0), -1)      
    cv2.rectangle(cropped, (0, 0), (cw, h), (0, 0, 0), -1)          
    cv2.rectangle(cropped, (w - cw, 0), (w, h), (0, 0, 0), -1)      
    return cropped

def attack_blur(img, ksize=5):
    ksize = int(ksize)
    if ksize % 2 == 0: ksize += 1
    return cv2.GaussianBlur(img, (ksize, ksize), 0)

# ==========================================
# THUẬT TOÁN LÕI DWT + SVD
# ==========================================
def embed_dwt_svd(host_img, wm_img, alpha):
    coeffs = pywt.dwt2(host_img, 'haar')
    LL, (HL, LH, HH) = coeffs
    
    U_h, S_h, V_h = np.linalg.svd(HL, full_matrices=False)
    U_w, S_w, V_w = np.linalg.svd(wm_img.astype(np.float32), full_matrices=False)
    
    S_new = S_h + alpha * S_w
    HL_new = np.dot(U_h, np.dot(np.diag(S_new), V_h))
    
    watermarked_img = pywt.idwt2((LL, (HL_new, LH, HH)), 'haar')
    return np.clip(watermarked_img, 0, 255).astype(np.uint8), S_h, U_w, V_w

def extract_dwt_svd(suspect_img, S_h, U_w, V_w, alpha):
    coeffs = pywt.dwt2(suspect_img, 'haar')
    _, (HL_s, _, _) = coeffs
    _, S_s, _ = np.linalg.svd(HL_s, full_matrices=False)
    
    S_w_ext = (S_s - S_h) / alpha
    extracted_wm = np.dot(U_w, np.dot(np.diag(S_w_ext), V_w))
    
    ex_min, ex_max = np.min(extracted_wm), np.max(extracted_wm)
    if ex_max > ex_min:
        extracted_wm = (extracted_wm - ex_min) / (ex_max - ex_min) * 255.0
    return np.clip(extracted_wm, 0, 255).astype(np.uint8), S_w_ext

# ==========================================
# HÀM BỔ TRỢ: NHÚNG HIỆN (VIDEO)
# ==========================================
def apply_visible_watermark(frame, logo, position="bottom-right", opacity=0.6):
    fh, fw = frame.shape[:2]
    lh, lw = logo.shape[:2]
    scale = (fw * 0.15) / lw
    new_lw, new_lh = int(lw * scale), int(lh * scale)
    logo_resized = cv2.resize(logo, (new_lw, new_lh))
    
    padding = 20
    if position == "top-left": x, y = padding, padding
    elif position == "top-right": x, y = fw - new_lw - padding, padding
    elif position == "bottom-left": x, y = padding, fh - new_lh - padding
    elif position == "center": x, y = (fw - new_lw) // 2, (fh - new_lh) // 2
    else: x, y = fw - new_lw - padding, fh - new_lh - padding

    roi = frame[y:y+new_lh, x:x+new_lw]
    blended = cv2.addWeighted(roi, 1 - opacity, logo_resized, opacity, 0)
    frame[y:y+new_lh, x:x+new_lw] = blended
    return frame

# ==========================================
# GIẢI THUẬT TỐI ƯU HÓA BẦY ĐÀN (PSO)
# ==========================================
def fitness_function(alphas, host_b, wm_img):
    n_particles = alphas.shape[0]
    costs = np.zeros(n_particles)
    for i in range(n_particles):
        a_i = alphas[i][0]
        stego, S_h, U_w, V_w = embed_dwt_svd(host_b, wm_img, a_i)
        psnr_val = calculate_psnr(host_b, stego)
        
        attacked = attack_jpeg(stego, 80)
        ext_wm, _ = extract_dwt_svd(attacked, S_h, U_w, V_w, a_i)
        nc_val = calculate_nc(wm_img, ext_wm)
        
        score = (0.4 * (psnr_val / 50.0)) + (0.6 * nc_val)
        costs[i] = -score
    return costs

        
def inject_lsb_text(img, text="Duoc nhung boi Nhom 2"):
    """Nhúng chuỗi text vào các bit cuối cùng (LSB) của các pixel đầu tiên trên kênh B"""
    # Đảm bảo ảnh đầu vào được đưa về kiểu uint8 chuẩn
    img = img.astype(np.uint8)
    
    # Chuyển chuỗi kí tự thành chuỗi bit
    bits = ''.join(format(ord(c), '08b') for c in text) + '00000000' # Thêm byte kết thúc
    b_channel = img[:, :, 0].copy().astype(np.uint8) # Ép kiểu uint8 cho kênh B
    
    h, w = b_channel.shape
    bit_idx = 0
    total_bits = len(bits)
    
    for i in range(h):
        for j in range(w):
            if bit_idx < total_bits:
                # Ép kiểu int rõ ràng cho phần tử ma trận trước khi tính toán bit
                pixel_val = int(b_channel[i, j])
                b_channel[i, j] = (pixel_val & ~1) | int(bits[bit_idx])
                bit_idx += 1
            else:
                img[:, :, 0] = b_channel
                return img
    img[:, :, 0] = b_channel
    return img
def extract_lsb_text(img):
    """Trích xuất chuỗi text từ các bit cuối cùng (LSB) của kênh B"""
    # Đảm bảo ma trận ảnh truyền vào là số nguyên uint8
    img = img.astype(np.uint8)
    b_channel = img[:, :, 0]
    
    h, w = b_channel.shape
    bits = ""
    current_byte = ""
    extracted_text = ""
    
    for i in range(h):
        for j in range(w):
            # Ép kiểu dữ liệu pixel về int thuần túy của Python để lấy bit cuối
            pixel_val = int(b_channel[i, j])
            bit = str(pixel_val & 1)
            current_byte += bit
            
            if len(current_byte) == 8:
                char_code = int(current_byte, 2)
                if char_code == 0: # Gặp byte kết thúc (Null) thì dừng
                    return extracted_text
                # Chỉ nhận các ký tự ASCII hiển thị được hợp lệ
                if 32 <= char_code <= 126:
                    extracted_text += chr(char_code)
                else:
                    # Nếu gặp ký tự rác quá nhiều (ảnh sạch), tự động dừng sớm
                    if len(extracted_text) == 0 and len(current_byte) == 8:
                        return ""
                current_byte = ""
                
                if len(extracted_text) > 30: 
                    return extracted_text
    return extracted_text
# ==========================================
# API 1: NHÚNG BẢN QUYỀN ẢNH (TRẢ VỀ BASE64)
# ==========================================
@app.post("/api/nhung-thuy-van")
async def process_embedding(
    host_file: UploadFile = File(...), 
    logo_file: UploadFile = File(...),
    alpha: float = Form(0.1),
    use_pso: bool = Form(False)
):
    h_img = cv2.imdecode(np.frombuffer(await host_file.read(), np.uint8), cv2.IMREAD_COLOR)
    l_img = cv2.imdecode(np.frombuffer(await logo_file.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    
    h_orig, w_orig = h_img.shape[:2]
    h_adj, w_adj = (h_orig // 2) * 2, (w_orig // 2) * 2
    h_img = cv2.resize(h_img, (w_adj, h_adj))
    k_size_h, k_size_w = h_adj // 2, w_adj // 2
    l_img = cv2.resize(l_img, (k_size_w, k_size_h))
    
    b, g, r = cv2.split(h_img)
    final_alpha = alpha

    if use_pso:
        proxy_b = cv2.resize(b, (256, 256))
        proxy_l = cv2.resize(l_img, (128, 128))
        optimizer = ps.single.GlobalBestPSO(n_particles=10, dimensions=1, 
                                            options={'c1': 1.5, 'c2': 1.5, 'w': 0.5}, 
                                            bounds=(np.array([0.01]), np.array([0.40])))
        _, best_pos = optimizer.optimize(fitness_function, iters=8, host_b=proxy_b, wm_img=proxy_l)
        final_alpha = float(best_pos[0])

    stego_b, _, _, _ = embed_dwt_svd(b, l_img, final_alpha)
    final_stego = cv2.merge((stego_b, g, r))
    final_stego = inject_lsb_text(final_stego, "Duoc nhung boi Nhom 2")
    _, buffer = cv2.imencode('.png', final_stego)
    stego_b64 = "data:image/png;base64," + base64.b64encode(buffer).decode('utf-8')
    
    return JSONResponse({
        "optimized_alpha": round(final_alpha, 4),
        "stego_image": stego_b64
    })

# ==========================================
# API 2: TRÍCH XUẤT ẢNH (TRẢ VỀ BASE64 JSON)
# ==========================================
@app.post("/api/trich-xuat")
async def process_extraction(
    watermarked_file: UploadFile = File(...), 
    host_file: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    alpha: float = Form(...)
):

    wm_data = await watermarked_file.read()
    h_data = await host_file.read()
    l_data = await logo_file.read()
    # --- BƯỚC KIỂM TRA BẢO MẬT ---
    scan_result = scanner.scan_bytes(wm_data, watermarked_file.filename)
    
    if not scan_result["is_secure"]:
        # THAY THẾ raise HTTPException BẰNG LỆNH TRẢ VỀ JSONRESPONSE
        return JSONResponse(
            status_code=412,
            content={
                "error_type": "SECURITY_MALWARE_ALERT",
                "msg": "Hệ thống phát hiện tệp tin chứa mã thực thi độc hại!",
                "filename": watermarked_file.filename,
                "details": scan_result["details"] # Đây là mảng dictionary/list chi tiết lỗi
            }
        )

    wm_img = cv2.imdecode(np.frombuffer(wm_data, np.uint8), cv2.IMREAD_COLOR)
    h_img = cv2.imdecode(np.frombuffer(h_data, np.uint8), cv2.IMREAD_COLOR)
    l_img_orig = cv2.imdecode(np.frombuffer(l_data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if wm_img is None or h_img is None:
        raise HTTPException(status_code=400, detail="Không thể đọc định dạng ảnh.")
    orig_logo_h, orig_logo_w = l_img_orig.shape[:2]
    h_adj, w_adj = (h_img.shape[0] // 2) * 2, (h_img.shape[1] // 2) * 2
    h_img = cv2.resize(h_img, (w_adj, h_adj))
    wm_img = cv2.resize(wm_img, (w_adj, h_adj))
    l_img = cv2.resize(l_img_orig, (w_adj // 2, h_adj // 2))
    
    b_h, _, _ = cv2.split(h_img)
    b_wm, _, _ = cv2.split(wm_img)
    
    _, S_h, U_w, V_w = embed_dwt_svd(b_h, l_img, alpha) 
    _, S_w_expected, _ = np.linalg.svd(l_img.astype(np.float32), full_matrices=False)
    
    ext_wm, S_w_ext = extract_dwt_svd(b_wm, S_h, U_w, V_w, alpha)
    
    error_margin = np.mean(np.abs(S_w_ext - S_w_expected))
    dynamic_threshold = 20.0 / alpha 
    if error_margin > dynamic_threshold:
        raise HTTPException(status_code=403, detail=f"SAI HỆ SỐ ALPHA! Mức năng lượng không khớp ({error_margin:.2f}).")

    nc = calculate_nc(l_img, ext_wm)
    if nc < 0.75:
        raise HTTPException(status_code=403, detail=f"Bằng chứng giả mạo hoặc ảnh đã bị hỏng nặng! NC: {round(nc*100,2)}%")

    # === ĐOẠN TRÍCH XUẤT MÃ HỆ THỐNG NGẦM ===
    marker_pattern = generate_text_watermark(w_adj // 2, h_adj // 2)
    nc_system_marker = calculate_nc(marker_pattern, ext_wm)
    is_from_system_2 = "YES" if nc_system_marker > 0.60 else "NO"


    ext_wm_final = cv2.resize(ext_wm, (orig_logo_w, orig_logo_h))
    _, buffer = cv2.imencode('.png', ext_wm_final)
    ext_b64 = "data:image/png;base64," + base64.b64encode(buffer).decode('utf-8')
    
    return JSONResponse({
        "nc_score": round(nc, 4), 
        "extracted_logo": ext_b64,
        "is_from_system_2": is_from_system_2,
        "marker_nc": round(nc_system_marker, 4)
    })

# ==========================================
# API 3: KIỂM THỬ TẤN CÔNG ẢNH
# ==========================================
@app.post("/api/tan-cong")
async def process_attack(
    watermarked_file: UploadFile = File(...), 
    host_file: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    alpha: float = Form(...),
    attack_type: str = Form("jpeg"),
    intensity: float = Form(80)
):
    wm_img = cv2.imdecode(np.frombuffer(await watermarked_file.read(), np.uint8), cv2.IMREAD_COLOR)
    h_img = cv2.imdecode(np.frombuffer(await host_file.read(), np.uint8), cv2.IMREAD_COLOR)
    l_img_orig = cv2.imdecode(np.frombuffer(await logo_file.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    
    orig_logo_h, orig_logo_w = l_img_orig.shape[:2] 
    h_adj, w_adj = (h_img.shape[0] // 2) * 2, (h_img.shape[1] // 2) * 2
    h_img = cv2.resize(h_img, (w_adj, h_adj))
    wm_img = cv2.resize(wm_img, (w_adj, h_adj))
    l_img = cv2.resize(l_img_orig, (w_adj // 2, h_adj // 2))
    
    b_h, _, _ = cv2.split(h_img)
    _, S_h, U_w, V_w = embed_dwt_svd(b_h, l_img, alpha) 
    
    attacked_img = np.copy(wm_img)
    if attack_type == "jpeg": attacked_img = attack_jpeg(wm_img, quality=int(intensity))
    elif attack_type == "noise": attacked_img = attack_noise(wm_img, variance=intensity)
    elif attack_type == "crop": attacked_img = attack_crop(wm_img, percent=intensity)
    elif attack_type == "blur": attacked_img = attack_blur(wm_img, ksize=intensity)

    b_att, _, _ = cv2.split(attacked_img)
    ext_wm, _ = extract_dwt_svd(b_att, S_h, U_w, V_w, alpha)
    
    nc = calculate_nc(l_img, ext_wm)
    ext_wm_final = cv2.resize(ext_wm, (orig_logo_w, orig_logo_h))

    _, att_buf = cv2.imencode('.jpg', attacked_img)
    _, ext_buf = cv2.imencode('.png', ext_wm_final)
    
    att_b64 = "data:image/jpeg;base64," + base64.b64encode(att_buf).decode('utf-8')
    ext_b64 = "data:image/png;base64," + base64.b64encode(ext_buf).decode('utf-8')

    return JSONResponse({"nc_score": round(nc, 4), "attacked_image": att_b64, "extracted_logo": ext_b64})

# ==========================================
# API 4: NHÚNG VIDEO
# ==========================================
@app.post("/api/nhung-video")
async def process_video_watermark(
    video_file: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    mode: str = Form("hidden"), 
    position: str = Form("bottom-right"),
    alpha: float = Form(0.1)
):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_vid:
        shutil.copyfileobj(video_file.file, tmp_vid)
        in_path = tmp_vid.name
        
    l_img = cv2.imdecode(np.frombuffer(await logo_file.read(), np.uint8), cv2.IMREAD_COLOR)

    cap = cv2.VideoCapture(in_path)
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # 1. OpenCV TRƯỚC TIÊN XUẤT RA VIDEO "CÂM" (SILENT)
    silent_out_path = f"temp/silent_{mode}.webm"
    fourcc = cv2.VideoWriter_fourcc(*'vp80') 
    out = cv2.VideoWriter(silent_out_path, fourcc, fps, (width, height))

    if mode == "hidden":
        l_img_gray = cv2.cvtColor(l_img, cv2.COLOR_BGR2GRAY)
        l_img_gray = cv2.resize(l_img_gray, (width // 4, height // 4))

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        
        if mode == "visible":
            processed_frame = apply_visible_watermark(frame, l_img, position)
        else: 
            b, g, r = cv2.split(frame)
            stego_b, _, _, _ = embed_dwt_svd(b, l_img_gray, alpha)
            processed_frame = cv2.merge((stego_b, g, r))
            
        out.write(processed_frame)

    cap.release()
    out.release()
    
    # =========================================================
    # 2. MOVIEPY: BÓC AUDIO TỪ VIDEO GỐC VÀ GHÉP VÀO VIDEO MỚI
    # =========================================================
    final_out_path = f"temp/watermarked_{mode}.webm"
    
    try:
        # Load lại video gốc và video câm
        orig_clip = VideoFileClip(in_path)
        silent_clip = VideoFileClip(silent_out_path)

        # Kiểm tra xem video gốc có âm thanh không
        if orig_clip.audio is not None:
            # Gắn audio gốc vào video đã xử lý thủy vân
            final_clip = silent_clip.with_audio(orig_clip.audio)
            
            # Lưu file cuối cùng ra (dùng chuẩn libvorbis cho âm thanh trên WebM)
            final_clip.write_videofile(final_out_path, codec="libvpx", audio_codec="libvorbis", logger=None)
        else:
            # Nếu video gốc vốn không có tiếng, chỉ cần đổi tên file
            shutil.copy(silent_out_path, final_out_path)

        # Đóng tài nguyên
        orig_clip.close()
        silent_clip.close()
        os.remove(silent_out_path) # Xóa file câm đi cho nhẹ máy
        
    except Exception as e:
        print(f"Lỗi ghép Audio: {e}")
        # Rủi ro nếu MoviePy lỗi, vẫn trả về video câm để hệ thống không bị sập
        final_out_path = silent_out_path 

    os.remove(in_path) 

    return FileResponse(final_out_path, media_type="video/webm", filename=f"watermarked_{mode}.webm")

# ==========================================
# API: KIỂM TRA QUÉT NGẦM TRƯỚC KHI NHÚNG
# ==========================================
@app.post("/api/kiem-tra-nhung-trung")
async def check_over_watermarking(host_file: UploadFile = File(...)):
    try:
        h_img = cv2.imdecode(np.frombuffer(await host_file.read(), np.uint8), cv2.IMREAD_COLOR)
        if h_img is None:
            return JSONResponse(status_code=400, content={"message": "Không thể đọc định dạng ảnh."})
            
        # Trích xuất chuỗi chữ giấu trong LSB
        secret_text = extract_lsb_text(h_img)
        
        # Kiểm tra xem có chứa đúng từ khóa của Nhóm 2 không
        is_already_watermarked = "Duoc nhung boi Nhom 2" in secret_text
        
        return JSONResponse({
            "is_already_watermarked": is_already_watermarked,
            "secret_text": secret_text
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": str(e)})
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)