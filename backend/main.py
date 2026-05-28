from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
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
import uuid
import hashlib
import random
from moviepy import VideoFileClip
from skimage.metrics import structural_similarity as ssim

# --- Thư viện Bảo mật của bạn bè ---
from SecurityCheck import ImageSecurityScanner
from PIL import Image, PngImagePlugin
import io
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

app = FastAPI(title="Hệ thống Thủy vân số DWT-SVD-PSO & RSA")
scanner = ImageSecurityScanner()

# Cấu hình CORS: BẮT BUỘC có expose_headers để ReactJS lấy được mã Hash của Video
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Logo-Hash"] 
)

os.makedirs("temp", exist_ok=True)
PRIVATE_KEY_PATH = "private_key.pem"
PUBLIC_KEY_PATH = "public_key.pem"

if not os.path.exists(PRIVATE_KEY_PATH) or not os.path.exists(PUBLIC_KEY_PATH):
    # Sinh cặp khóa 2048-bit nếu chưa có
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    
    # Lưu Private Key
    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    # Lưu Public Key
    with open(PUBLIC_KEY_PATH, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))

# Nạp khóa vào bộ nhớ RAM để sẵn sàng xử lý
with open(PRIVATE_KEY_PATH, "rb") as f:
    SERVER_PRIVATE_KEY = serialization.load_pem_private_key(f.read(), password=None)
with open(PUBLIC_KEY_PATH, "rb") as f:
    SERVER_PUBLIC_KEY = serialization.load_pem_public_key(f.read())

# ==========================================
# CÁC HÀM BỔ TRỢ & TÍNH TOÁN CHỈ SỐ
# ==========================================
def generate_image_hash(img_array):
    _, buffer = cv2.imencode('.png', img_array)
    return hashlib.sha256(buffer.tobytes()).hexdigest()

def calculate_psnr(img1, img2):
    mse = np.mean((img1.astype(np.float64) - img2.astype(np.float64)) ** 2)
    if mse == 0: return 100.0
    return 20 * math.log10(255.0 / math.sqrt(mse))

def calculate_ssim(img1, img2):
    if len(img1.shape) == 3:
        return ssim(img1, img2, channel_axis=2, data_range=255)
    return ssim(img1, img2, data_range=255)

def calculate_nc(wm1, wm2):
    wm1_flat = wm1.flatten().astype(np.float64)
    wm2_flat = wm2.flatten().astype(np.float64)
    den = np.linalg.norm(wm1_flat) * np.linalg.norm(wm2_flat)
    return np.dot(wm1_flat, wm2_flat) / den if den != 0 else 0.0

# ==========================================
# CÁC HÀM TẤN CÔNG ẢNH
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

# ==========================================
# CÁC HÀM BẢO MẬT RSA
# ==========================================
def inject_rsa_signature_png(cv2_img):
    """Bước 1: Tính SHA-256 của pixel -> Bước 2: Ký bằng Private Key -> Bước 3: Nhét vào chunk tEXt"""
    pil_img = Image.fromarray(cv2.cvtColor(cv2_img, cv2.COLOR_BGR2RGB))
    
    pixel_bytes = pil_img.tobytes()
    img_hash = hashlib.sha256(pixel_bytes).digest()
    
    signature = SERVER_PRIVATE_KEY.sign(
        img_hash,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256()
    )
    signature_b64_str = base64.b64encode(signature).decode('utf-8')
    
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Nhom2_RSA_Signature", signature_b64_str)
    
    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG", pnginfo=png_info)
    
    return buffer.getvalue()


