import os
import sys
import time
import io
import asyncio
import threading
import cv2
import numpy as np
import pandas as pd
from datetime import datetime
from PIL import Image, ImageOps
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ------------------------------------------------------------------------------
# 1. ĐƯỜNG DẪN & IMPORT CÁC MODULE DỰ ÁN
# ------------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
FACES_DIR = os.path.join(BASE_DIR, "known_faces")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
VIOLATIONS_DIR = os.path.join(BASE_DIR, "violations")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(FACES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(VIOLATIONS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

sys.path.append(BASE_DIR)
import DUANCLB
import database

# ------------------------------------------------------------------------------
# 2. KHỞI TẠO FASTAPI APP
# ------------------------------------------------------------------------------
app = FastAPI(title="Smart HSE Vision Web", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Phục vụ file tĩnh và ảnh vi phạm
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/violations", StaticFiles(directory=VIOLATIONS_DIR), name="violations")
app.mount("/known_faces", StaticFiles(directory=FACES_DIR), name="known_faces")

# ------------------------------------------------------------------------------
# 3. QUẢN LÝ CAMERA AI & LUỒNG VIDEO STREAM (THREAD SAFE)
# ------------------------------------------------------------------------------
class CameraManager:
    def __init__(self):
        self.is_running = False
        self.cap = None
        self.thread = None
        self.lock = threading.Lock()
        
        # Mô hình AI
        self.person_model = None
        self.ppe_model = None
        self.face_detector = None
        self.face_recognizer = None
        self.known_workers = {}
        self.models_loaded = False
        
        # Trạng thái thống kê Real-time
        self.person_count = 0
        self.safe_count = 0
        self.violation_count = 0
        self.latest_jpeg_bytes = None
        
        # Sự kiện nhận diện
        self.checked_in_workers = set()
        self.recent_events = []
        self.captured_violation_workers = set()
        self.violation_confirm_counter = {}
        self.absent_counters = {}
        self.last_unknown_violation_time = 0

    def ensure_models(self):
        if not self.models_loaded:
            print("[AI ENGINE]: Đang nạp các mô hình AI (YuNet, SFace, YOLOv8)...")
            self.person_model, self.ppe_model, self.face_detector, self.face_recognizer = DUANCLB.init_models()
            self.reload_known_faces()
            self.models_loaded = True
            print("[AI ENGINE]: Nạp mô hình thành công!")

    def reload_known_faces(self):
        if self.face_detector and self.face_recognizer:
            self.known_workers = DUANCLB.load_known_workers(FACES_DIR, self.face_detector, self.face_recognizer)
            print(f"[FACES]: Đã tải {len(self.known_workers)} hồ sơ khuôn mặt.")

    def start(self):
        with self.lock:
            if self.is_running:
                return True
            self.ensure_models()
            
            # Sử dụng FastThreadedCamera với cv2.CAP_DSHOW mở camera tức thì <0.2s
            self.cap = DUANCLB.FastThreadedCamera(0)
            if not self.cap.cap or not self.cap.cap.isOpened():
                print("[CAMERA]: Không thể kết nối Webcam!")
                return False
            
            # Cấu hình kích thước YuNet
            self.face_detector.setInputSize((640, 480))
            
            self.checked_in_workers = database.get_today_checked_in_workers("OWN01")
            self.captured_violation_workers.clear()
            self.violation_confirm_counter.clear()
            self.absent_counters.clear()
            self.is_running = True
            
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            print("[CAMERA]: Đã bật Camera bất đồng bộ và kích hoạt AI Vision siêu tốc!")
            return True

    def stop(self):
        with self.lock:
            self.is_running = False
            if self.cap:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
            cv2.destroyAllWindows()
            self.latest_jpeg_bytes = None
            self.person_count = 0
            self.safe_count = 0
            self.violation_count = 0
            print("[CAMERA]: Đã tắt Camera và giải phóng hoàn toàn phần cứng!")

    def _capture_loop(self):
        while self.is_running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.03)
                continue
            
            w = frame.shape[1]
            h = frame.shape[0]
            
            # 1. Nhận diện khuôn mặt (YuNet + SFace)
            detected_faces = []
            _, faces = self.face_detector.detect(frame)
            if faces is not None and len(faces) > 0:
                for face in faces:
                    fb = face[0:4].astype(int)
                    aligned = self.face_recognizer.alignCrop(frame, face)
                    feat = self.face_recognizer.feature(aligned)
                    
                    best_score = 0.0
                    matched_id = "UNKNOWN"
                    matched_name = "Nguoi la (Unknown)"
                    
                    for kname, kinfo in self.known_workers.items():
                        score = self.face_recognizer.match(feat, kinfo["feature"], cv2.FaceRecognizerSF_FR_COSINE)
                        if score > best_score and score >= DUANCLB.COSINE_THRESHOLD:
                            best_score = score
                            matched_id = kinfo["id"]
                            matched_name = kinfo["name"]
                            
                    if matched_name != "Nguoi la (Unknown)" and matched_name not in self.checked_in_workers:
                        self.checked_in_workers.add(matched_name)
                        self.checked_in_workers.add(matched_id)
                        DUANCLB.log_attendance("OWN01", matched_id, matched_name)
                        self.recent_events.insert(0, {
                            "type": "checkin",
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "name": matched_name,
                            "id": matched_id
                        })
                        if len(self.recent_events) > 10:
                            self.recent_events.pop()
                            
                    detected_faces.append({
                        "bbox": fb,
                        "id": matched_id,
                        "name": matched_name,
                        "score": best_score,
                        "center": (fb[0] + fb[2] // 2, fb[1] + fb[3] // 2)
                    })
                    
            # 2. Phát hiện người & PPE
            person_results = self.person_model(frame, classes=[0], conf=0.30, verbose=False)
            person_boxes = []
            for r in person_results:
                for box in r.boxes:
                    person_boxes.append(list(map(int, box.xyxy[0])))
                    
            if len(person_boxes) == 0 and len(detected_faces) > 0:
                for f_item in detected_faces:
                    fx, fy, fw, fh = f_item["bbox"]
                    person_boxes.append([max(0, fx - int(fw*0.5)), max(0, fy - int(fh*0.3)), 
                                         min(w, fx + int(fw*1.5)), min(h, fy + int(fh*3.5))])
                                         
            current_safe = 0
            current_vio = 0
            visible_workers = set()
            violations_to_capture = []
            
            for pbox in person_boxes:
                px1, py1, px2, py2 = pbox
                ph = py2 - py1
                identified_id = "UNKNOWN"
                identified_name = "Nguoi la (Unknown)"
                best_f_score = -1.0
                
                body_center_x = (px1 + px2) / 2
                for f_item in detected_faces:
                    fc_x, fc_y = f_item["center"]
                    if (px1 <= fc_x <= px2) and (py1 <= fc_y <= py1 + int(ph * 0.45)):
                        dist_penalty = (abs(fc_x - body_center_x) / max(1, (px2 - px1))) * 0.1
                        adjusted_score = f_item["score"] - dist_penalty
                        if adjusted_score > best_f_score:
                            best_f_score = adjusted_score
                            identified_id = f_item["id"]
                            identified_name = f_item["name"]
                            
                visible_workers.add(identified_name)
                self.absent_counters[identified_name] = 0
                
                checklist, is_safe, violation_text = DUANCLB.check_ppe_compliance(frame, (px1, py1, px2, py2), self.ppe_model)
                
                is_captured = False
                current_t = time.time()
                
                if is_safe:
                    current_safe += 1
                    self.violation_confirm_counter[identified_name] = 0
                else:
                    current_vio += 1
                    self.violation_confirm_counter[identified_name] = self.violation_confirm_counter.get(identified_name, 0) + 1
                    
                    if identified_name != "Nguoi la (Unknown)":
                        if identified_name not in self.captured_violation_workers and self.violation_confirm_counter[identified_name] >= 3:
                            self.captured_violation_workers.add(identified_name)
                            violations_to_capture.append((identified_id, identified_name, violation_text))
                        is_captured = (identified_name in self.captured_violation_workers)
                    else:
                        if (current_t - self.last_unknown_violation_time) > 10.0 and self.violation_confirm_counter[identified_name] >= 3:
                            self.last_unknown_violation_time = current_t
                            violations_to_capture.append((identified_id, identified_name, violation_text))
                            is_captured = True
                        else:
                            is_captured = True
                            
                DUANCLB.draw_worker_card(frame, (px1, py1, px2, py2), identified_id, identified_name, checklist, is_safe, is_captured)
                
            # Lưu ảnh bằng chứng đã chèn khung HUD
            for vid, vname, vtext in violations_to_capture:
                DUANCLB.log_violation(frame.copy(), "OWN01", vid, vname, vtext)
                self.recent_events.insert(0, {
                    "type": "violation",
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "name": vname,
                    "id": vid,
                    "text": vtext
                })
                if len(self.recent_events) > 10:
                    self.recent_events.pop()
                    
            # Reset lượt khi vắng mặt
            for worker in list(self.captured_violation_workers):
                if worker not in visible_workers:
                    self.absent_counters[worker] = self.absent_counters.get(worker, 0) + 1
                    if self.absent_counters[worker] > DUANCLB.ABSENT_RESET_FRAMES:
                        self.captured_violation_workers.remove(worker)
                        self.absent_counters.pop(worker, None)
                        self.violation_confirm_counter.pop(worker, None)
                        
            # Cập nhật số liệu Real-time
            self.person_count = len(person_boxes)
            self.safe_count = current_safe
            self.violation_count = current_vio
            
            # Encode frame sang JPEG (Chất lượng 75 tối ưu truyền Tailscale siêu nhẹ)
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ret:
                self.latest_jpeg_bytes = buf.tobytes()
                
            time.sleep(0.01)

camera_mgr = CameraManager()

# ------------------------------------------------------------------------------
# 4. API ENDPOINTS
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Smart HSE Vision Server Ready</h1>"

@app.get("/api/db/status")
async def get_db_status():
    ok, msg = database.test_db_connection()
    return {"connected": ok, "message": msg}

@app.post("/api/camera/start")
async def start_camera():
    success = camera_mgr.start()
    return {"success": success, "running": camera_mgr.is_running}

@app.post("/api/camera/stop")
async def stop_camera():
    camera_mgr.stop()
    return {"success": True, "running": False}

@app.get("/api/camera/status")
async def get_camera_status():
    today_workers = database.get_today_checked_in_workers()
    clean_today = [w for w in today_workers if not str(w).startswith("CN")]
    with camera_mgr.lock:
        recent_evs = list(camera_mgr.recent_events)
        p_count = camera_mgr.person_count
        s_count = camera_mgr.safe_count
        v_count = camera_mgr.violation_count
        is_run = camera_mgr.is_running
        
    return {
        "running": is_run,
        "person_count": p_count,
        "safe_count": s_count,
        "violation_count": v_count,
        "today_checkin_count": len(clean_today),
        "today_workers": clean_today[-8:],
        "recent_events": recent_evs
    }

async def mjpeg_generator():
    try:
        while camera_mgr.is_running:
            if camera_mgr.latest_jpeg_bytes:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + camera_mgr.latest_jpeg_bytes + b"\r\n")
            await asyncio.sleep(0.033)
    except (asyncio.CancelledError, GeneratorExit):
        pass

@app.get("/video_feed")
async def video_feed():
    if not camera_mgr.is_running:
        raise HTTPException(status_code=400, detail="Camera đang tắt")
    return StreamingResponse(
        mjpeg_generator(), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/api/attendance")
async def get_attendance_data():
    df = database.get_attendance_df()
    return {"data": df.to_dict(orient="records")}

@app.get("/api/violations")
async def get_violations_data():
    df = database.get_violations_df()
    records = df.to_dict(orient="records")
    for r in records:
        img_name = str(r.get("File Ảnh Bằng Chứng") or "").strip()
        img_path = os.path.join(VIOLATIONS_DIR, img_name) if img_name else ""
        if img_name and os.path.exists(img_path):
            r["has_image"] = True
            r["image_url"] = f"/violations/{img_name}"
        else:
            r["has_image"] = False
            r["image_url"] = None
    return {"data": records}

@app.get("/api/analytics")
async def get_analytics_data():
    df_att = database.get_attendance_df()
    df_vio = database.get_violations_df()
    
    total_att = len(df_att)
    total_vio = len(df_vio)
    safe_est = max(0, total_att - total_vio)
    compliance_rate = round((safe_est / total_att * 100) if total_att > 0 else 100, 1)
    
    helmet_err = 0
    vest_err = 0
    if not df_vio.empty:
        helmet_err = int(df_vio["Lỗi Vi Phạm Cụ Thể"].astype(str).str.contains("mu", case=False).sum())
        vest_err = int(df_vio["Lỗi Vi Phạm Cụ Thể"].astype(str).str.contains("ao", case=False).sum())
        
    return {
        "total_att": total_att,
        "safe_est": safe_est,
        "total_vio": total_vio,
        "compliance_rate": compliance_rate,
        "pie_data": {"safe": safe_est, "violation": total_vio},
        "bar_data": {"helmet": helmet_err, "vest": vest_err}
    }

@app.get("/api/workers")
async def get_workers():
    files = sorted([f for f in os.listdir(FACES_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    workers = []
    for f in files:
        base_name = os.path.splitext(f)[0]
        parts = base_name.split("_", 1)
        w_id = parts[0] if len(parts) > 1 and parts[0].startswith("CN") else "CN"
        w_name = parts[1].replace("_", " ") if len(parts) > 1 and parts[0].startswith("CN") else base_name.replace("_", " ")
        workers.append({
            "filename": f,
            "id": w_id,
            "name": w_name,
            "image_url": f"/known_faces/{f}"
        })
    return {"workers": workers, "count": len(workers)}

@app.post("/api/workers/add")
async def add_worker(
    worker_id: str = Form(...),
    worker_name: str = Form(...),
    photo: Optional[UploadFile] = File(None),
    captured_data: Optional[str] = Form(None)
):
    try:
        worker_id = "".join(c for c in worker_id if c.isalnum() or c in ("-", "_")).strip().upper()
        worker_name = " ".join(worker_name.split())
        
        if not worker_name or not worker_id:
            raise HTTPException(status_code=400, detail="Vui lòng nhập Mã và Họ tên công nhân hợp lệ!")
            
        safe_fname = "".join(c for c in worker_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        clean_filename = f"{worker_id}_{safe_fname}.jpg"
        save_path = os.path.join(FACES_DIR, clean_filename)
        
        # Xử lý ảnh tải lên hoặc ảnh chụp
        if photo and photo.filename:
            content = await photo.read()
            img = Image.open(io.BytesIO(content))
        elif captured_data and "base64," in captured_data:
            import base64
            b64_str = captured_data.split("base64,")[1]
            img_bytes = base64.b64decode(b64_str)
            img = Image.open(io.BytesIO(img_bytes))
        else:
            raise HTTPException(status_code=400, detail="Vui lòng tải ảnh hoặc chụp ảnh chân dung!")
            
        img = ImageOps.exif_transpose(img)
        img.convert("RGB").save(save_path)
        
        # Lưu vào SQL Server
        try:
            conn = database.get_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaCN = ?", (worker_id,))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO CONGNHAN (MaCN, HoTen) VALUES (?, ?)", (worker_id, worker_name))
                    conn.commit()
                conn.close()
        except Exception as sql_err:
            print(f"[SQL INSERT WORKER ERROR]: {sql_err}")
            
        # Cập nhật lại bộ nhớ khuôn mặt
        camera_mgr.reload_known_faces()
        
        return {"success": True, "message": f"Đã đăng ký thành công công nhân [{worker_id}] {worker_name}!"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/workers/{filename}")
async def delete_worker(filename: str):
    fpath = os.path.join(FACES_DIR, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
        camera_mgr.reload_known_faces()
        return {"success": True, "message": "Đã xóa hồ sơ thành công"}
    raise HTTPException(status_code=404, detail="Không tìm thấy file")

@app.get("/api/export-excel")
async def export_excel():
    excel_bytes = database.get_dynamic_excel_bytes()
    filename = f"Smart_HSE_BaoCao_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/shift/config")
async def get_shift_config():
    return database.load_shift_config()

@app.post("/api/shift/config")
async def save_shift_config(cfg: Dict[str, Any]):
    success = database.save_shift_config(cfg)
    return {"success": success, "message": "Đã lưu cài đặt ca làm việc thành công!"}

# ------------------------------------------------------------------------------
# 5. ENTRYPOINT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import webbrowser
    import socket
    import subprocess

    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "localhost"

    def get_tailscale_ip():
        """Tự động phát hiện địa chỉ IP cố định của mạng ảo Tailscale (100.x.y.z)."""
        try:
            # Thử qua CLI tailscale ip -4
            res = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=1)
            ip = res.stdout.strip()
            if ip and ip.startswith("100."):
                return ip
        except Exception:
            pass

        try:
            # Thử tìm qua danh sách card mạng Windows
            s = socket.getaddrinfo(socket.gethostname(), None)
            for item in s:
                ip = item[4][0]
                if ip.startswith("100."):
                    return ip
        except Exception:
            pass
        return None

    local_ip = get_local_ip()
    tailscale_ip = get_tailscale_ip()

    print("=" * 72)
    print("  SMART HSE VISION - MAY CHU AN TOAN & DIEM DANH AI DANG CHAY")
    print(f"  -> Truy cap cuc bo tren Laptop/PC:     http://localhost:8501")
    print(f"  -> Truy cap trong mang Wi-Fi noi bo:   http://{local_ip}:8501")
    if tailscale_ip:
        print(f"  -> KET NOI TAILSCALE VPN (4G/INTERNET): http://{tailscale_ip}:8501")
        print("     [OK] Bao mat dau-cuoi WireGuard, co the xem moi luc moi noi!")
    else:
        print("  -> TAILSCALE VPN: Chua bat hoac chua cai dat tren may tinh nay.")
        print("     (Cai dat Tailscale de co IP 100.x.y.z co dinh truy cap tu xa an toan)")
    print("=" * 72)
    
    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://localhost:8501")
        
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")
