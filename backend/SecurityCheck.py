import re
import math
import io
from PIL import Image
from PIL.ExifTags import TAGS

class ImageSecurityScanner:
    def __init__(self):
        self.signatures = {
            '.jpg': {'start': b'\xff\xd8\xff', 'end': b'\xff\xd9'},
            '.jpeg': {'start': b'\xff\xd8\xff', 'end': b'\xff\xd9'},
            '.png': {'start': b'\x89\x50\x4e\x47', 'end': b'\xae\x42\x60\x82'}
        }

    def calculate_entropy(self, data):
        if not data: return 0
        entropy = 0
        for x in range(256):
            p_x = float(data.count(x)) / len(data)
            if p_x > 0:
                entropy += - p_x * math.log(p_x, 2)
        return entropy

    # def scan_metadata(self, data_bytes):
    #     meta_results = []
    #     try:
    #         # Chuyển bytes thành file ảo trong bộ nhớ để Pillow đọc
    #         img = Image.open(io.BytesIO(data_bytes))
    #         info = img.getexif()
    #         if info:
    #             for tag, value in info.items():
    #                 tag_name = TAGS.get(tag, tag)
    #                 if isinstance(value, str) and any(kw in value.lower() for kw in ['script', 'eval', 'exec', 'system']):
    #                     meta_results.append(f"Metadata nhạy cảm ở {tag_name}")
    #     except: pass
    #     return meta_results

    def scan_bytes(self, data, filename):
        ext = "." + filename.split('.')[-1].lower()
        alerts = []
        
        # 1. Check Magic Bytes
        if ext in self.signatures and not data.startswith(self.signatures[ext]['start']):
            alerts.append("Header không khớp định dạng.")

        # 2. Check Overlay & Entropy
        if ext in self.signatures:
            eof_marker = self.signatures[ext]['end']
            eof_index = data.rfind(eof_marker)
            if eof_index != -1 and eof_index < len(data) - len(eof_marker):
                overlay = data[eof_index + len(eof_marker):]
                entropy = self.calculate_entropy(overlay)
                if entropy > 7.0 or len(overlay) > 100: # Nếu overlay quá lớn hoặc entropy cao
                    alerts.append(f"Phát hiện dữ liệu lạ (Overlay). Entropy: {entropy:.2f}")
                
                # Quét từ khóa mã độc trong overlay
                patterns = [rb'WScript\.Shell', rb'shell\.run', rb'mshta', rb'cmd\.exe', rb'MsgBox', rb'<script']
                for p in patterns:
                    if re.search(p, overlay, re.IGNORECASE):
                        alerts.append(f"Tìm thấy mã thực thi: {p.decode()}")

        # 3. Check Metadata
        # meta_alerts = self.scan_metadata(data)
        # alerts.extend(meta_alerts)

        return {"is_secure": len(alerts) == 0, "details": alerts}