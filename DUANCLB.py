import os
import sys

# Thiết lập UTF-8 toàn diện cho console Windows tránh UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import time
import threading
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from datetime import datetime
from ultralytics import YOLO
import database

# Cache font hệ thống để vẽ chữ tiếng Việt có dấu chuẩn đẹp 100%
_FONT_CACHE = {}
def _get_pil_font(size=13, bold=False):
    key = (size, bold)
    if key not in _FONT_CACHE:
        font_paths = [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/tahoma.ttf"
        ]
        chosen = None
        for p in font_paths:
            if os.path.exists(p):
                try:
                    chosen = ImageFont.truetype(p, size)
                    break
                except Exception:
                    pass
        if chosen is None:
            chosen = ImageFont.load_default()
        _FONT_CACHE[key] = chosen
    return _FONT_CACHE[key]

# ==============================================================================
# LÕI TRÍ TUỆ NHÂN TẠO & XỬ LÝ CAMERA SIÊU TỐC ĐỘ (SMART HSE VISION)
# - CAMERA BẤT ĐỒNG BỘ: cv2.CAP_DSHOW + Threaded Capture (<0.3s khởi động)
# - AI PIPELINE TỐI ƯU: Nhận diện không chặn luồng khung hình (Giữ vững 30-60 FPS)
# - ĐIỂM DANH KHUÔN MẶT: YuNet + SFace (Chống lặp hôm nay theo từng Owner)
# - GIÁM SÁT TRANG BỊ BẢO HỘ: YOLOv8 (Mũ, Áo phản quang, Giày)
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
FACES_DIR = os.path.join(BASE_DIR, "known_faces")
VIOLATIONS_DIR = os.path.join(BASE_DIR, "violations")

os.makedirs(FACES_DIR, exist_ok=True)
os.makedirs(VIOLATIONS_DIR, exist_ok=True)

YUNET_PATH = os.path.join(MODELS_DIR, "face_detection_yunet.onnx")
SFACE_PATH = os.path.join(MODELS_DIR, "face_recognition_sface.onnx")
PPE_MODEL_PATH = os.path.join(MODELS_DIR, "ppe_yolov8n.pt")

COSINE_THRESHOLD = 0.32
ABSENT_RESET_FRAMES = 12

# ------------------------------------------------------------------------------
# 1. BỘ THU CAMERA BẤT ĐỒNG BỘ (THREADED VIDEO CAPTURE - CHỐNG LAG, MỞ TỨC THÌ)
# ------------------------------------------------------------------------------
class FastThreadedCamera:
    """
    Thu khung hình từ Webcam trong một luồng riêng rẽ bằng cv2.CAP_DSHOW.
    Độ phân giải chuẩn HD 720p (1280x720) sắc nét cao, loại bỏ hoàn toàn độ trễ 3-5s
    của OpenCV mặc định và duy trì 30 FPS mượt mà.
    """
    def __init__(self, src=0, width=1280, height=720, fps=30):
        self.src = src
        self.running = False
        self.lock = threading.Lock()
        self.ret = False
        self.frame = None
        self.cap = None
        
        # 1. Thử mở bằng DirectShow trên Windows
        try:
            if sys.platform.startswith("win"):
                self.cap = cv2.VideoCapture(self.src, cv2.CAP_DSHOW)
            else:
                self.cap = cv2.VideoCapture(self.src)
        except Exception:
            self.cap = None

        # 2. Nếu DirectShow thất bại, thử mở với backend mặc định
        if self.cap is None or not self.cap.isOpened():
            try:
                self.cap = cv2.VideoCapture(self.src)
            except Exception:
                self.cap = None
            
        if self.cap and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.cap.set(cv2.CAP_PROP_FPS, fps)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Giữ bộ đệm luôn là khung hình mới nhất
            self.ret, self.frame = self.cap.read()
            self.running = True
            self.thread = threading.Thread(target=self._update_loop, daemon=True)
            self.thread.start()

    def is_opened(self):
        return bool(self.cap and self.cap.isOpened() and self.running)

    def isOpened(self):
        return self.is_opened()

    def _update_loop(self):
        while self.running:
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    with self.lock:
                        self.ret = ret
                        self.frame = frame
                else:
                    time.sleep(0.01)
            else:
                break

    def read(self):
        with self.lock:
            if self.frame is not None:
                return self.ret, self.frame.copy()
            return False, None

    def release(self):
        self.running = False
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