def verify_rsa_signature_png(file_bytes):
    """Mở file PNG -> Bóc chunk tEXt -> Tính lại SHA-256 pixel -> Dùng Public Key verify"""
    try:
        pil_img = Image.open(io.BytesIO(file_bytes))
        
        if "Nhom2_RSA_Signature" not in pil_img.info:
            return {"is_authentic": False }
            
        signature_b64_str = pil_img.info["Nhom2_RSA_Signature"]
        signature = base64.b64decode(signature_b64_str)
        
        pixel_bytes = pil_img.tobytes()
        current_hash = hashlib.sha256(pixel_bytes).digest()
        
        SERVER_PUBLIC_KEY.verify(
            signature,
            current_hash,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return {"is_authentic": True, "reason": "Xác thực thành công! Ảnh chính hãng Nhóm 2 và NGUYÊN VẸN 100%."}
        
    except Exception:
        return {"is_authentic": False, "reason": "CẢNH BÁO: Ảnh đã từng nhúng hệ thống Nhóm 2 nhưng ĐÃ BỊ SỬA ĐỔI / CHỈNH SỬA cấu trúc màu!"}

# ==========================================
# API 1: NHÚNG ẢNH
# ==========================================
@app.post("/api/nhung-thuy-van")
async def process_embedding(
    host_file: UploadFile = File(...), 
    logo_file: UploadFile = File(...),
    alpha: float = Form(0.1),
    use_pso: bool = Form(False)
):
    h_img = cv2.imdecode(np.frombuffer(await host_file.read(), np.uint8), cv2.IMREAD_COLOR)
    l_img_raw = cv2.imdecode(np.frombuffer(await logo_file.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    
    logo_hash = generate_image_hash(l_img_raw)

    h_orig, w_orig = h_img.shape[:2]
    h_adj, w_adj = (h_orig // 2) * 2, (w_orig // 2) * 2
    h_img = cv2.resize(h_img, (w_adj, h_adj))
    k_size_h, k_size_w = h_adj // 2, w_adj // 2
    l_img = cv2.resize(l_img_raw, (k_size_w, k_size_h))
    
    b, g, r = cv2.split(h_img)
    final_alpha = alpha

    if use_pso:
        proxy_b = cv2.resize(b, (256, 256))
        proxy_l = cv2.resize(l_img, (128, 128))
        optimizer = ps.single.GlobalBestPSO(n_particles=10, dimensions=1, 
                                            options={'c1': 1.5, 'c2': 1.5, 'w': 0.5}, 
                                            bounds=(np.array([0.10]), np.array([0.40])))
        _, best_pos = optimizer.optimize(fitness_function, iters=8, host_b=proxy_b, wm_img=proxy_l)
        final_alpha = float(best_pos[0])

    stego_b, _, _, _ = embed_dwt_svd(b, l_img, final_alpha)
    final_stego = cv2.merge((stego_b, g, r))
    
    # Tính toán PSNR và SSIM (Code của bạn)
    psnr_val = calculate_psnr(h_img, final_stego)
    ssim_val = calculate_ssim(h_img, final_stego)
    
    # Ký RSA lên file ảnh (Code của bạn bè)
    png_file_bytes = inject_rsa_signature_png(final_stego)
    stego_b64 = "data:image/png;base64," + base64.b64encode(png_file_bytes).decode('utf-8')
    
    return JSONResponse({
        "optimized_alpha": round(final_alpha, 4),
        "logo_hash": logo_hash,
        "psnr_score": round(psnr_val, 2),
        "ssim_score": round(ssim_val, 4),
        "stego_image": stego_b64
    })

# ==========================================
# API 2: TRÍCH XUẤT ẢNH
# ==========================================
@app.post("/api/trich-xuat")
async def process_extraction(
    watermarked_file: UploadFile = File(...), 
    host_file: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    alpha: float = Form(...),
    original_logo_hash: str = Form(...)
):
    wm_data = await watermarked_file.read()
    h_data = await host_file.read()
    l_data = await logo_file.read()
    
    # --- BƯỚC KIỂM TRA BẢO MẬT MÃ ĐỘC ---
    scan_result = scanner.scan_bytes(wm_data, watermarked_file.filename)
    
    if not scan_result["is_secure"]:
        return JSONResponse(
            status_code=412,
            content={
                "error_type": "SECURITY_MALWARE_ALERT",
                "msg": "Hệ thống phát hiện tệp tin chứa mã thực thi độc hại!",
                "filename": watermarked_file.filename,
                "details": scan_result["details"] 
            }
        )

    wm_img = cv2.imdecode(np.frombuffer(wm_data, np.uint8), cv2.IMREAD_COLOR)
    h_img = cv2.imdecode(np.frombuffer(h_data, np.uint8), cv2.IMREAD_COLOR)
    l_img_orig = cv2.imdecode(np.frombuffer(l_data, np.uint8), cv2.IMREAD_GRAYSCALE)
    
    if wm_img is None or h_img is None:
        raise HTTPException(status_code=400, detail="Không thể đọc định dạng ảnh.")

    # Mã Hash Logo gốc
    if generate_image_hash(l_img_orig) != original_logo_hash:
        raise HTTPException(status_code=403, detail="TỪ CHỐI TRUY CẬP: Logo không khớp với hồ sơ gốc.")

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
    dynamic_threshold = min(20.0 / alpha, 150.0) 
    
    if error_margin > dynamic_threshold:
        raise HTTPException(status_code=403, detail=f"SAI HỆ SỐ ALPHA! Lỗi năng lượng: {error_margin:.2f}")

    nc = calculate_nc(l_img, ext_wm)
    if nc < 0.75:
        raise HTTPException(status_code=403, detail=f"Bằng chứng giả mạo hoặc hỏng nặng! NC: {round(nc*100,2)}%")

    # Đọc trực tiếp text ẩn từ bức ảnh nghi ngờ
    rsa_result = verify_rsa_signature_png(wm_data)
    is_from_system_2 = "YES" if (rsa_result["is_authentic"]) else "NO"

    ext_wm_final = cv2.resize(ext_wm, (orig_logo_w, orig_logo_h))
    _, buffer = cv2.imencode('.png', ext_wm_final)
    ext_b64 = "data:image/png;base64," + base64.b64encode(buffer).decode('utf-8')
    
    return JSONResponse({
        "nc_score": round(nc, 4), 
        "extracted_logo": ext_b64,
        "is_from_system_2": is_from_system_2,
        "marker_nc": 1.0 if is_from_system_2 == "YES" else 0.0
    })

# ==========================================
# API 3: TẤN CÔNG ẢNH
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
# API 4: NHÚNG VIDEO (KHỐI RỜI RẠC + NGẪU NHIÊN)
# ==========================================
@app.post("/api/nhung-video")
async def process_video_watermark(
    background_tasks: BackgroundTasks, 
    video_file: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    mode: str = Form("hidden"), 
    position: str = Form("bottom-right"),
    alpha: float = Form(0.1)
):
    logo_bytes = await logo_file.read()
    l_img_orig_raw = cv2.imdecode(np.frombuffer(logo_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    
    logo_hash = generate_image_hash(l_img_orig_raw)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_vid:
        shutil.copyfileobj(video_file.file, tmp_vid)
        in_path = tmp_vid.name
        
    l_img = cv2.imdecode(np.frombuffer(logo_bytes, np.uint8), cv2.IMREAD_COLOR)

    cap = cv2.VideoCapture(in_path)
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    orig_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    width = (orig_width // 2) * 2
    height = (orig_height // 2) * 2
    unique_id = uuid.uuid4().hex
    silent_out_path = f"temp/silent_{mode}_{unique_id}.mp4"
    fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
    out = cv2.VideoWriter(silent_out_path, fourcc, fps, (width, height))

    BLOCK_SIZE = (min(256, width, height) // 2) * 2
    if mode in ["hidden", "dual"]:
        l_img_gray = cv2.cvtColor(l_img, cv2.COLOR_BGR2GRAY)
        l_img_gray = cv2.resize(l_img_gray, (BLOCK_SIZE // 2, BLOCK_SIZE // 2))

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        
        frame = cv2.resize(frame, (width, height))
        processed_frame = frame.copy()
        
        if mode == "visible" or mode == "dual":
            processed_frame = apply_visible_watermark(processed_frame, l_img, position)

        if mode == "hidden" or mode == "dual":
            if random.random() < 0.30: 
                b, g, r = cv2.split(processed_frame)
                start_y = (height - BLOCK_SIZE) // 2
                start_x = (width - BLOCK_SIZE) // 2
                block_b = b[start_y : start_y + BLOCK_SIZE, start_x : start_x + BLOCK_SIZE]
                
                stego_block, _, _, _ = embed_dwt_svd(block_b, l_img_gray, alpha)
                b[start_y : start_y + BLOCK_SIZE, start_x : start_x + BLOCK_SIZE] = stego_block
                
                processed_frame = cv2.merge((b, g, r))
                
        out.write(processed_frame)

    cap.release()
    out.release()
    
    final_out_path = f"temp/watermarked_{mode}_{unique_id}.mp4"
    try:
        orig_clip = VideoFileClip(in_path)
        silent_clip = VideoFileClip(silent_out_path)
        if orig_clip.audio is not None:
            final_clip = silent_clip.with_audio(orig_clip.audio)
            final_clip.write_videofile(final_out_path, codec="libx264", audio_codec="aac", logger=None)
        else:
            silent_clip.write_videofile(final_out_path, codec="libx264", logger=None)
        orig_clip.close()
        silent_clip.close()
        os.remove(silent_out_path) 
    except Exception as e:
        final_out_path = silent_out_path 

    os.remove(in_path) 
    background_tasks.add_task(os.remove, final_out_path)
    
    custom_headers = {"X-Logo-Hash": logo_hash}
    return FileResponse(
        final_out_path, 
        media_type="video/mp4", 
        filename=f"watermarked_{mode}.mp4",
        headers=custom_headers
    )

# ==========================================
# API 5: TRÍCH XUẤT VIDEO
# ==========================================
@app.post("/api/trich-xuat-video")
async def process_video_extraction(
    suspect_video: UploadFile = File(...), 
    host_video: UploadFile = File(...),
    logo_file: UploadFile = File(...),
    alpha: float = Form(...),
    original_logo_hash: str = Form(...)
):
    l_img_orig = cv2.imdecode(np.frombuffer(await logo_file.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if generate_image_hash(l_img_orig) != original_logo_hash:
        raise HTTPException(status_code=403, detail="TỪ CHỐI TRUY CẬP: Phát hiện logo giả mạo.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_s:
        shutil.copyfileobj(suspect_video.file, tmp_s)
        vid_s_path = tmp_s.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_h:
        shutil.copyfileobj(host_video.file, tmp_h)
        vid_h_path = tmp_h.name

    cap_s = cv2.VideoCapture(vid_s_path)
    cap_h = cv2.VideoCapture(vid_h_path)
    
    orig_width = int(cap_s.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_height = int(cap_s.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width, height = (orig_width // 2) * 2, (orig_height // 2) * 2
    
    BLOCK_SIZE = (min(256, width, height) // 2) * 2
    l_img = cv2.resize(l_img_orig, (BLOCK_SIZE // 2, BLOCK_SIZE // 2))
    
    MAX_FRAMES_TO_CHECK = 180
    frames_checked = 0
    best_nc = 0.0
    best_ext_wm = None

    while cap_s.isOpened() and cap_h.isOpened() and frames_checked < MAX_FRAMES_TO_CHECK:
        ret_s, frame_s = cap_s.read()
        ret_h, frame_h = cap_h.read()
        if not ret_s or not ret_h: break
        
        frame_s = cv2.resize(frame_s, (width, height))
        frame_h = cv2.resize(frame_h, (width, height))
        
        b_s, _, _ = cv2.split(frame_s)
        b_h, _, _ = cv2.split(frame_h)
        
        start_y = (height - BLOCK_SIZE) // 2
        start_x = (width - BLOCK_SIZE) // 2
        
        block_b_s = b_s[start_y : start_y + BLOCK_SIZE, start_x : start_x + BLOCK_SIZE]
        block_b_h = b_h[start_y : start_y + BLOCK_SIZE, start_x : start_x + BLOCK_SIZE]
        
        _, S_h, U_w, V_w = embed_dwt_svd(block_b_h, l_img, alpha) 
        ext_wm, _ = extract_dwt_svd(block_b_s, S_h, U_w, V_w, alpha)
        
        nc = calculate_nc(l_img, ext_wm)
        if nc > best_nc:
            best_nc = nc
            best_ext_wm = ext_wm
            
        if best_nc > 0.75: 
            break
            
        frames_checked += 1

    cap_s.release()
    cap_h.release()
    os.remove(vid_s_path)
    os.remove(vid_h_path)

    if best_nc < 0.75 or best_ext_wm is None:
        raise HTTPException(status_code=403, detail=f"Không tìm thấy thủy vân ẩn hợp lệ. NC cao nhất: {round(best_nc*100,2)}%")

    orig_logo_h, orig_logo_w = l_img_orig.shape[:2]
    ext_wm_final = cv2.resize(best_ext_wm, (orig_logo_w, orig_logo_h))
    _, buffer = cv2.imencode('.png', ext_wm_final)
    ext_b64 = "data:image/png;base64," + base64.b64encode(buffer).decode('utf-8')
    
    return JSONResponse({"nc_score": round(best_nc, 4), "extracted_logo": ext_b64})

# ==========================================
# API 6: KIỂM TRA QUÉT NGẦM TRƯỚC KHI NHÚNG
# ==========================================
@app.post("/api/kiem-tra-nhung-trung")
async def check_over_watermarking(host_file: UploadFile = File(...)):
    try:
        file_bytes = await host_file.read()
        
        rsa_result = verify_rsa_signature_png(file_bytes)
        
        if rsa_result["is_authentic"]:
            return JSONResponse({
                "is_already_watermarked": True,
                "status_code": "AUTHENTIC",
                "message": rsa_result["reason"]
            })
            
        if "SỬA ĐỔI" in rsa_result["reason"]:
            return JSONResponse({
                "is_already_watermarked": True, 
                "status_code": "TAMPERED_ATTACK",
                "message": "Ảnh này đã bị sửa đổi so với ban đầu của hệ thống, có thể đã bị tấn công!"
            })

        return JSONResponse({
            "is_already_watermarked": False,
            "status_code": "CLEAN",
            "message": rsa_result["reason"]
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)