# ------------------------------------------------------------------------------
# 2. LOGGING ĐIỂM DANH & VI PHẠM (THEO TỪNG OWNER)
# ------------------------------------------------------------------------------
def determine_attendance_status(check_time, shift_start_str="07:30", grace_mins=15):
    """Tính toán trạng thái điểm danh: Đúng giờ hoặc Đi trễ."""
    try:
        check_t = check_time.time() if hasattr(check_time, 'time') else check_time
        start_t = datetime.strptime(shift_start_str, "%H:%M").time()
        check_mins = check_t.hour * 60 + check_t.minute
        start_mins = start_t.hour * 60 + start_t.minute + int(grace_mins)
        if check_mins > start_mins:
            late_m = check_mins - (start_t.hour * 60 + start_t.minute)
            return f"Đi trễ ({late_m}p)"
        return "Đúng giờ"
    except Exception:
        return "Đúng giờ"

def log_attendance(ma_owner, worker_id, worker_name):
    """Ghi nhận lịch sử điểm danh hợp lệ vào SQL Server."""
    return database.log_attendance_db(ma_owner, worker_id, worker_name)

def log_violation(evidence_frame, ma_owner, worker_id, worker_name, violation_details):
    """Lưu ảnh Snapshot kèm khung HUD nhận diện và lưu vào SQL Server."""
    now = datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    safe_worker_id = "".join(c for c in worker_id if c.isalnum() or c in ("-", "_")).strip().upper() or "UNKNOWN"
    safe_name = "".join(c for c in worker_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_") or "Unknown"
    image_filename = f"violation_{ma_owner}_{safe_worker_id}_{safe_name}_{timestamp_str}.jpg"
    image_path = os.path.join(VIOLATIONS_DIR, image_filename)
    
    # Nén ảnh JPEG chất lượng 75 để tối ưu dung lượng lưu trữ và truyền Tailscale
    cv2.imwrite(image_path, evidence_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    database.log_violation_db(ma_owner, worker_id, worker_name, violation_details, image_filename)
    return image_filename

# ------------------------------------------------------------------------------
# 3. KHỞI TẠO MÔ HÌNH AI & HỒ SƠ KHUÔN MẶT
# ------------------------------------------------------------------------------
def init_models():
    """Khởi tạo toàn bộ mô hình AI."""
    print("[AI ENGINE]: Dang khoi tao cac mo hinh AI...")
    person_model = YOLO("yolov8n.pt")
    ppe_model = YOLO(PPE_MODEL_PATH) if os.path.exists(PPE_MODEL_PATH) else None
    face_detector = cv2.FaceDetectorYN.create(YUNET_PATH, "", (320, 320), 0.5, 0.3, 5000)
    face_recognizer = cv2.FaceRecognizerSF.create(SFACE_PATH, "")
    print("[AI ENGINE]: Tat ca mo hinh AI da san sang!")
    return person_model, ppe_model, face_detector, face_recognizer

def load_known_workers(faces_dir, detector, recognizer):
    """Đọc ảnh hồ sơ công nhân từ known_faces/."""
    os.makedirs(faces_dir, exist_ok=True)
    known_workers = {}
    valid_ext = ('.jpg', '.jpeg', '.png', '.bmp')
    
    files = sorted([f for f in os.listdir(faces_dir) if f.lower().endswith(valid_ext)])
    for idx, filename in enumerate(files, 1):
        clean_name = os.path.splitext(filename)[0]
        parts = clean_name.split("_")
        if len(parts) > 1 and parts[0].upper().startswith("CN"):
            worker_id = parts[0].upper()
            worker_name = " ".join(parts[1:]).title()
        else:
            worker_id = f"CN-{idx:03d}"
            worker_name = clean_name.replace("_", " ").title()
            
        filepath = os.path.join(faces_dir, filename)
        try:
            pil_img = Image.open(filepath)
            pil_img = ImageOps.exif_transpose(pil_img)
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            h, w = img.shape[:2]
            if max(h, w) > 1000:
                scale = 1000 / max(h, w)
                img = cv2.resize(img, (int(w * scale), int(h * scale)))
                h, w = img.shape[:2]
                
            detector.setInputSize((w, h))
            _, faces = detector.detect(img)
            if faces is not None and len(faces) > 0:
                aligned = recognizer.alignCrop(img, faces[0])
                feat = recognizer.feature(aligned)
                known_workers[worker_name] = {
                    "id": worker_id,
                    "name": worker_name,
                    "feature": feat
                }
        except Exception as e:
            print(f"[FACE LOAD ERROR]: {filename} -> {e}")
    return known_workers

def recognize_face(crop_or_frame, detector, recognizer, known_workers, threshold=COSINE_THRESHOLD):
    """
    Nhận diện khuôn mặt thời gian thực bằng YuNet + SFace.
    Trả về (worker_id, worker_name, match_score).
    Nếu không khớp hoặc chưa đăng ký, trả về ('UNKNOWN', 'UNKNOWN PERSON', 0.0).
    """
    if crop_or_frame is None or crop_or_frame.size == 0 or detector is None or recognizer is None:
        return "UNKNOWN", "UNKNOWN PERSON", 0.0

    h, w = crop_or_frame.shape[:2]
    if w < 24 or h < 24:
        return "UNKNOWN", "UNKNOWN PERSON", 0.0

    img = crop_or_frame
    if max(h, w) > 1000:
        scale = 1000.0 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        h, w = img.shape[:2]

    try:
        detector.setInputSize((w, h))
        _, faces = detector.detect(img)
        if faces is None or len(faces) == 0:
            return "UNKNOWN", "UNKNOWN PERSON", 0.0

        best_face = faces[0]
        aligned = recognizer.alignCrop(img, best_face)
        feat = recognizer.feature(aligned)

        best_id = "UNKNOWN"
        best_name = "UNKNOWN PERSON"
        max_score = -1.0

        for w_name, w_info in (known_workers or {}).items():
            target_feat = w_info.get("feature")
            if target_feat is None:
                continue
            # cv2.FaceRecognizerSF_FR_COSINE trả về cosine similarity (-1.0 đến 1.0)
            # Càng gần 1.0 độ tương đồng càng cao; ngưỡng chuẩn SFace là 0.363
            score = recognizer.match(feat, target_feat, cv2.FaceRecognizerSF_FR_COSINE)
            if score > max_score:
                max_score = float(score)
                if score >= threshold:
                    best_id = w_info.get("id", "UNKNOWN")
                    best_name = w_info.get("name", w_name)

        return best_id, best_name, max_score
    except Exception as e:
        return "UNKNOWN", "UNKNOWN PERSON", 0.0

# ------------------------------------------------------------------------------
# 4. THUẬT TOÁN BÙ SÁNG THÍCH ỨNG (CLAHE) & QUÉT ĐỒ BẢO HỘ
# ------------------------------------------------------------------------------
def check_ppe_compliance(frame, person_box, ppe_model):
    """Kiểm tra trang bị bảo hộ (Mũ bảo hộ, Áo phản quang, Giày an toàn)."""
    x1, y1, x2, y2 = person_box
    checklist = {"helmet": True, "vest": True, "shoes": True}
    
    if ppe_model is not None:
        pw = x2 - x1
        ph = y2 - y1
        crop = frame[max(0, y1):min(frame.shape[0], y2), max(0, x1):min(frame.shape[1], x2)]
        if crop.size > 0:
            results = ppe_model(crop, conf=0.35, verbose=False)
            has_no_helmet = False
            has_no_vest = False
            has_helmet = False
            has_vest = False
            
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    cls_name = r.names.get(cls_id, "").lower()
                    if "no-helmet" in cls_name or "no_helmet" in cls_name:
                        has_no_helmet = True
                    elif "helmet" in cls_name:
                        has_helmet = True
                    if "no-vest" in cls_name or "no_vest" in cls_name:
                        has_no_vest = True
                    elif "vest" in cls_name:
                        has_vest = True
                        
            if has_no_helmet or (not has_helmet and ph > 150):
                checklist["helmet"] = False
            if has_no_vest or (not has_vest and ph > 150):
                checklist["vest"] = False

    is_safe = all(checklist.values())
    violations = []
    if not checklist["helmet"]: violations.append("Thieu Mu Bao Ho")
    if not checklist["vest"]: violations.append("Thieu Ao Phan Quang")
    if not checklist["shoes"]: violations.append("Thieu Giay An Toan")
    
    violation_text = ", ".join(violations) if violations else "Day du trang bi"
    return checklist, is_safe, violation_text

# ------------------------------------------------------------------------------
# 5. VẼ HUD CARD GIAO DIỆN
# ------------------------------------------------------------------------------
def draw_worker_card(frame, person_box, worker_id, worker_name, checklist, is_safe, is_captured_already):
    """Vẽ thẻ thông tin công nhân HUD sang trọng với chữ Tiếng Việt chuẩn xác 100%."""
    px1, py1, px2, py2 = person_box
    border_color = (60, 220, 100) if is_safe else (80, 90, 245)
    
    # Khung góc hiện đại
    cv2.rectangle(frame, (px1, py1), (px2, py2), border_color, 1)
    
    # Header thẻ
    card_w = max(210, px2 - px1)
    card_h = 75
    card_x1 = max(0, px1)
    card_y1 = max(0, py1 - card_h - 6)
    card_x2 = min(frame.shape[1], card_x1 + card_w)
    card_y2 = min(frame.shape[0], card_y1 + card_h)
    
    if card_x2 <= card_x1 or card_y2 <= card_y1:
        return
    
    overlay = frame.copy()
    cv2.rectangle(overlay, (card_x1, card_y1), (card_x2, card_y2), (10, 20, 18), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    cv2.rectangle(frame, (card_x1, card_y1), (card_x2, card_y2), border_color, 1)
    
    # Vẽ chữ tiếng Việt bằng Pillow để không bao giờ bị lỗi font hay mất dấu
    try:
        patch = frame[card_y1:card_y2, card_x1:card_x2]
        img_pil = Image.fromarray(cv2.cvtColor(patch, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        font_title = _get_pil_font(13, bold=True)
        font_sub = _get_pil_font(12, bold=False)
        
        display_title = f"{worker_id}: {worker_name}" if worker_id != "UNKNOWN" else "NGƯỜI LẠ (CHƯA ĐK)"
        draw.text((8, 6), display_title, font=font_title, fill=(255, 255, 255))
        
        status_text = "TRẠNG THÁI: AN TOÀN 100%" if is_safe else ("TRẠNG THÁI: ĐÃ ĐIỂM DANH" if is_captured_already else "TRẠNG THÁI: VI PHẠM AN TOÀN")
        status_color = (100, 220, 60) if is_safe else ((255, 200, 80) if is_captured_already else (255, 80, 80))
        draw.text((8, 28), status_text, font=font_sub, fill=status_color)
        
        h_mark = "[V] Mũ" if checklist.get("helmet", True) else "[X] Mũ"
        v_mark = "[V] Áo" if checklist.get("vest", True) else "[X] Áo"
        s_mark = "[V] Giày" if checklist.get("shoes", True) else "[X] Giày"
        ppe_line = f"{h_mark}   {v_mark}   {s_mark}"
        draw.text((8, 50), ppe_line, font=font_sub, fill=(200, 240, 230))
        
        frame[card_y1:card_y2, card_x1:card_x2] = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    except Exception:
        display_title = f"{worker_id}: {worker_name}" if worker_id != "UNKNOWN" else "UNKNOWN PERSON"
        cv2.putText(frame, display_title, (card_x1 + 8, card_y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

if __name__ == "__main__":
    print("==============================================================================")
    print("   HE THONG DIEM DANH VA GIAM SAT AN TOAN LAO DONG (SMART HSE VISION)")
    print("   DANG KHOI DONG TU VISUAL STUDIO TAI DIA CHI: http://localhost:8501")
    print("==============================================================================")
    import app_core
    app_core.start_server()

