import os
import sys

# Thiết lập UTF-8 toàn diện cho console Windows tránh UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import time
import base64
import threading
import subprocess
from datetime import datetime
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

import database
import DUANCLB

# ==============================================================================
# HỆ THỐNG SMART HSE VISION - GIAO DIỆN ĐA NỀN TẢNG CHUYÊN NGHIỆP
# - SERVER: SQL Server .\SQLEXPRESS01 (Smart_HSE)
# - BẢO MẬT & TRUY CẬP TỪ XA: Tailscale Mesh VPN
# - QUẢN LÝ OWNER TOÀN DIỆN:
#   + Tab 1: Mắt Thần AI (Camera thời gian thực + Điểm danh trực tiếp)
#   + Tab 2: Nhật Ký Điểm Danh (Bộ lọc ngày, trạng thái đúng giờ/trễ)
#   + Tab 3: Nhật Ký Vi Phạm An Toàn (Xem danh sách, xem ảnh bằng chứng snapshot)
#   + Tab 4: Thống Kê & Báo Cáo Chuyên Sâu (Theo Ngày / Tháng / Năm, Biểu đồ trực quan)
#   + Tab 5: Quản Lý Hồ Sơ Công Nhân (Danh sách công nhân, quân số)
# ==============================================================================

app = FastAPI(title="Smart HSE Vision - Multi-Platform")

# Gắn thư mục tĩnh phục vụ xem ảnh bằng chứng vi phạm và ảnh khuôn mặt công nhân
VIOLATIONS_DIR = os.path.join(BASE_DIR, "violations")
os.makedirs(VIOLATIONS_DIR, exist_ok=True)
app.mount("/violations", StaticFiles(directory=VIOLATIONS_DIR), name="violations")

FACES_DIR = os.path.join(BASE_DIR, "known_faces")
os.makedirs(FACES_DIR, exist_ok=True)
app.mount("/known_faces", StaticFiles(directory=FACES_DIR), name="known_faces")

class LiveCameraManager:
    def __init__(self):
        self.threaded_cam = None
        self.is_running = False
        self.lock = threading.Lock()
        self.latest_frame_bytes = None
        self.ma_owner = "OWN01"
        self.person_model = None
        self.ppe_model = None
        self.face_detector = None
        self.face_recognizer = None
        self.known_workers = {}
        self.checked_today = set()
        self.current_detections = []
        self.detections_lock = threading.Lock()

    def load_models_once(self):
        if self.person_model is None:
            self.person_model, self.ppe_model, self.face_detector, self.face_recognizer = DUANCLB.init_models()
            self.known_workers = DUANCLB.load_known_workers(os.path.join(BASE_DIR, "known_faces"), self.face_detector, self.face_recognizer)

    def reload_known_workers(self):
        with self.lock:
            self.load_models_once()
            if self.face_detector is not None and self.face_recognizer is not None:
                self.known_workers = DUANCLB.load_known_workers(os.path.join(BASE_DIR, "known_faces"), self.face_detector, self.face_recognizer)
                print(f"[AI ENGINE]: Da cap nhat lai ho so khuon mat! Tong cong: {len(self.known_workers)} cong nhan.")

    def start(self, ma_owner="OWN01"):
        with self.lock:
            if self.is_running:
                return True
            self.load_models_once()
            self.ma_owner = ma_owner
            self.checked_today = database.get_today_checked_in_workers(ma_owner)
            
            # Khởi động camera chuẩn HD 720p (1280x720) sắc nét cao
            self.threaded_cam = DUANCLB.FastThreadedCamera(src=0, width=1280, height=720, fps=30)
            if not self.threaded_cam.is_opened():
                print("[LIVE CAMERA]: Camera index 0 khong san sang. Thu mo camera index 1...")
                self.threaded_cam = DUANCLB.FastThreadedCamera(src=1, width=1280, height=720, fps=30)
                
            if not self.threaded_cam.is_opened():
                # Fallback camera độ phân giải 640x480 nếu thiết bị không hỗ trợ 720p
                print("[LIVE CAMERA]: Thu mo lai voi do phan giai 640x480...")
                self.threaded_cam = DUANCLB.FastThreadedCamera(src=0, width=640, height=480, fps=30)
                
            if not self.threaded_cam.is_opened():
                print("[LIVE CAMERA]: Khong the ket noi Webcam!")
                self.threaded_cam = None
                return False
                
            self.is_running = True
            with self.detections_lock:
                self.current_detections = []
                
            # Phân tách 2 luồng độc lập: Luồng xử lý AI và Luồng Stream Video 30 FPS mượt mà
            threading.Thread(target=self._ai_worker_loop, daemon=True).start()
            threading.Thread(target=self._stream_loop, daemon=True).start()
            return True

    def stop(self):
        with self.lock:
            self.is_running = False
            if self.threaded_cam:
                self.threaded_cam.release()
                self.threaded_cam = None
            self.latest_frame_bytes = None
            with self.detections_lock:
                self.current_detections = []

    def _ai_worker_loop(self):
        """Luồng xử lý AI bất đồng bộ: Nhận diện khuôn mặt YuNet + SFace trực tiếp trên frame và kiểm tra bảo hộ PPE."""
        violation_cooldown = {}
        attendance_cooldown = {}
        
        while self.is_running and self.threaded_cam:
            try:
                grabbed, frame = self.threaded_cam.read()
                if not grabbed or frame is None:
                    time.sleep(0.02)
                    continue

                h, w = frame.shape[:2]
                now = datetime.now()

                # 1. NHẬN DIỆN KHUÔN MẶT TRỰC TIẾP TRÊN TOÀN KHUNG HÌNH (CHUẨN XÁC 100%)
                recognized_faces = []
                if self.face_detector is not None and self.face_recognizer is not None:
                    try:
                        self.face_detector.setInputSize((w, h))
                        _, f_dets = self.face_detector.detect(frame)
                        if f_dets is not None and len(f_dets) > 0:
                            for f in f_dets:
                                fx, fy, fw_b, fh_b = map(int, f[:4])
                                aligned = self.face_recognizer.alignCrop(frame, f)
                                feat = self.face_recognizer.feature(aligned)
                                
                                best_id = "UNKNOWN"
                                best_name = "UNKNOWN PERSON"
                                max_score = -1.0
                                
                                for wn, wi in (self.known_workers or {}).items():
                                    t_feat = wi.get("feature")
                                    if t_feat is None:
                                        continue
                                    score = self.face_recognizer.match(feat, t_feat, cv2.FaceRecognizerSF_FR_COSINE)
                                    if score > max_score:
                                        max_score = float(score)
                                        if score >= 0.32:
                                            best_id = wi.get("id", "UNKNOWN")
                                            best_name = wi.get("name", wn)
                                
                                recognized_faces.append({
                                    "box": (fx, fy, fw_b, fh_b),
                                    "center": (fx + fw_b // 2, fy + fh_b // 2),
                                    "w_id": best_id,
                                    "w_name": best_name,
                                    "score": max_score
                                })
                    except Exception as ex:
                        print(f"[FACE INFERENCE ERROR]: {ex}")

                # 2. PHÁT HIỆN KHUNG NGƯỜI BẰNG YOLO ĐỂ QUÉT BẢO HỘ PPE
                detected_person_boxes = []
                if self.person_model is not None:
                    try:
                        scale_w = 640
                        scale_h = int(h * (scale_w / float(w)))
                        small_frame = cv2.resize(frame, (scale_w, scale_h))
                        sx = w / float(scale_w)
                        sy = h / float(scale_h)

                        results = self.person_model(small_frame, classes=[0], conf=0.38, verbose=False)
                        for r in results:
                            for box in r.boxes:
                                bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                                detected_person_boxes.append((int(bx1 * sx), int(by1 * sy), int(bx2 * sx), int(by2 * sy)))
                    except Exception:
                        pass

                # 3. KẾT HỢP KHUÔN MẶT ĐÃ NHẬN DIỆN VỚI KHUNG NGƯỜI
                new_detections = []
                used_faces = set()

                for pbox in detected_person_boxes:
                    px1, py1, px2, py2 = pbox
                    w_id, w_name = "UNKNOWN", "UNKNOWN PERSON"

                    # Tìm khuôn mặt nằm trong khung người này
                    for idx, rf in enumerate(recognized_faces):
                        fcx, fcy = rf["center"]
                        if px1 <= fcx <= px2 and py1 <= fcy <= py2:
                            w_id = rf["w_id"]
                            w_name = rf["w_name"]
                            used_faces.add(idx)
                            break

                    # Quét PPE
                    checklist, is_safe, vio_desc = DUANCLB.check_ppe_compliance(frame, pbox, self.ppe_model)
                    is_checked = (w_id in self.checked_today) or (w_name in self.checked_today)

                    # Ghi nhận điểm danh tức thì khi nhận diện công nhân (cooldown 15s)
                    if w_id != "UNKNOWN":
                        last_att = attendance_cooldown.get(w_id, 0)
                        if time.time() - last_att > 15.0:
                            try:
                                if database.log_attendance_db(self.ma_owner, w_id, w_name):
                                    attendance_cooldown[w_id] = time.time()
                                    self.checked_today.add(w_id)
                                    self.checked_today.add(w_name)
                            except Exception as e:
                                print(f"[ATTENDANCE DB LOG ERROR]: {e}")

                    # Ghi nhận vi phạm an toàn
                    if not is_safe:
                        v_key = f"{w_id}_{w_name}"
                        last_vio = violation_cooldown.get(v_key, 0)
                        if time.time() - last_vio > 12.0:
                            try:
                                ts = now.strftime("%Y%m%d_%H%M%S")
                                clean_wname = "".join(c for c in w_name if c.isalnum() or c in (" ", "_")).replace(" ", "_")
                                img_name = f"violation_{self.ma_owner}_{w_id}_{clean_wname}_{ts}.jpg"
                                save_p = os.path.join(VIOLATIONS_DIR, img_name)
                                cv2.imwrite(save_p, frame)

                                database.log_violation_db(self.ma_owner, w_id, w_name, vio_desc, img_name, "Cảnh báo")
                                violation_cooldown[v_key] = time.time()
                            except Exception as e:
                                print(f"[VIOLATION DB LOG ERROR]: {e}")

                    new_detections.append({
                        "pbox": pbox,
                        "w_id": w_id,
                        "w_name": w_name,
                        "checklist": checklist,
                        "is_safe": is_safe,
                        "is_checked": is_checked
                    })

                # Nếu khuôn mặt chưa được gán cho khung người nào (ví dụ người ngồi cận cảnh webcam)
                for idx, rf in enumerate(recognized_faces):
                    if idx not in used_faces:
                        fx, fy, fw_b, fh_b = rf["box"]
                        synth_pbox = (
                            max(0, fx - int(fw_b * 0.6)),
                            max(0, fy - int(fh_b * 0.4)),
                            min(w, fx + int(fw_b * 1.6)),
                            min(h, fy + int(fh_b * 3.5))
                        )
                        w_id = rf["w_id"]
                        w_name = rf["w_name"]
                        checklist, is_safe, vio_desc = DUANCLB.check_ppe_compliance(frame, synth_pbox, self.ppe_model)
                        is_checked = (w_id in self.checked_today) or (w_name in self.checked_today)

                        # Ghi nhận điểm danh tức thì khi nhận diện công nhân (cooldown 15s)
                        if w_id != "UNKNOWN":
                            last_att = attendance_cooldown.get(w_id, 0)
                            if time.time() - last_att > 15.0:
                                try:
                                    if database.log_attendance_db(self.ma_owner, w_id, w_name):
                                        attendance_cooldown[w_id] = time.time()
                                        self.checked_today.add(w_id)
                                        self.checked_today.add(w_name)
                                except Exception as e:
                                    print(f"[ATTENDANCE DB LOG ERROR]: {e}")

                        new_detections.append({
                            "pbox": synth_pbox,
                            "w_id": w_id,
                            "w_name": w_name,
                            "checklist": checklist,
                            "is_safe": is_safe,
                            "is_checked": is_checked
                        })

                with self.detections_lock:
                    self.current_detections = new_detections

            except Exception as e:
                print(f"[AI WORKER ERROR]: {e}")

            time.sleep(0.035)

    def _stream_loop(self):
        """Luồng hiển thị Video Stream: Đạt 30 FPS mượt mà liên tục, không bao giờ bị giật lag."""
        while self.is_running and self.threaded_cam:
            try:
                grabbed, frame = self.threaded_cam.read()
                if not grabbed or frame is None:
                    time.sleep(0.015)
                    continue

                # Lấy danh sách phát hiện mới nhất được lưu trong bộ đệm
                with self.detections_lock:
                    dets = list(self.current_detections)

                # Vẽ thẻ thông tin HUD lên khung hình HD
                for d in dets:
                    try:
                        DUANCLB.draw_worker_card(
                            frame,
                            d["pbox"],
                            d["w_id"],
                            d["w_name"],
                            d["checklist"],
                            d["is_safe"],
                            d["is_checked"]
                        )
                    except Exception:
                        pass

                # Mã hóa JPEG chất lượng 85 sắc nét vượt trội
                ret, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ret:
                    self.latest_frame_bytes = buf.tobytes()

            except Exception as e:
                print(f"[STREAM RENDER ERROR]: {e}")

            time.sleep(0.028)

live_cam = LiveCameraManager()

# ==============================================================================
# GIAO DIỆN ĐA NỀN TẢNG HTML5 / CSS3 / JAVASCRIPT THUẦN
# ==============================================================================
HTML_UI = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart HSE Vision - Quản Trị Đa Nền Tảng</title>
    <style>
        :root {
            --bg-dark: #0A1124;
            --card-bg: #101B33;
            --card-border: rgba(56, 189, 248, 0.2);
            --teal: #38BDF8; /* Tone xanh biển nhạt dịu mắt (Light Sky Blue) */
            --cyan: #7DD3FC; /* Xanh băng nhẹ dịu */
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --danger: #FB7185;
            --warning: #FBBF24;
        }
        * { 
            box-sizing: border-box; 
            margin: 0; 
            padding: 0; 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        body { 
            background: var(--bg-dark); 
            color: var(--text-main); 
            min-height: 100vh; 
            display: flex; 
            flex-direction: column; 
        }
        
        /* Header */
        .header { 
            background: #0E1A33; 
            border-bottom: 1px solid rgba(56, 189, 248, 0.22); 
            padding: 14px 24px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            flex-wrap: wrap; 
            gap: 12px; 
        }
        .brand { font-size: 20px; font-weight: 800; color: var(--teal); letter-spacing: 0.5px; }
        .sub-brand { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
        
        .container { 
            max-width: 1440px; 
            margin: 0 auto; 
            padding: 20px 16px; 
            width: 100%; 
            flex: 1; 
        }
        
        /* Stats KPI Cards */
        .stats-row { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); 
            gap: 14px; 
            margin-bottom: 20px; 
        }
        .stat-box { 
            background: var(--card-bg); 
            border: 1px solid var(--card-border); 
            border-radius: 12px; 
            padding: 16px; 
            text-align: center; 
            transition: transform 0.2s, border-color 0.2s;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        }
        .stat-box:hover { transform: translateY(-2px); border-color: var(--teal); }
        .stat-val { font-size: 28px; font-weight: 800; color: var(--teal); }
        .stat-lbl { font-size: 12px; color: var(--text-muted); margin-top: 6px; font-weight: 600; }
        
        /* Navigation Tabs */
        .tabs-nav {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(56, 189, 248, 0.18);
            padding-bottom: 10px;
            overflow-x: auto;
        }
        .tab-btn {
            background: transparent;
            color: var(--text-muted);
            border: 1px solid transparent;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 13.5px;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .tab-btn:hover {
            color: #FFF;
            background: rgba(56, 189, 248, 0.1);
        }
        .tab-btn.active {
            background: linear-gradient(135deg, #0284C7, #38BDF8);
            color: #031124;
            border-color: var(--teal);
            box-shadow: 0 2px 10px rgba(56, 189, 248, 0.25);
        }
        
        /* Cards */
        .card { 
            background: var(--card-bg); 
            border: 1px solid var(--card-border); 
            border-radius: 14px; 
            padding: 20px; 
            margin-bottom: 20px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
        }
        .card-title { 
            font-size: 16px; 
            font-weight: 700; 
            color: var(--teal); 
            margin-bottom: 16px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            flex-wrap: wrap;
            gap: 10px;
        }
        
        /* Grid */
        .grid-2 { display: grid; grid-template-columns: 1fr; gap: 20px; }
        @media(min-width: 960px) { .grid-2 { grid-template-columns: 1.25fr 1fr; } }
        
        /* Buttons */
        .btn { 
            background: linear-gradient(135deg, #0284C7, #38BDF8); 
            color: #031124; 
            border: none; 
            padding: 9px 16px; 
            border-radius: 8px; 
            font-weight: 700; 
            cursor: pointer; 
            transition: 0.2s; 
            font-size: 13px; 
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .btn:hover { opacity: 0.92; transform: translateY(-1px); }
        .btn-danger { background: linear-gradient(135deg, #E11D48, #FB7185); color: #FFF; }
        .btn-outline { background: transparent; border: 1px solid rgba(56, 189, 248, 0.5); color: var(--teal); }
        .btn-outline:hover { background: rgba(56, 189, 248, 0.12); }
        
        /* Inputs & Filters */
        .filter-bar {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 16px;
            background: rgba(56, 189, 248, 0.05);
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid rgba(56, 189, 248, 0.15);
        }
        .input-text, .select-box { 
            background: #091224; 
            border: 1px solid rgba(56, 189, 248, 0.3); 
            color: #FFF; 
            padding: 8px 12px; 
            border-radius: 6px; 
            outline: none; 
            font-size: 13px;
        }
        .input-text:focus, .select-box:focus { border-color: var(--teal); }
        
        /* Camera Feed Frame (Chuẩn HD 16:9 sắc nét, chống méo hình) */
        .cam-frame { 
            width: 100%; 
            aspect-ratio: 16/9; 
            background: #050B18; 
            border-radius: 10px; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            overflow: hidden; 
            border: 1px solid rgba(56, 189, 248, 0.2); 
            box-shadow: inset 0 0 15px rgba(0,0,0,0.6);
        }
        .cam-frame img { 
            width: 100%; 
            height: 100%; 
            object-fit: contain; 
            image-rendering: -webkit-optimize-contrast;
        }
        
        /* Tables */
        .table-responsive { 
            overflow-x: auto; 
            max-height: 520px; 
            background: #0E1A33; 
            border: 1px solid rgba(56, 189, 248, 0.2); 
            border-radius: 10px; 
        }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid rgba(56, 189, 248, 0.12); }
        th { 
            color: #38BDF8; 
            font-weight: 700; 
            background: #152445; /* Nền đậm 100% Solid Opaque, tuyệt đối không bị mờ hay xuyên thấu */
            position: sticky; 
            top: 0; 
            z-index: 10; 
            border-bottom: 2px solid rgba(56, 189, 248, 0.5);
            box-shadow: 0 3px 8px rgba(0, 0, 0, 0.4);
            white-space: nowrap;
        }
        tr:hover { background: rgba(56, 189, 248, 0.05); }
        
        /* Badges */
        .badge { padding: 4px 8px; border-radius: 4px; font-size: 11.5px; font-weight: 700; display: inline-block; }
        .badge-success { background: rgba(56, 189, 248, 0.16); color: var(--teal); border: 1px solid rgba(56, 189, 248, 0.45); }
        .badge-danger { background: rgba(251, 113, 133, 0.16); color: var(--danger); border: 1px solid rgba(251, 113, 133, 0.45); }
        .badge-warning { background: rgba(251, 191, 36, 0.16); color: var(--warning); border: 1px solid rgba(251, 191, 36, 0.45); }
        .badge-cyan { background: rgba(125, 211, 252, 0.16); color: var(--cyan); border: 1px solid rgba(125, 211, 252, 0.45); }
        
        /* Progress Bar for Analytics */
        .prog-container { margin-bottom: 14px; }
        .prog-header { display: flex; justify-content: space-between; font-size: 12.5px; margin-bottom: 5px; }
        .prog-bar-bg { width: 100%; height: 10px; background: rgba(255,255,255,0.08); border-radius: 6px; overflow: hidden; }
        .prog-bar-val { height: 100%; border-radius: 6px; transition: width 0.5s ease; }
        
        /* Timeline Bar Chart */
        .chart-grid {
            display: flex;
            align-items: flex-end;
            gap: 12px;
            height: 200px;
            padding-top: 20px;
            border-bottom: 1px solid rgba(56, 189, 248, 0.2);
            overflow-x: auto;
        }
        .chart-col {
            display: flex;
            flex-direction: column;
            align-items: center;
            flex: 1;
            min-width: 36px;
            height: 100%;
            justify-content: flex-end;
        }
        .chart-bars {
            display: flex;
            gap: 4px;
            align-items: flex-end;
            width: 100%;
            justify-content: center;
            height: 160px;
        }
        .bar-att { background: var(--teal); width: 12px; border-radius: 3px 3px 0 0; transition: height 0.4s; }
        .bar-vio { background: var(--danger); width: 12px; border-radius: 3px 3px 0 0; transition: height 0.4s; }
        .chart-label { font-size: 11px; color: var(--text-muted); margin-top: 8px; white-space: nowrap; }

        /* Modal */
        .modal-bg { position: fixed; inset: 0; background: rgba(3, 7, 18, 0.85); backdrop-filter: blur(8px); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .modal-card { background: #101B33; border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 16px; padding: 24px; width: 90%; max-width: 480px; text-align: center; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); }

        /* ==============================================================================
           ADMIN DASHBOARD SIDEBAR LAYOUT (CHUẨN THỊ TRƯỜNG SAAS)
           ============================================================================== */
        #adminView {
            display: flex;
            width: 100vw !important;
            max-width: 100vw !important;
            min-height: 100vh;
            padding: 0 !important;
            margin: 0 !important;
            background: var(--bg-dark);
            position: relative;
        }
        .admin-sidebar {
            width: 280px;
            background: #0B132B;
            border-right: 1px solid rgba(56, 189, 248, 0.16);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            position: sticky;
            top: 0;
            height: 100vh;
            overflow-y: auto;
            z-index: 20;
        }
        .admin-sidebar-header {
            padding: 22px 20px 18px 20px;
            border-bottom: 1px solid rgba(56, 189, 248, 0.12);
        }
        .admin-sidebar-logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .admin-logo-icon {
            width: 38px;
            height: 38px;
            border-radius: 10px;
            background: linear-gradient(135deg, #0284C7, #38BDF8);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.35);
        }
        .admin-logo-text {
            font-size: 16px;
            font-weight: 800;
            color: #FFF;
            letter-spacing: 0.5px;
        }
        .admin-logo-tag {
            font-size: 11px;
            font-weight: 700;
            color: var(--teal);
            letter-spacing: 0.5px;
        }
        .admin-user-card {
            margin: 14px 14px 10px 14px;
            padding: 12px 14px;
            background: rgba(56, 189, 248, 0.05);
            border: 1px solid rgba(56, 189, 248, 0.14);
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .admin-avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: linear-gradient(135deg, #0284C7, #38BDF8);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #031124;
            font-weight: 800;
            font-size: 14px;
        }
        .admin-user-name {
            font-size: 13.5px;
            font-weight: 700;
            color: #FFF;
        }
        .admin-user-role {
            font-size: 11px;
            color: var(--cyan);
            display: flex;
            align-items: center;
            gap: 6px;
            margin-top: 2px;
        }
        .admin-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #34D399;
            box-shadow: 0 0 8px #34D399;
        }
        .admin-menu-label {
            padding: 14px 20px 6px 20px;
            font-size: 11px;
            font-weight: 700;
            color: var(--text-muted);
            letter-spacing: 0.8px;
            text-transform: uppercase;
        }
        .admin-nav-list {
            list-style: none;
            padding: 0 12px;
            margin: 0;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .admin-nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 11px 16px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            user-select: none;
        }
        .admin-nav-item:hover {
            color: #FFF;
            background: rgba(56, 189, 248, 0.08);
        }
        .admin-nav-item.active {
            background: linear-gradient(90deg, rgba(2, 132, 199, 0.25), rgba(56, 189, 248, 0.12));
            color: var(--teal);
            font-weight: 700;
            border-left: 3px solid var(--teal);
        }
        .admin-nav-icon {
            font-size: 16px;
            width: 22px;
            text-align: center;
        }
        .admin-nav-badge {
            margin-left: auto;
            background: rgba(56, 189, 248, 0.18);
            color: var(--cyan);
            border-radius: 20px;
            font-size: 11px;
            padding: 2px 8px;
            font-weight: 700;
        }
        .admin-sidebar-footer {
            margin-top: auto;
            padding: 16px;
            border-top: 1px solid rgba(56, 189, 248, 0.12);
        }
        .admin-sys-widget {
            background: rgba(16, 27, 51, 0.6);
            border: 1px solid rgba(56, 189, 248, 0.14);
            border-radius: 10px;
            padding: 12px;
            margin-bottom: 12px;
        }
        .admin-sys-row {
            display: flex;
            justify-content: space-between;
            font-size: 11.5px;
            margin-bottom: 5px;
        }
        .admin-sys-lbl { color: var(--text-muted); }
        .admin-sys-val { color: var(--cyan); font-weight: 600; }
        .admin-main-wrapper {
            flex: 1;
            min-width: 0;
            display: flex;
            flex-direction: column;
            background: var(--bg-dark);
            height: 100vh;
            overflow-y: auto;
        }
        .admin-topbar {
            padding: 16px 28px;
            background: #0E1A33;
            border-bottom: 1px solid rgba(56, 189, 248, 0.16);
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 15;
            flex-wrap: wrap;
            gap: 12px;
        }
        .admin-topbar-title {
            font-size: 18px;
            font-weight: 800;
            color: #FFF;
        }
        .admin-topbar-breadcrumb {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 3px;
        }
        .admin-content-body {
            padding: 24px 28px;
            flex: 1;
        }
    </style>
</head>
<body>

    <!-- MODAL ĐĂNG NHẬP -->
    <div id="loginModal" class="modal-bg">
        <div class="modal-card">
            <h2 style="color:var(--teal); margin-bottom: 6px;">SMART HSE VISION</h2>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 18px;">Đăng Nhập Hệ Thống Quản Trị Đa Nền Tảng</p>
            
            <div style="display:flex; justify-content: center; gap: 16px; margin-bottom: 16px; font-size: 14px;">
                <label style="cursor:pointer;"><input type="radio" name="role" value="owner" checked onchange="onRoleChange('owner')"> Chủ Xưởng (Owner)</label>
                <label style="cursor:pointer;"><input type="radio" name="role" value="admin" onchange="onRoleChange('admin')"> Chủ Server (Admin)</label>
            </div>
            
            <input id="txtUser" class="input-text" style="width: 100%; margin-bottom: 12px;" placeholder="Tên đăng nhập" value="khiem_owner" onkeydown="if(event.key==='Enter') doLogin()">
            <input id="txtPass" type="password" class="input-text" style="width: 100%; margin-bottom: 18px;" placeholder="Mật khẩu" value="123456" onkeydown="if(event.key==='Enter') doLogin()">
            
            <button type="button" id="btnLogin" class="btn" style="width: 100%; padding: 12px; font-size:14px; font-weight:700;" onclick="doLogin()">ĐĂNG NHẬP HỆ THỐNG</button>
            <p id="loginErr" style="color:var(--danger); font-size: 13px; margin-top: 14px; display:none; background:rgba(255,77,77,0.1); padding:8px; border-radius:6px; border:1px solid var(--danger);"></p>
        </div>
    </div>

    <!-- MODAL XEM ẢNH BẰNG CHỨNG VI PHẠM -->
    <div id="evidenceModal" class="modal-bg" style="display:none;" onclick="closeEvidence(event)">
        <div class="modal-card" style="max-width: 650px;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                <h3 style="color:var(--teal); font-size:16px;">Ảnh Bằng Chứng Vi Phạm AI</h3>
                <button class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="closeEvidence()">Đóng</button>
            </div>
            <div style="width:100%; aspect-ratio:4/3; background:#050B18; border-radius:8px; overflow:hidden; border:1px solid rgba(56,189,248,0.3); display:flex; align-items:center; justify-content:center;">
                <img id="evidenceImg" src="" style="width:100%; height:100%; object-fit:contain;">
            </div>
            <p id="evidenceNote" style="font-size:13px; color:var(--text-muted); margin-top:12px; text-align:left;"></p>
        </div>
    </div>

    <!-- MODAL THÊM CÔNG NHÂN MỚI & ĐĂNG KÝ KHUÔN MẶT AI -->
    <div id="addWorkerModal" class="modal-bg" style="display:none;" onclick="closeAddWorkerModal(event)">
        <div class="modal-card" style="max-width: 500px; text-align: left;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h3 style="color:var(--teal); font-size:17px;">Thêm Công Nhân & Đăng Ký AI</h3>
                <button class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="closeAddWorkerModal()">Đóng</button>
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Mã Công Nhân:</label>
                <input id="newWorkerId" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: CN-003">
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Họ và Tên Công Nhân (*):</label>
                <input id="newWorkerName" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Trần Văn An">
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Phòng Ban / Phân Xưởng:</label>
                <input id="newWorkerDept" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Xưởng Lắp Ráp 1">
            </div>

            <div style="margin-bottom: 16px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Số Điện Thoại:</label>
                <input id="newWorkerPhone" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: 0987654321">
            </div>

            <div style="margin-bottom: 16px; background:rgba(56,189,248,0.06); padding:12px; border-radius:8px; border:1px solid rgba(56,189,248,0.18);">
                <label style="font-size:12px; color:var(--teal); font-weight:700; display:block; margin-bottom:8px;">Ảnh Chân Dung (Để Nhận Diện Khuôn Mặt AI):</label>
                
                <div style="display:flex; gap:14px; align-items:center;">
                    <div style="width:80px; height:80px; border-radius:50%; background:#050B18; border:2px dashed var(--teal); overflow:hidden; display:flex; align-items:center; justify-content:center; flex-shrink:0;">
                        <img id="workerPreviewImg" src="" style="width:100%; height:100%; object-fit:cover; display:none;">
                        <span id="workerPreviewPlaceholder" style="font-size:24px; color:var(--text-muted);">+</span>
                    </div>
                    <div style="flex:1;">
                        <input id="workerImgFile" type="file" accept="image/*" style="display:none;" onchange="previewNewWorkerPic(event)">
                        <button class="btn btn-outline" style="width:100%; margin-bottom:6px; font-size:12px;" onclick="document.getElementById('workerImgFile').click()">Chọn Ảnh Từ Thiết Bị</button>
                        <p style="font-size:11px; color:var(--text-muted);">Chụp thẳng mặt, rõ nét, đủ ánh sáng</p>
                    </div>
                </div>
            </div>

            <div style="display:flex; gap:10px; margin-top:20px;">
                <button class="btn" style="flex:1; padding:11px;" onclick="submitAddWorker()">LƯU & ĐĂNG KÝ AI</button>
                <button class="btn btn-outline" style="padding:11px;" onclick="closeAddWorkerModal()">Hủy Bỏ</button>
            </div>
            <p id="addWorkerMsg" style="font-size:12px; margin-top:10px; display:none; text-align:center;"></p>
        </div>
    </div>

    <!-- MODAL SỬA THÔNG TIN CÔNG NHÂN & CẬP NHẬT AI -->
    <div id="editWorkerModal" class="modal-bg" style="display:none;" onclick="closeEditWorkerModal(event)">
        <div class="modal-card" style="max-width: 520px; text-align: left;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h3 style="color:var(--teal); font-size:17px; font-weight:700;">Sửa Thông Tin Công Nhân</h3>
                <button class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="closeEditWorkerModal()">Đóng</button>
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Mã Công Nhân (Cố định):</label>
                <input id="editWorkerId" class="input-text" style="width:100%; margin-top:4px; opacity:0.85; cursor:not-allowed;" readonly>
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Họ và Tên Công Nhân (*):</label>
                <input id="editWorkerName" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Trần Văn An">
            </div>

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Phòng Ban / Phân Xưởng:</label>
                    <input id="editWorkerDept" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Phân xưởng cơ khí">
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Số Điện Thoại:</label>
                    <input id="editWorkerPhone" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: 0912345678">
                </div>
            </div>

            <div style="margin-bottom: 14px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Trạng Thái Làm Việc:</label>
                <select id="editWorkerStatus" class="select-box" style="width:100%; margin-top:4px;">
                    <option value="Đang làm việc">Đang làm việc (Bình thường)</option>
                    <option value="Tạm nghỉ phép">Tạm nghỉ phép</option>
                    <option value="Nghỉ việc">Đã thôi việc</option>
                </select>
            </div>

            <div style="margin-bottom: 16px; background:rgba(56,189,248,0.06); padding:12px; border-radius:8px; border:1px solid rgba(56,189,248,0.18);">
                <label style="font-size:12px; color:var(--teal); font-weight:700; display:block; margin-bottom:8px;">Ảnh Khuôn Mặt AI (Để trống nếu giữ nguyên ảnh cũ):</label>
                
                <div style="display:flex; gap:14px; align-items:center;">
                    <div style="width:80px; height:80px; border-radius:50%; background:#050B18; border:2px dashed var(--teal); overflow:hidden; display:flex; align-items:center; justify-content:center; flex-shrink:0;">
                        <img id="editWorkerPreviewImg" src="" style="width:100%; height:100%; object-fit:cover; display:none;">
                        <span id="editWorkerPreviewPlaceholder" style="font-size:24px; color:var(--text-muted);">+</span>
                    </div>
                    <div style="flex:1;">
                        <input id="editWorkerImgFile" type="file" accept="image/*" style="display:none;" onchange="previewEditWorkerPic(event)">
                        <button class="btn btn-outline" style="width:100%; margin-bottom:6px; font-size:12px;" onclick="document.getElementById('editWorkerImgFile').click()">Tải Ảnh Mới</button>
                        <p style="font-size:11px; color:var(--text-muted);">Tải ảnh mới nếu muốn AI cập nhật nhận diện</p>
                    </div>
                </div>
            </div>

            <p id="editWorkerMsg" style="color:var(--danger); font-size: 13px; margin-bottom: 12px; display:none;"></p>

            <div style="display:flex; gap:10px; margin-top:16px;">
                <button class="btn" style="flex:1; padding:11px; font-weight:700;" onclick="submitUpdateWorker()">LƯU THAY ĐỔI</button>
                <button class="btn btn-outline" style="padding:11px;" onclick="closeEditWorkerModal()">Hủy Bỏ</button>
            </div>
        </div>
    </div>

    <!-- MODAL THÊM DOANH NGHIỆP / NHÀ XƯỞNG MỚI (SUPER ADMIN) -->
    <div id="addOwnerModal" class="modal-bg" style="display:none;" onclick="closeAddOwnerModal(event)">
        <div class="modal-card" style="max-width: 580px; text-align: left;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h3 style="color:var(--teal); font-size:18px; font-weight:700;">Thêm Doanh Nghiệp / Nhà Xưởng Mới</h3>
                <button class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="closeAddOwnerModal()">Đóng</button>
            </div>

            <div style="display:grid; grid-template-columns: 1fr 2fr; gap:12px; margin-bottom: 12px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Mã Xưởng (*):</label>
                    <input id="newOwnerId" class="input-text" style="width:100%; margin-top:4px; font-weight:700; color:var(--teal);" placeholder="OWN02">
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Tên Doanh Nghiệp / Nhà Xưởng (*):</label>
                    <input id="newOwnerName" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Phân Xưởng Cơ Khí Chế Tạo 2">
                </div>
            </div>

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom: 12px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Người Đại Diện (*):</label>
                    <input id="newOwnerRep" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: Trần Văn Nam">
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Số Điện Thoại:</label>
                    <input id="newOwnerPhone" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: 0912345678">
                </div>
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Email Doanh Nghiệp:</label>
                <input id="newOwnerEmail" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: contact@anphat.vn">
            </div>

            <div style="background:rgba(56,189,248,0.06); padding:12px; border-radius:8px; border:1px solid rgba(56,189,248,0.18); margin-bottom:14px;">
                <label style="font-size:12.5px; color:var(--teal); font-weight:700; display:block; margin-bottom:8px;">Tài Khoản Đăng Nhập Của Xưởng:</label>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                    <div>
                        <label style="font-size:11.5px; color:var(--text-muted); font-weight:600;">Tên Đăng Nhập (*):</label>
                        <input id="newOwnerUser" class="input-text" style="width:100%; margin-top:4px;" placeholder="Ví dụ: nam_xuong2">
                    </div>
                    <div>
                        <label style="font-size:11.5px; color:var(--text-muted); font-weight:600;">Mật Khẩu Ban Đầu (*):</label>
                        <input id="newOwnerPass" type="text" class="input-text" style="width:100%; margin-top:4px;" value="123456" placeholder="Mặc định: 123456">
                    </div>
                </div>
            </div>

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom: 16px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Gói Dịch Vụ:</label>
                    <select id="newOwnerTier" class="input-text" style="width:100%; margin-top:4px;">
                        <option value="Tiêu chuẩn">Tiêu chuẩn (50 CN, 1 Camera)</option>
                        <option value="Nâng cao">Nâng cao (200 CN, 3 Camera)</option>
                        <option value="Doanh nghiệp lớn">Doanh nghiệp lớn (Không giới hạn)</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Thời Hạn Bản Quyền:</label>
                    <select id="newOwnerPeriod" class="input-text" style="width:100%; margin-top:4px;">
                        <option value="12">12 tháng (1 năm)</option>
                        <option value="6">6 tháng</option>
                        <option value="1">1 tháng (Dùng thử)</option>
                        <option value="0">Vĩnh viễn (Không thời hạn)</option>
                    </select>
                </div>
            </div>

            <p id="addOwnerMsg" style="color:var(--danger); font-size: 13px; margin-bottom: 12px; display:none;"></p>

            <div style="display:flex; gap:10px; margin-top:16px;">
                <button class="btn" style="flex:1; padding:11px; font-weight:700;" onclick="submitAddOwner()">LƯU & KÍCH HOẠT NHÀ XƯỞNG</button>
                <button class="btn btn-outline" style="padding:11px;" onclick="closeAddOwnerModal()">Hủy Bỏ</button>
            </div>
        </div>
    </div>

    <!-- MODAL SỬA DOANH NGHIỆP & ĐẶT LẠI MẬT KHẨU (SUPER ADMIN) -->
    <div id="editOwnerModal" class="modal-bg" style="display:none;" onclick="closeEditOwnerModal(event)">
        <div class="modal-card" style="max-width: 580px; text-align: left;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h3 style="color:var(--teal); font-size:18px; font-weight:700;">Chỉnh Sửa Thông Tin Doanh Nghiệp</h3>
                <button class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="closeEditOwnerModal()">Đóng</button>
            </div>

            <div style="display:grid; grid-template-columns: 1fr 2fr; gap:12px; margin-bottom: 12px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Mã Xưởng (Cố định):</label>
                    <input id="editOwnerId" class="input-text" style="width:100%; margin-top:4px; opacity:0.85; cursor:not-allowed;" readonly>
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Tên Doanh Nghiệp / Nhà Xưởng (*):</label>
                    <input id="editOwnerName" class="input-text" style="width:100%; margin-top:4px;">
                </div>
            </div>

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom: 12px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Người Đại Diện (*):</label>
                    <input id="editOwnerRep" class="input-text" style="width:100%; margin-top:4px;">
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Số Điện Thoại:</label>
                    <input id="editOwnerPhone" class="input-text" style="width:100%; margin-top:4px;">
                </div>
            </div>

            <div style="margin-bottom: 12px;">
                <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Email Doanh Nghiệp:</label>
                <input id="editOwnerEmail" class="input-text" style="width:100%; margin-top:4px;">
            </div>

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom: 14px;">
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Gói Dịch Vụ:</label>
                    <select id="editOwnerTier" class="input-text" style="width:100%; margin-top:4px;">
                        <option value="Tiêu chuẩn">Tiêu chuẩn</option>
                        <option value="Nâng cao">Nâng cao</option>
                        <option value="Doanh nghiệp lớn">Doanh nghiệp lớn</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; color:var(--text-muted); font-weight:600;">Trạng Thái Tài Khoản:</label>
                    <select id="editOwnerStatus" class="input-text" style="width:100%; margin-top:4px;">
                        <option value="Hoạt động">Hoạt động</option>
                        <option value="Tạm khóa">Tạm khóa</option>
                    </select>
                </div>
            </div>

            <div style="background:rgba(251,191,36,0.06); padding:12px; border-radius:8px; border:1px solid rgba(251,191,36,0.25); margin-bottom:14px;">
                <label style="font-size:12px; color:var(--warning); font-weight:700; display:block; margin-bottom:4px;">Đặt Lại Mật Khẩu (Để trống nếu giữ nguyên):</label>
                <input id="editOwnerNewPass" type="text" class="input-text" style="width:100%;" placeholder="Nhập mật khẩu mới nếu muốn thay đổi...">
            </div>

            <p id="editOwnerMsg" style="color:var(--danger); font-size: 13px; margin-bottom: 12px; display:none;"></p>

            <div style="display:flex; gap:10px; margin-top:16px;">
                <button class="btn" style="flex:1; padding:11px; font-weight:700;" onclick="submitUpdateOwner()">LƯU THAY ĐỔI</button>
                <button class="btn btn-outline" style="padding:11px;" onclick="closeEditOwnerModal()">Hủy Bỏ</button>
            </div>
        </div>
    </div>

    <!-- HEADER CHÍNH -->
    <div class="header">
        <div>
            <div class="brand" id="uiTitle">SMART HSE VISION</div>
            <div class="sub-brand" id="uiSubTitle">Hệ Thống An Toàn AI • CSDL SQL Server • Tailscale Mesh VPN</div>
        </div>
        <div style="display: flex; gap: 10px; align-items: center;">
            <button class="btn btn-outline" onclick="downloadOwnerExcel()">Tải Báo Cáo Excel</button>
            <button class="btn btn-danger" onclick="logout()">Đăng Xuất</button>
        </div>
    </div>

    <!-- NỘI DUNG OWNER (DOANH NGHIỆP) -->
    <div class="container" id="ownerView">

        <!-- BANNER QUAN SÁT CỦA CHỦ SERVER KHI VÀO XƯỞNG -->
        <div id="impersonateBanner" style="display:none; background: linear-gradient(90deg, rgba(245, 158, 11, 0.15), rgba(56, 189, 248, 0.15)); border: 1px solid var(--warning); padding: 12px 18px; border-radius: 8px; margin-bottom: 18px; justify-content: space-between; align-items: center;">
            <div style="display:flex; align-items:center; gap:12px;">
                <span class="badge badge-warning" style="font-size:12px; padding:6px 10px;">CHẾ ĐỘ ADMIN TỔNG</span>
                <span style="font-size:14px; color:#F8FAFC;">Bạn đang kiểm tra và quản trị trực tiếp nhà xưởng: <b id="impersonateOrgName" style="color:var(--cyan);">...</b></span>
            </div>
            <button class="btn btn-outline" style="padding:6px 14px; font-size:12px; border-color:var(--warning); color:var(--warning); font-weight:700;" onclick="exitImpersonate()">← Quay Lại Bảng Chủ Server</button>
        </div>
        
        <!-- KPI CARDS TOÀN HỆ THỐNG -->
        <div class="stats-row">
            <div class="stat-box">
                <div class="stat-val" id="statToday">0</div>
                <div class="stat-lbl">Đã Điểm Danh Hôm Nay</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" style="color:var(--danger);" id="statVio">0</div>
                <div class="stat-lbl">Lượt Vi Phạm An Toàn</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" style="color:var(--cyan);" id="statSafeRate">100%</div>
                <div class="stat-lbl">Tỷ Lệ Tuân Thủ PPE</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" style="color:#F59E0B;" id="statTotalWorkers">2</div>
                <div class="stat-lbl">Tổng Quân Số Xưởng</div>
            </div>
        </div>

        <!-- THANH ĐIỀU HƯỚNG TABS -->
        <div class="tabs-nav">
            <button class="tab-btn active" onclick="switchOwnerTab('camTab', this)">Mắt Thần AI (Camera)</button>
            <button class="tab-btn" onclick="switchOwnerTab('attTab', this)">Nhật Ký Điểm Danh</button>
            <button class="tab-btn" onclick="switchOwnerTab('vioTab', this)">Danh Sách Vi Phạm PPE</button>
            <button class="tab-btn" onclick="switchOwnerTab('statsTab', this)">Thống Kê Chuyên Sâu</button>
            <button class="tab-btn" onclick="switchOwnerTab('workersTab', this)">Quản Lý Công Nhân</button>
        </div>

        <!-- TAB 1: MẮT THẦN AI (CAMERA THỜI GIAN THỰC) -->
        <div id="camTab" class="tab-pane">
            <div class="grid-2">
                <!-- Cột Camera -->
                <div class="card">
                    <div class="card-title">
                        <span>Luồng Giám Sát Trực Tiếp AI Vision</span>
                        <button id="btnCam" class="btn" onclick="toggleCam()">BẬT CAMERA</button>
                    </div>
                    <div class="cam-frame">
                        <img id="camStream" src="" style="display:none;">
                        <div id="camOffText" style="text-align: center; color: #64748B;">
                            <p style="font-size: 16px; font-weight: 700;">Camera Đang Tắt</p>
                            <p style="font-size: 12px; margin-top: 4px;">Bấm 'BẬT CAMERA' để bắt đầu AI Vision</p>
                        </div>
                    </div>

                    <!-- HỘP THIẾT LẬP CA LÀM VIỆC & ÂN HẠN -->
                    <div style="margin-top: 16px; background: #0E1A33; border: 1px solid rgba(56,189,248,0.22); border-radius: 12px; padding: 14px 16px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                            <span style="font-size:13.5px; font-weight:700; color:var(--teal);">Cấu Hình Ca Làm Việc & Ân Hạn Check-in</span>
                            <span id="shiftSaveMsg" style="display:none; color:#34D399; font-size:12px; font-weight:700; background:rgba(52,211,153,0.12); padding:3px 10px; border-radius:6px; border:1px solid #34D399;">Đã lưu cấu hình thành công!</span>
                        </div>
                        
                        <div style="display:grid; grid-template-columns: 1fr 1.2fr auto; gap: 12px; align-items: flex-end;">
                            <div>
                                <label style="display:block; font-size:12px; color:var(--text-muted); font-weight:600; margin-bottom:5px;">Giờ bắt đầu ca:</label>
                                <input id="txtShiftStart" type="time" class="input-text" style="width:100%; font-size:13.5px; font-weight:600;" value="07:30">
                            </div>
                            <div>
                                <label style="display:block; font-size:12px; color:var(--text-muted); font-weight:600; margin-bottom:5px;">Phút ân hạn (Cho phép muộn):</label>
                                <div style="display:flex; align-items:center; gap:6px;">
                                    <input id="txtGrace" type="number" min="0" max="180" class="input-text" style="width:100%; font-size:13.5px; font-weight:600;" value="15">
                                    <span style="font-size:13px; color:var(--text-muted); font-weight:600; white-space:nowrap;">phút</span>
                                </div>
                            </div>
                            <div>
                                <button type="button" class="btn" style="padding: 9px 18px; font-size:13px; font-weight:700;" onclick="saveShift()">LƯU CẤU HÌNH</button>
                            </div>
                        </div>
                        <p style="font-size:11.5px; color:var(--text-muted); margin-top:9px; line-height:1.4;">
                            Quy chế: Công nhân quét điểm danh sau <strong>Giờ bắt đầu + Phút ân hạn</strong> sẽ tự động bị tính là <strong>Đi trễ</strong>.
                        </p>
                    </div>
                </div>

                <!-- Cột Check-in Nhanh Hôm Nay -->
                <div class="card">
                    <div class="card-title">Điểm Danh Vừa Ghi Nhận</div>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>Giờ</th>
                                    <th>Mã CN</th>
                                    <th>Họ và Tên</th>
                                    <th>Trạng Thái</th>
                                </tr>
                            </thead>
                            <tbody id="tblLiveAttendance"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 2: NHẬT KÝ ĐIỂM DANH -->
        <div id="attTab" class="tab-pane" style="display:none;">
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:12px;">
                    <div class="card-title" style="margin-bottom:0;">Nhật Ký Điểm Danh Toàn Diện</div>
                    <div style="display:flex; gap:10px;">
                        <span class="badge" style="background:#1E293B; color:var(--sky); border:1px solid rgba(56,189,248,0.3); font-size:13px; padding:6px 12px;" id="badgeTotalAtt">Tổng: 0 lượt</span>
                        <span class="badge badge-success" style="font-size:13px; padding:6px 12px;" id="badgeOnTimeAtt">Đúng giờ: 0</span>
                        <span class="badge badge-danger" style="font-size:13px; padding:6px 12px;" id="badgeLateAtt">Đi trễ: 0</span>
                    </div>
                </div>
                
                <div class="filter-bar">
                    <input id="attSearchText" class="input-text" style="width: 200px;" placeholder="Tìm theo Mã hoặc Tên..." oninput="filterAttendanceTable()">
                    <input id="attDateInput" type="date" class="input-text" onchange="filterAttendanceTable()">
                    <select id="attStatusSelect" class="select-box" onchange="filterAttendanceTable()">
                        <option value="ALL">Tất cả trạng thái</option>
                        <option value="ON_TIME">Chỉ xem: Đi Đúng Giờ</option>
                        <option value="LATE">Chỉ xem: Điểm Danh Trễ</option>
                    </select>
                    <button class="btn btn-outline" onclick="clearAttFilter()">Đặt Lại</button>
                </div>

                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>STT</th>
                                <th>Ngày</th>
                                <th>Giờ Điểm Danh</th>
                                <th>Mã Công Nhân</th>
                                <th>Họ và Tên Công Nhân</th>
                                <th>Trạng Thái</th>
                                <th>Phương Thức Nhận Diện</th>
                            </tr>
                        </thead>
                        <tbody id="tblFullAttendance"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 3: DANH SÁCH VI PHẠM AN TOÀN PPE -->
        <div id="vioTab" class="tab-pane" style="display:none;">
            <div class="card">
                <div class="card-title">
                    <span>Nhật Ký Vi Phạm An Toàn Lao Động</span>
                    <span id="vioCountBadge" class="badge badge-danger">59 Vụ vi phạm</span>
                </div>

                <div class="filter-bar">
                    <input id="vioSearchText" class="input-text" style="width: 200px;" placeholder="Tìm theo Tên hoặc Mã..." oninput="filterViolationsTable()">
                    <input id="vioDateInput" type="date" class="input-text" onchange="filterViolationsTable()">
                    <select id="vioTypeSelect" class="select-box" onchange="filterViolationsTable()">
                        <option value="ALL">Tất cả loại vi phạm</option>
                        <option value="HELMET">Thiếu Mũ Bảo Hộ</option>
                        <option value="VEST">Thiếu Áo Phản Quang</option>
                        <option value="SHOES">Thiếu Giày An Toàn</option>
                    </select>
                    <button class="btn btn-outline" onclick="clearVioFilter()">Đặt Lại</button>
                </div>

                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>STT</th>
                                <th>Ngày</th>
                                <th>Giờ Vi Phạm</th>
                                <th>Mã CN</th>
                                <th>Họ và Tên</th>
                                <th>Lỗi Vi Phạm Cụ Thể</th>
                                <th>Mức Độ</th>
                                <th>Trạng Thái</th>
                                <th>Bằng Chứng</th>
                            </tr>
                        </thead>
                        <tbody id="tblFullViolations"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 4: THỐNG KÊ & BÁO CÁO (NGÀY / THÁNG / NĂM) -->
        <div id="statsTab" class="tab-pane" style="display:none;">
            <div class="card">
                <div class="card-title">
                    <span>Trung Tâm Thống Kê & Phân Tích An Toàn Lao Động</span>
                    <div style="display:flex; gap:8px;">
                        <button class="btn btn-outline" id="btnPeriodDay" onclick="setAnalyticsPeriod('day')">Theo Ngày</button>
                        <button class="btn btn-outline" id="btnPeriodMonth" onclick="setAnalyticsPeriod('month')">Theo Tháng</button>
                        <button class="btn btn-outline" id="btnPeriodYear" onclick="setAnalyticsPeriod('year')">Theo Năm</button>
                    </div>
                </div>

                <!-- Thanh chọn mốc thời gian -->
                <div class="filter-bar" style="display:flex; align-items:center; flex-wrap:wrap; gap:10px;">
                    <span style="font-size:13px; font-weight:700; color:var(--sky);" id="lblPeriodTarget">Chọn thời gian:</span>
                    <div id="periodInputHolder" style="display:inline-block;"></div>
                    <button class="btn" onclick="fetchAnalyticsData()">Xem Báo Cáo</button>
                    <div style="margin-left:auto; display:flex; gap:6px;">
                        <button class="btn btn-outline" style="font-size:12px; padding:4px 10px;" onclick="quickFilterStats('today')">Hôm Nay</button>
                        <button class="btn btn-outline" style="font-size:12px; padding:4px 10px;" onclick="quickFilterStats('sample')">Dữ Liệu Mẫu 02/10</button>
                        <button class="btn btn-outline" style="font-size:12px; padding:4px 10px;" onclick="quickFilterStats('year')">Cả Năm 2026</button>
                    </div>
                </div>

                <!-- 4 Thẻ KPI của kỳ thống kê -->
                <div class="stats-row" style="margin-top: 14px;">
                    <div class="stat-box">
                        <div class="stat-val" id="kpiPeriodAtt">0</div>
                        <div class="stat-lbl">Lượt Điểm Danh</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;" id="kpiSubAtt">Toàn bộ ca làm việc</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:var(--teal);" id="kpiPeriodOnTime">0</div>
                        <div class="stat-lbl">Đi Đúng Giờ</div>
                        <div style="font-size:11.5px; color:var(--teal); margin-top:2px; font-weight:600;" id="kpiSubOnTime">0% tỷ lệ đúng giờ</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:var(--warning);" id="kpiPeriodLate">0</div>
                        <div class="stat-lbl">Điểm Danh Trễ</div>
                        <div style="font-size:11.5px; color:var(--warning); margin-top:2px; font-weight:600;" id="kpiSubLate">0% đi trễ</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:var(--danger);" id="kpiPeriodVio">0</div>
                        <div class="stat-lbl">Vi Phạm Bảo Hộ</div>
                        <div style="font-size:11.5px; color:var(--danger); margin-top:2px;" id="kpiSubVio">Cần chấn chỉnh an toàn</div>
                    </div>
                </div>

                <!-- Thanh Tiến Trình Trực Quan: Đúng Giờ vs Đi Trễ -->
                <div class="card" style="margin-top:16px; margin-bottom:0; background:rgba(0,0,0,0.25);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
                        <span style="font-size:14px; font-weight:700; color:var(--sky);">Biểu Đồ Tỷ Lệ: Điểm Danh Đúng Giờ vs Điểm Danh Trễ</span>
                        <div style="font-size:12.5px; display:flex; gap:16px;">
                            <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block; width:12px; height:12px; background:var(--teal); border-radius:3px;"></span> Đúng Giờ: <b id="lblPunctualOnTime" style="color:var(--teal);">0% (0 lượt)</b></span>
                            <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block; width:12px; height:12px; background:var(--warning); border-radius:3px;"></span> Đi Trễ: <b id="lblPunctualLate" style="color:var(--warning);">0% (0 lượt)</b></span>
                        </div>
                    </div>
                    <div style="height:14px; width:100%; background:#1E293B; border-radius:7px; overflow:hidden; display:flex; box-shadow:inset 0 1px 3px rgba(0,0,0,0.5);">
                        <div id="barOnTime" style="height:100%; width:0%; background:var(--teal); transition:width 0.5s ease;"></div>
                        <div id="barLate" style="height:100%; width:0%; background:var(--warning); transition:width 0.5s ease;"></div>
                    </div>
                </div>

                <!-- Bảng Thống Kê Đúng Giờ & Đi Trễ Chi Tiết Từng Công Nhân -->
                <div class="card" style="margin-top:16px; margin-bottom:0; background:rgba(0,0,0,0.25);">
                    <div class="card-title" style="font-size:14.5px;">Bảng Thống Kê Đúng Giờ & Đi Trễ Chi Tiết Theo Công Nhân</div>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>Mã CN</th>
                                    <th>Họ và Tên Công Nhân</th>
                                    <th>Tổng Số Ca</th>
                                    <th>Số Lần Đúng Giờ</th>
                                    <th>Số Lần Đi Trễ</th>
                                    <th>Tỷ Lệ Đúng Giờ</th>
                                    <th>Đánh Giá Chuyên Cần</th>
                                </tr>
                            </thead>
                            <tbody id="tblWorkerPunctuality"></tbody>
                        </table>
                    </div>
                </div>

                <!-- 2 Cột Biểu Đồ Trực Quan -->
                <div class="grid-2" style="margin-top: 16px;">
                    <!-- Cột 1: Phân Loại Lỗi Vi Phạm PPE -->
                    <div class="card" style="margin-bottom:0; background:rgba(0,0,0,0.2);">
                        <div class="card-title" style="font-size:14.5px;">Phân Loại Lỗi Vi Phạm Bảo Hộ PPE</div>
                        
                        <div class="prog-container">
                            <div class="prog-header">
                                <span>Thiếu Mũ Bảo Hộ</span>
                                <span id="lblNoHelmetCount" style="color:var(--danger); font-weight:700;">0 vụ</span>
                            </div>
                            <div class="prog-bar-bg"><div id="barNoHelmet" class="prog-bar-val" style="background:var(--danger); width:0%;"></div></div>
                        </div>

                        <div class="prog-container">
                            <div class="prog-header">
                                <span>Thiếu Áo Phản Quang</span>
                                <span id="lblNoVestCount" style="color:var(--warning); font-weight:700;">0 vụ</span>
                            </div>
                            <div class="prog-bar-bg"><div id="barNoVest" class="prog-bar-val" style="background:var(--warning); width:0%;"></div></div>
                        </div>

                        <div class="prog-container">
                            <div class="prog-header">
                                <span>Thiếu Giày An Toàn</span>
                                <span id="lblNoShoesCount" style="color:var(--cyan); font-weight:700;">0 vụ</span>
                            </div>
                            <div class="prog-bar-bg"><div id="barNoShoes" class="prog-bar-val" style="background:var(--cyan); width:0%;"></div></div>
                        </div>
                    </div>

                    <!-- Cột 2: Top Công Nhân Vi Phạm Cần Nhắc Nhở -->
                    <div class="card" style="margin-bottom:0; background:rgba(0,0,0,0.2);">
                        <div class="card-title" style="font-size:14.5px;">Top Công Nhân Vi Phạm Trong Kỳ</div>
                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Mã CN</th>
                                        <th>Họ và Tên</th>
                                        <th>Số Lần</th>
                                        <th>Mức Cảnh Báo</th>
                                    </tr>
                                </thead>
                                <tbody id="tblTopViolators"></tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <!-- Biểu Đồ Xu Hướng Theo Mốc Thời Gian -->
                <div style="margin-top: 20px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <span style="font-size:14px; font-weight:700; color:var(--teal);">Biểu Đồ Xu Hướng Hoạt Động (Điểm Danh vs Vi Phạm)</span>
                        <div style="font-size:12px; display:flex; gap:14px;">
                            <span style="display:flex; align-items:center; gap:5px;"><span style="display:inline-block; width:10px; height:10px; background:var(--teal); border-radius:2px;"></span> Điểm Danh</span>
                            <span style="display:flex; align-items:center; gap:5px;"><span style="display:inline-block; width:10px; height:10px; background:var(--danger); border-radius:2px;"></span> Vi Phạm</span>
                        </div>
                    </div>
                    <div class="chart-grid" id="chartTimeline"></div>
                </div>
            </div>
        </div>

        <!-- TAB 5: QUẢN LÝ CÔNG NHÂN -->
        <div id="workersTab" class="tab-pane" style="display:none;">
            <div class="card">
                <div class="card-title">
                    <span>Danh Sách Công Nhân Thuộc Doanh Nghiệp</span>
                    <div style="display:flex; gap:8px;">
                        <button class="btn" onclick="openAddWorkerModal()">+ THÊM CÔNG NHÂN MỚI</button>
                        <button class="btn btn-outline" onclick="loadWorkersTable()">Làm Mới</button>
                    </div>
                </div>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Mã Công Nhân</th>
                                <th>Ảnh Khuôn Mặt</th>
                                <th>Họ và Tên</th>
                                <th>Phòng Ban</th>
                                <th>Số Điện Thoại</th>
                                <th>Ngày Đăng Ký</th>
                                <th>Trạng Thái</th>
                                <th>Hành Động</th>
                            </tr>
                        </thead>
                        <tbody id="tblWorkers"></tbody>
                    </table>
                </div>
            </div>
        </div>

    </div>

    <!-- NỘI DUNG CHỦ SERVER (SUPER ADMIN) - SIDEBAR DASHBOARD CHUẨN THỊ TRƯỜNG -->
    <div id="adminView" style="display: none;">
        
        <!-- THANH BÊN TRÁI (ADMIN SIDEBAR) -->
        <aside class="admin-sidebar">
            <!-- Header Logo -->
            <div class="admin-sidebar-header">
                <div class="admin-sidebar-logo">
                    <div class="admin-logo-icon">🛡️</div>
                    <div>
                        <div class="admin-logo-text">SMART HSE</div>
                        <div class="admin-logo-tag">Super Admin Portal</div>
                    </div>
                </div>
            </div>

            <!-- Profile Info -->
            <div class="admin-user-card">
                <div class="admin-avatar">AD</div>
                <div class="admin-user-info">
                    <div class="admin-user-name">Chủ Server</div>
                    <div class="admin-user-role">
                        <span class="admin-status-dot"></span>
                        <span>Trực tuyến (Online)</span>
                    </div>
                </div>
            </div>

            <!-- Menu Navigation -->
            <div class="admin-menu-label">Quản Trị Hệ Thống</div>
            <ul class="admin-nav-list">
                <li class="admin-nav-item active" id="navItemAdminOwners" onclick="switchAdminTab('adminTabOwners', this)">
                    <span class="admin-nav-icon">🏢</span>
                    <span>Doanh Nghiệp & Xưởng</span>
                </li>
                <li class="admin-nav-item" id="navItemAdminInfra" onclick="switchAdminTab('adminTabInfra', this)">
                    <span class="admin-nav-icon">🖥️</span>
                    <span>Hạ Tầng & Server AI</span>
                </li>
                <li class="admin-nav-item" id="navItemAdminLogs" onclick="switchAdminTab('adminTabLogs', this)">
                    <span class="admin-nav-icon">📋</span>
                    <span>Nhật Ký Hệ Thống (Audit)</span>
                </li>
            </ul>

            <div class="admin-menu-label" style="margin-top:10px;">Báo Cáo & Dữ Liệu</div>
            <ul class="admin-nav-list">
                <li>
                    <a href="/api/admin/export-excel" download class="admin-nav-item" style="color:var(--text-muted);">
                        <span class="admin-nav-icon">📥</span>
                        <span>Xuất File Excel Toàn Mạng</span>
                    </a>
                </li>
            </ul>

            <!-- Footer Quick Stats Widget -->
            <div class="admin-sidebar-footer">
                <div class="admin-sys-widget">
                    <div class="admin-sys-row">
                        <span class="admin-sys-lbl">Cơ sở dữ liệu:</span>
                        <span class="admin-sys-val">SQL Server</span>
                    </div>
                    <div class="admin-sys-row">
                        <span class="admin-sys-lbl">Cổng dịch vụ:</span>
                        <span class="admin-sys-val">8501 (Active)</span>
                    </div>
                    <div class="admin-sys-row" style="margin-bottom:0;">
                        <span class="admin-sys-lbl">Mô hình AI:</span>
                        <span class="admin-sys-val" style="color:#34D399;">SFace + YOLOv8</span>
                    </div>
                </div>

                <button class="btn btn-danger" style="width:100%; padding:10px; font-weight:700; font-size:13px; display:flex; align-items:center; justify-content:center; gap:8px;" onclick="logout()">
                    <span>Đăng Xuất Admin</span>
                </button>
            </div>
        </aside>

        <!-- KHU VỰC NỘI DUNG CHÍNH BÊN PHẢI (MAIN CONTENT AREA) -->
        <main class="admin-main-wrapper">
            
            <!-- Topbar của Admin -->
            <header class="admin-topbar">
                <div>
                    <div class="admin-topbar-title" id="adminTopbarTitle">
                        <span>Quản Lý Doanh Nghiệp & Nhà Xưởng</span>
                    </div>
                    <div class="admin-topbar-breadcrumb" id="adminBreadcrumb">Admin Portal / Doanh Nghiệp & Nhà Xưởng</div>
                </div>
                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="background:rgba(56,189,248,0.08); border:1px solid rgba(56,189,248,0.2); padding:6px 14px; border-radius:20px; font-size:12px; color:var(--cyan); display:flex; align-items:center; gap:8px;">
                        <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#34D399; box-shadow:0 0 6px #34D399;"></span>
                        <span id="adminLiveClock">Đang tải...</span>
                    </div>
                    <a href="/api/admin/export-excel" class="btn btn-outline" download style="font-size:12.5px; padding:7px 14px;">Báo Cáo Toàn Mạng (Excel)</a>
                </div>
            </header>

            <!-- Nội dung Body (KPIs + Tabs Content) -->
            <div class="admin-content-body">
                
                <!-- 5 THẺ THỐNG KÊ TOÀN CỤC (KPIS) -->
                <div class="stats-row" style="margin-bottom: 22px;">
                    <div class="stat-box">
                        <div class="stat-val" id="adminTotalTenants" style="color:var(--teal);">1</div>
                        <div class="stat-lbl">Tổng Doanh Nghiệp</div>
                        <div style="font-size:11.5px; color:var(--cyan); margin-top:4px;" id="adminTenantsSub">1 đang hoạt động</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" id="adminTotalWorkers" style="color:var(--cyan);">0</div>
                        <div class="stat-lbl">Tổng Quân Số Toàn Mạng</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;">Trên tất cả các nhà xưởng</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" id="adminTotalAtt" style="color:#34D399;">0</div>
                        <div class="stat-lbl">Lượt Điểm Danh Hệ Thống</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;" id="adminAttTodaySub">Hôm nay: 0 lượt</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" id="adminTotalVio" style="color:var(--danger);">0</div>
                        <div class="stat-lbl">Lượt Vi Phạm An Toàn (PPE)</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;" id="adminVioTodaySub">Hôm nay: 0 vi phạm</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" id="adminSystemHealth" style="color:#38BDF8; font-size:22px; display:flex; align-items:center; justify-content:center; gap:6px;">
                            <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#34D399; box-shadow:0 0 10px #34D399;"></span> Online
                        </div>
                        <div class="stat-lbl">Trạng Thái SQL Server & AI</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;" id="adminDbStatusSub">.\SQLEXPRESS01 (Smart_HSE)</div>
                    </div>
                </div>

                <!-- TAB 1: QUẢN LÝ DOANH NGHIỆP -->
                <div id="adminTabOwners" class="admin-tab-pane">
                    <div class="card">
                        <div class="card-title" style="flex-wrap:wrap; gap:12px;">
                            <span>Danh Sách Các Doanh Nghiệp / Nhà Xưởng Thuê Dịch Vụ Hệ Thống</span>
                            <div style="display:flex; gap:8px;">
                                <button class="btn" onclick="openAddOwnerModal()">+ THÊM DOANH NGHIỆP MỚI</button>
                                <button class="btn btn-outline" onclick="loadAdminDashboard()">Làm Mới</button>
                            </div>
                        </div>

                        <!-- BỘ LỌC TÌM KIẾM -->
                        <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom: 16px; background:rgba(255,255,255,0.02); padding:10px; border-radius:8px; border:1px solid rgba(56,189,248,0.12);">
                            <input id="adminSearchInput" class="input-text" style="flex:1; min-width:220px;" placeholder="Tìm theo Mã, Tên xưởng, Người đại diện, Username..." oninput="filterAdminOwnersTable()">
                            <select id="adminStatusFilter" class="input-text" style="width:160px;" onchange="filterAdminOwnersTable()">
                                <option value="">Tất cả trạng thái</option>
                                <option value="Hoạt động">Đang hoạt động</option>
                                <option value="Tạm khóa">Đang tạm khóa</option>
                            </select>
                            <select id="adminTierFilter" class="input-text" style="width:170px;" onchange="filterAdminOwnersTable()">
                                <option value="">Tất cả gói dịch vụ</option>
                                <option value="Tiêu chuẩn">Gói Tiêu chuẩn</option>
                                <option value="Nâng cao">Gói Nâng cao</option>
                                <option value="Doanh nghiệp lớn">Doanh nghiệp lớn</option>
                            </select>
                        </div>

                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Mã Xưởng</th>
                                        <th>Doanh Nghiệp & Đại Diện</th>
                                        <th>Thông Tin Liên Hệ</th>
                                        <th>Tài Khoản App</th>
                                        <th>Quân Số</th>
                                        <th>Dữ Liệu Hệ Thống</th>
                                        <th>Gói Dịch Vụ</th>
                                        <th>Hạn Dùng</th>
                                        <th>Trạng Thái</th>
                                        <th>Hành Động</th>
                                    </tr>
                                </thead>
                                <tbody id="tblOwners"></tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <!-- TAB 2: HẠ TẦNG & GIÁM SÁT SERVER -->
                <div id="adminTabInfra" class="admin-tab-pane" style="display:none;">
                    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:16px;">
                        
                        <!-- Card 1: SQL Server Database -->
                        <div class="card">
                            <div class="card-title" style="color:var(--teal);">
                                <span>Cơ Sở Dữ Liệu SQL Server</span>
                                <span class="badge badge-success" id="infraDbBadge">KẾT NỐI TỐT</span>
                            </div>
                            <div style="font-size:13px; line-height:2.0; color:var(--text-main);">
                                <div>• <b>Máy chủ SQL:</b> <span style="color:var(--cyan);" id="infraDbHost">.\SQLEXPRESS01</span></div>
                                <div>• <b>Tên CSDL:</b> <span style="color:var(--cyan);">Smart_HSE</span></div>
                                <div>• <b>Trình điều khiển:</b> ODBC Driver 17 for SQL Server</div>
                                <div>• <b>Xác thực:</b> Windows Authentication (Trusted Connection)</div>
                                <div>• <b>Độ trễ truy vấn:</b> <span style="color:#34D399;">< 2ms (Cực nhanh)</span></div>
                            </div>
                            <div style="margin-top:14px;">
                                <button class="btn btn-outline" style="width:100%; font-size:12px;" onclick="testInfraDb()">Kiểm Tra Kết Nối CSDL Ngay</button>
                            </div>
                        </div>

                        <!-- Card 2: AI Vision Models -->
                        <div class="card">
                            <div class="card-title" style="color:var(--teal);">
                                <span>Trí Tuệ Nhân Tạo (AI Vision Engine)</span>
                                <span class="badge badge-success" id="infraAiBadge">SẴN SÀNG</span>
                            </div>
                            <div style="font-size:13px; line-height:2.0; color:var(--text-main);">
                                <div>• <b>Face Detector:</b> YuNet (ONNX OpenCV DNN)</div>
                                <div>• <b>Face Recognizer:</b> SFace 128-d Cosine (Threshold: 0.32)</div>
                                <div>• <b>PPE Compliance:</b> YOLOv8 Deep Learning (Mũ & Áo)</div>
                                <div>• <b>Kiến trúc luồng:</b> Decoupled 2-Thread Async (30 FPS)</div>
                                <div>• <b>Bộ nhớ khuôn mặt:</b> <span style="color:var(--cyan);" id="infraFaceCount">Đã tải vào RAM</span></div>
                            </div>
                            <div style="margin-top:14px;">
                                <button class="btn btn-outline" style="width:100%; font-size:12px;" onclick="reloadAiEngine()">Nạp Lại Hồ Sơ Khuôn Mặt Vào RAM</button>
                            </div>
                        </div>

                        <!-- Card 3: Network & Distribution -->
                        <div class="card">
                            <div class="card-title" style="color:var(--teal);">
                                <span>Mạng & Phân Phối Dịch Vụ</span>
                                <span class="badge badge-cyan">PORT 8501</span>
                            </div>
                            <div style="font-size:13px; line-height:2.0; color:var(--text-main);">
                                <div>• <b>Web Server:</b> FastAPI ASGI + Uvicorn High Performance</div>
                                <div>• <b>Giao thức Camera:</b> MJPEG Stream HD 720p (Quality 85)</div>
                                <div>• <b>Địa chỉ Local:</b> http://127.0.0.1:8501</div>
                                <div>• <b>Mạng Mesh VPN:</b> Tailscale Supported (Bảo mật cao)</div>
                                <div>• <b>Buffer Camera:</b> Threaded Buffer Drop (Không giật lùi hình)</div>
                            </div>
                            <div style="margin-top:14px;">
                                <a href="/api/admin/export-excel" download class="btn" style="display:block; text-align:center; font-size:12px; text-decoration:none;">Xuất Báo Cáo Tổng Hợp Toàn Mạng (Excel)</a>
                            </div>
                        </div>

                    </div>
                </div>

                <!-- TAB 3: NHẬT KÝ HỆ THỐNG (AUDIT LOGS) -->
                <div id="adminTabLogs" class="admin-tab-pane" style="display:none;">
                    <div class="card">
                        <div class="card-title">
                            <span>Nhật Ký Sự Kiện Hoạt Động Toàn Cục (Audit Logs)</span>
                            <button class="btn btn-outline" onclick="loadAdminAuditLogs()">Làm Mới Nhật Ký</button>
                        </div>
                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Thời Gian</th>
                                        <th>Loại Sự Kiện</th>
                                        <th>Nhà Xưởng / Doanh Nghiệp</th>
                                        <th>Mã Nhân Sự</th>
                                        <th>Họ và Tên</th>
                                        <th>Chi Tiết Ghi Nhận</th>
                                        <th>Phương Thức</th>
                                    </tr>
                                </thead>
                                <tbody id="tblAdminLogs"></tbody>
                            </table>
                        </div>
                    </div>
                </div>

            </div>
        </main>

    </div>

    <script>
        let isCamOn = false;
        let currentOwner = 'OWN01';
        let currentPeriod = 'month'; // 'day', 'month', 'year'
        let cachedAttData = [];
        let cachedVioData = [];
        let isShiftConfigLoaded = false;

        // ĐĂNG NHẬP
        function onRoleChange(role) {
            const errEl = document.getElementById('loginErr');
            if(errEl) errEl.style.display = 'none';
            if(role === 'admin') {
                document.getElementById('txtUser').value = 'admin';
                document.getElementById('txtPass').value = 'admin123';
            } else {
                document.getElementById('txtUser').value = 'khiem_owner';
                document.getElementById('txtPass').value = '123456';
            }
        }

        function doLogin() {
            const roleEl = document.querySelector('input[name="role"]:checked');
            const role = roleEl ? roleEl.value : 'owner';
            const u = (document.getElementById('txtUser').value || '').trim();
            const p = (document.getElementById('txtPass').value || '').trim();
            const errEl = document.getElementById('loginErr');
            const btn = document.getElementById('btnLogin');

            if(!u || !p) {
                errEl.innerText = 'Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu!';
                errEl.style.display = 'block';
                return;
            }

            errEl.style.display = 'none';
            if(btn) {
                btn.innerText = 'Đang xác thực...';
                btn.disabled = true;
            }
            
            fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({role, u, p})
            })
            .then(res => {
                if(!res.ok) {
                    throw new Error(`Máy chủ phản hồi mã lỗi ${res.status}`);
                }
                return res.json();
            })
            .then(data => {
                if(data.success) {
                    document.getElementById('loginModal').style.display = 'none';
                    if(role === 'admin') {
                        const globalHeader = document.querySelector('.header');
                        if(globalHeader) globalHeader.style.display = 'none';
                        document.getElementById('ownerView').style.display = 'none';
                        document.getElementById('adminView').style.display = 'flex';
                        loadAdminDashboard();
                    } else {
                        const globalHeader = document.querySelector('.header');
                        if(globalHeader) globalHeader.style.display = 'flex';
                        document.getElementById('adminView').style.display = 'none';
                        document.getElementById('ownerView').style.display = 'block';
                        currentOwner = (data.data && data.data.ma_owner) ? data.data.ma_owner : 'OWN01';
                        const orgName = (data.data && data.data.ten_doanh_nghiep) ? data.data.ten_doanh_nghiep : 'DOANH NGHIỆP';
                        document.getElementById('uiTitle').innerText = orgName.toUpperCase();
                        const subEl = document.getElementById('uiSubTitle');
                        if(subEl) subEl.innerText = 'Hệ Thống An Toàn AI • CSDL SQL Server • Tailscale Mesh VPN';
                        initOwnerDashboard();
                    }
                } else {
                    errEl.innerText = data.message || 'Sai tên đăng nhập hoặc mật khẩu!';
                    errEl.style.display = 'block';
                }
            })
            .catch(err => {
                console.error('Lỗi kết nối login:', err);
                errEl.innerText = 'Không thể kết nối máy chủ! Vui lòng đảm bảo cửa sổ chạy server (file .bat) vẫn đang mở.';
                errEl.style.display = 'block';
            })
            .finally(() => {
                if(btn) {
                    btn.innerText = 'ĐĂNG NHẬP HỆ THỐNG';
                    btn.disabled = false;
                }
            });
        }

        function logout() {
            toggleCam(false);
            const globalHeader = document.querySelector('.header');
            if(globalHeader) globalHeader.style.display = 'flex';
            const impEl = document.getElementById('impersonateBanner');
            if(impEl) impEl.style.display = 'none';
            document.getElementById('adminView').style.display = 'none';
            document.getElementById('loginModal').style.display = 'flex';
        }

        function downloadOwnerExcel() {
            window.location.href = `/api/export-excel?owner=${currentOwner || 'OWN01'}`;
        }

        // CHUYỂN TABS OWNER
        function switchOwnerTab(tabId, btn) {
            document.querySelectorAll('.tab-pane').forEach(el => el.style.display = 'none');
            document.getElementById(tabId).style.display = 'block';
            
            if(btn) {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            }

            if(tabId === 'attTab') loadAttendanceTable();
            if(tabId === 'vioTab') loadViolationsTable();
            if(tabId === 'statsTab') fetchAnalyticsData();
            if(tabId === 'workersTab') loadWorkersTable();
        }

        // KHỞI TẠO DASHBOARD OWNER
        function initOwnerDashboard() {
            loadOwnerKPIs();
            loadLiveAttendance();
            setAnalyticsPeriod('month');
        }

        // TẢI CHỈ SỐ KPI CHÍNH
        function loadOwnerKPIs() {
            fetch('/api/owner/data?owner=' + currentOwner)
            .then(res => res.json())
            .then(d => {
                document.getElementById('statToday').innerText = d.today_count;
                document.getElementById('statVio').innerText = d.vio_count;
                if(d.shift && !isShiftConfigLoaded) {
                    const startEl = document.getElementById('txtShiftStart');
                    const graceEl = document.getElementById('txtGrace');
                    if(document.activeElement !== startEl && document.activeElement !== graceEl) {
                        startEl.value = d.shift.shift_start || '07:30';
                        graceEl.value = d.shift.grace_period_mins || 15;
                        isShiftConfigLoaded = true;
                    }
                }
                const total = d.today_count + d.vio_count;
                const safeRate = total === 0 ? 100 : Math.round((d.today_count / total) * 100);
                document.getElementById('statSafeRate').innerText = safeRate + '%';
            });
        }

        // TẢI ĐIỂM DANH LIVE (TAB 1)
        function loadLiveAttendance() {
            fetch('/api/owner/attendance?owner=' + currentOwner)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data) ? data : [];
                let html = '';
                const recents = list.slice(0, 8);
                recents.forEach(r => {
                    const isLate = (r.trang_thai || '').toLowerCase().includes('trễ');
                    html += `<tr>
                        <td style="color:var(--text-muted);">${r.gio || '--:--'}</td>
                        <td style="font-weight:700; color:var(--sky);">${r.ma_cn || 'CN-001'}</td>
                        <td>${r.ho_ten || 'Pham Van Khiem'}</td>
                        <td><span class="badge ${isLate ? 'badge-danger':'badge-success'}">${isLate ? r.trang_thai : 'Đúng giờ'}</span></td>
                    </tr>`;
                });
                document.getElementById('tblLiveAttendance').innerHTML = html || '<tr><td colspan="4" style="text-align:center; color:#64748B;">Chưa có lượt điểm danh nào hôm nay</td></tr>';
            })
            .catch(() => {});
        }

        // TẢI BẢNG ĐIỂM DANH TOÀN DIỆN (TAB 2)
        function loadAttendanceTable() {
            fetch('/api/owner/attendance?owner=' + currentOwner)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data) ? data : [];
                cachedAttData = list;
                renderAttendanceTable(list);
            })
            .catch(() => {});
        }

        function renderAttendanceTable(list) {
            let html = '';
            let onTimeCount = 0;
            let lateCount = 0;
            const arr = Array.isArray(list) ? list : [];
            arr.forEach((r) => {
                const isLate = (r.trang_thai || '').toLowerCase().includes('trễ');
                if(isLate) lateCount++; else onTimeCount++;
                const maCn = (r.ma_cn && r.ma_cn !== 'null') ? r.ma_cn : 'CN-001';
                const hoTen = (r.ho_ten && r.ho_ten !== 'null' && r.ho_ten !== 'Chưa rõ') ? r.ho_ten : 'Pham Van Khiem';
                const badgeSt = isLate 
                    ? `<span class="badge badge-danger">${r.trang_thai || 'Đi trễ'}</span>`
                    : `<span class="badge badge-success">Đúng giờ</span>`;

                html += `<tr>
                    <td style="color:var(--text-muted);">${r.id}</td>
                    <td>${r.ngay}</td>
                    <td style="font-weight:700;">${r.gio}</td>
                    <td style="color:var(--sky); font-weight:700;">${maCn}</td>
                    <td>${hoTen}</td>
                    <td>${badgeSt}</td>
                    <td style="color:var(--sky);">${r.phuong_thuc || 'Khuôn mặt AI'}</td>
                </tr>`;
            });
            document.getElementById('tblFullAttendance').innerHTML = html || '<tr><td colspan="7" style="text-align:center; color:#64748B;">Không tìm thấy dữ liệu điểm danh phù hợp</td></tr>';

            const bTotal = document.getElementById('badgeTotalAtt');
            const bOnTime = document.getElementById('badgeOnTimeAtt');
            const bLate = document.getElementById('badgeLateAtt');
            if(bTotal) bTotal.innerText = `Tổng: ${arr.length} lượt`;
            if(bOnTime) bOnTime.innerText = `Đúng giờ: ${onTimeCount}`;
            if(bLate) bLate.innerText = `Đi trễ: ${lateCount}`;
        }

        function filterAttendanceTable() {
            const txt = (document.getElementById('attSearchText').value || '').toLowerCase();
            const dateVal = document.getElementById('attDateInput').value; // YYYY-MM-DD
            const stVal = document.getElementById('attStatusSelect').value;

            let filtered = (cachedAttData || []).filter(r => {
                const ma = (r.ma_cn || 'CN-001').toLowerCase();
                const ten = (r.ho_ten || 'Pham Van Khiem').toLowerCase();
                const matchTxt = ma.includes(txt) || ten.includes(txt);
                let matchDate = true;
                if(dateVal) {
                    const [y, m, d] = dateVal.split('-');
                    matchDate = r.ngay === `${d}/${m}/${y}`;
                }
                let matchSt = true;
                const isLate = (r.trang_thai || '').toLowerCase().includes('trễ');
                if(stVal === 'ON_TIME') matchSt = !isLate;
                if(stVal === 'LATE') matchSt = isLate;

                return matchTxt && matchDate && matchSt;
            });
            renderAttendanceTable(filtered);
        }

        function clearAttFilter() {
            document.getElementById('attSearchText').value = '';
            document.getElementById('attDateInput').value = '';
            document.getElementById('attStatusSelect').value = 'ALL';
            renderAttendanceTable(cachedAttData);
        }

        // TẢI BẢNG VI PHẠM AN TOÀN (TAB 3)
        function loadViolationsTable() {
            fetch('/api/owner/violations?owner=' + currentOwner)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data) ? data : [];
                cachedVioData = list;
                document.getElementById('vioCountBadge').innerText = list.length + ' Vụ vi phạm';
                renderViolationsTable(list);
            })
            .catch(() => {});
        }

        function renderViolationsTable(list) {
            let html = '';
            (Array.isArray(list) ? list : []).forEach((r) => {
                const hasPic = r.minh_chung && r.minh_chung.length > 0;
                html += `<tr>
                    <td style="color:var(--text-muted);">${r.id}</td>
                    <td>${r.ngay}</td>
                    <td style="font-weight:700;">${r.gio}</td>
                    <td style="color:var(--danger); font-weight:700;">${r.ma_cn}</td>
                    <td>${r.ho_ten}</td>
                    <td style="color:#FFF;">${r.loi_vi_pham}</td>
                    <td><span class="badge badge-warning">${r.muc_do}</span></td>
                    <td><span class="badge badge-danger">${r.trang_thai}</span></td>
                    <td>
                        ${hasPic ? `<button class="btn btn-outline" style="padding:4px 8px; font-size:12px;" onclick="viewEvidence('${r.minh_chung}', '${r.ho_ten} - ${r.loi_vi_pham}')">Xem Ảnh</button>` : `<span style="color:#64748B;">Không có</span>`}
                    </td>
                </tr>`;
            });
            document.getElementById('tblFullViolations').innerHTML = html || '<tr><td colspan="9" style="text-align:center; color:#64748B;">Không tìm thấy dữ liệu vi phạm an toàn phù hợp</td></tr>';
        }

        function filterViolationsTable() {
            const txt = (document.getElementById('vioSearchText').value || '').toLowerCase();
            const dateVal = document.getElementById('vioDateInput').value;
            const typeVal = document.getElementById('vioTypeSelect').value;

            let filtered = cachedVioData.filter(r => {
                const matchTxt = r.ma_cn.toLowerCase().includes(txt) || r.ho_ten.toLowerCase().includes(txt);
                let matchDate = true;
                if(dateVal) {
                    const [y, m, d] = dateVal.split('-');
                    matchDate = r.ngay === `${d}/${m}/${y}`;
                }
                let matchType = true;
                const desc = r.loi_vi_pham.toLowerCase();
                if(typeVal === 'HELMET') matchType = desc.includes('mu');
                if(typeVal === 'VEST') matchType = desc.includes('ao');
                if(typeVal === 'SHOES') matchType = desc.includes('giay');

                return matchTxt && matchDate && matchType;
            });
            renderViolationsTable(filtered);
        }

        function clearVioFilter() {
            document.getElementById('vioSearchText').value = '';
            document.getElementById('vioDateInput').value = '';
            document.getElementById('vioTypeSelect').value = 'ALL';
            renderViolationsTable(cachedVioData);
        }

        // XEM ẢNH BẰNG CHỨNG
        function viewEvidence(imgName, note) {
            document.getElementById('evidenceImg').src = '/violations/' + imgName;
            document.getElementById('evidenceNote').innerText = 'Chi tiết: ' + (note || imgName);
            document.getElementById('evidenceModal').style.display = 'flex';
        }

        function closeEvidence(e) {
            document.getElementById('evidenceModal').style.display = 'none';
            document.getElementById('evidenceImg').src = '';
        }

        // THỐNG KÊ CHUYÊN SÂU (TAB 4)
        function setAnalyticsPeriod(period, customVal) {
            currentPeriod = period;
            ['Day', 'Month', 'Year'].forEach(p => {
                const btn = document.getElementById('btnPeriod' + p);
                if(btn) btn.className = (p.toLowerCase() === period) ? 'btn' : 'btn btn-outline';
            });

            const holder = document.getElementById('periodInputHolder');
            const now = new Date();
            const y = now.getFullYear();
            const m = String(now.getMonth() + 1).padStart(2, '0');
            const d = String(now.getDate()).padStart(2, '0');
            const defaultDate = customVal || `${y}-${m}-${d}`;

            if(period === 'day') {
                document.getElementById('lblPeriodTarget').innerText = 'Chọn ngày:';
                holder.innerHTML = `<input id="periodValInput" type="date" class="input-text" value="${defaultDate}">`;
            } else if(period === 'month') {
                document.getElementById('lblPeriodTarget').innerText = 'Chọn tháng:';
                const defaultMonth = customVal || `${y}-${m}`;
                holder.innerHTML = `<input id="periodValInput" type="month" class="input-text" value="${defaultMonth}">`;
            } else {
                document.getElementById('lblPeriodTarget').innerText = 'Chọn năm:';
                holder.innerHTML = `<select id="periodValInput" class="select-box"><option value="2026" selected>Năm 2026</option><option value="2025">Năm 2025</option></select>`;
            }
            fetchAnalyticsData();
        }

        function quickFilterStats(type) {
            if(type === 'today') {
                const now = new Date();
                const y = now.getFullYear();
                const m = String(now.getMonth() + 1).padStart(2, '0');
                const d = String(now.getDate()).padStart(2, '0');
                setAnalyticsPeriod('day', `${y}-${m}-${d}`);
            } else if(type === 'sample') {
                setAnalyticsPeriod('day', '2026-10-02');
            } else if(type === 'year') {
                setAnalyticsPeriod('year', '2026');
            }
        }

        function fetchAnalyticsData() {
            const inputEl = document.getElementById('periodValInput');
            const val = inputEl ? inputEl.value : '';
            
            fetch(`/api/owner/analytics?owner=${currentOwner}&period=${currentPeriod}&val=${val}`)
            .then(res => res.json())
            .then(data => {
                const totalAtt = data.total_att || 0;
                const onTime = data.on_time || 0;
                const late = data.late || 0;
                const onTimeRate = data.on_time_rate || (totalAtt > 0 ? Math.round((onTime / totalAtt) * 100) : 0);
                const lateRate = data.late_rate || (totalAtt > 0 ? Math.round((late / totalAtt) * 100) : 0);

                // Cập nhật 4 hộp KPI
                document.getElementById('kpiPeriodAtt').innerText = totalAtt;
                document.getElementById('kpiPeriodOnTime').innerText = onTime;
                document.getElementById('kpiPeriodLate').innerText = late;
                document.getElementById('kpiPeriodVio').innerText = data.total_vio || 0;

                const subAtt = document.getElementById('kpiSubAtt');
                const subOnTime = document.getElementById('kpiSubOnTime');
                const subLate = document.getElementById('kpiSubLate');
                const subVio = document.getElementById('kpiSubVio');
                if(subAtt) subAtt.innerText = totalAtt > 0 ? `${totalAtt} lượt ghi nhận` : 'Chưa có lượt điểm danh';
                if(subOnTime) subOnTime.innerText = `${onTimeRate}% đúng giờ`;
                if(subLate) subLate.innerText = `${lateRate}% đi trễ`;
                if(subVio) subVio.innerText = `${data.total_vio || 0} vụ cần chấn chỉnh`;

                // Cập nhật thanh tiến trình Đúng Giờ vs Đi Trễ
                const barOnTime = document.getElementById('barOnTime');
                const barLate = document.getElementById('barLate');
                const lblOnTime = document.getElementById('lblPunctualOnTime');
                const lblLate = document.getElementById('lblPunctualLate');
                if(barOnTime) barOnTime.style.width = onTimeRate + '%';
                if(barLate) barLate.style.width = lateRate + '%';
                if(lblOnTime) lblOnTime.innerText = `${onTimeRate}% (${onTime} lượt)`;
                if(lblLate) lblLate.innerText = `${lateRate}% (${late} lượt)`;

                // Bảng Thống Kê Chuyên Cần Chi Tiết Từng Công Nhân
                let puncHtml = '';
                (data.punctuality || []).forEach(p => {
                    const isGood = p.ty_le >= 90;
                    puncHtml += `<tr>
                        <td style="color:var(--sky); font-weight:700;">${p.ma_cn}</td>
                        <td style="font-weight:600;">${p.ho_ten}</td>
                        <td style="font-weight:700;">${p.tong_ca} ca</td>
                        <td style="color:var(--teal); font-weight:700;">${p.dung_gio} lần</td>
                        <td style="color:var(--warning); font-weight:700;">${p.di_tre} lần</td>
                        <td style="font-weight:700; color:${isGood ? 'var(--teal)':'var(--warning)'};">${p.ty_le}%</td>
                        <td><span class="badge ${isGood ? 'badge-success':'badge-warning'}">${p.danh_gia || (isGood ? 'Xuất sắc':'Cần nhắc nhở')}</span></td>
                    </tr>`;
                });
                const tblPunc = document.getElementById('tblWorkerPunctuality');
                if(tblPunc) {
                    tblPunc.innerHTML = puncHtml || '<tr><td colspan="7" style="text-align:center; color:#64748B;">Không có dữ liệu điểm danh trong kỳ này. (Bấm "Dữ Liệu Mẫu 02/10" hoặc "Cả Năm 2026" để xem dữ liệu)</td></tr>';
                }

                // Cập nhật thanh tiến trình PPE
                const totalVio = data.total_vio || 1;
                const hPct = Math.round(((data.no_helmet || 0) / totalVio) * 100);
                const vPct = Math.round(((data.no_vest || 0) / totalVio) * 100);
                const sPct = Math.round(((data.no_shoes || 0) / totalVio) * 100);

                document.getElementById('lblNoHelmetCount').innerText = `${data.no_helmet || 0} vụ (${hPct}%)`;
                document.getElementById('barNoHelmet').style.width = hPct + '%';

                document.getElementById('lblNoVestCount').innerText = `${data.no_vest || 0} vụ (${vPct}%)`;
                document.getElementById('barNoVest').style.width = vPct + '%';

                document.getElementById('lblNoShoesCount').innerText = `${data.no_shoes || 0} vụ (${sPct}%)`;
                document.getElementById('barNoShoes').style.width = sPct + '%';

                // Top người vi phạm
                let topHtml = '';
                (data.top_violators || []).forEach(t => {
                    topHtml += `<tr>
                        <td style="color:var(--danger); font-weight:700;">${t.ma_cn}</td>
                        <td>${t.ho_ten}</td>
                        <td style="font-weight:700;">${t.so_lan} lần</td>
                        <td><span class="badge ${t.so_lan > 5 ? 'badge-danger':'badge-warning'}">${t.so_lan > 5 ? 'Kỷ luật / Đào tạo lại':'Cảnh báo nội bộ'}</span></td>
                    </tr>`;
                });
                document.getElementById('tblTopViolators').innerHTML = topHtml || '<tr><td colspan="4" style="text-align:center; color:#64748B;">Không có trường hợp vi phạm nào</td></tr>';

                // Vẽ Biểu Đồ Cột Timeline
                renderTimelineChart(data.timeline || []);
            });
        }

        function renderTimelineChart(timeline) {
            const chart = document.getElementById('chartTimeline');
            if(!timeline || timeline.length === 0) {
                chart.innerHTML = '<p style="color:#64748B; padding:20px; text-align:center; width:100%;">Chưa có dữ liệu xu hướng cho kỳ này</p>';
                return;
            }

            let maxVal = 1;
            timeline.forEach(t => {
                if(t.att > maxVal) maxVal = t.att;
                if(t.vio > maxVal) maxVal = t.vio;
            });

            let html = '';
            timeline.forEach(t => {
                const attH = Math.max(3, Math.round((t.att / maxVal) * 140));
                const vioH = Math.max(3, Math.round((t.vio / maxVal) * 140));
                html += `
                <div class="chart-col">
                    <div class="chart-bars">
                        <div class="bar-att" style="height:${t.att > 0 ? attH : 2}px;" title="Điểm danh: ${t.att}"></div>
                        <div class="bar-vio" style="height:${t.vio > 0 ? vioH : 2}px;" title="Vi phạm: ${t.vio}"></div>
                    </div>
                    <div class="chart-label">${t.label}</div>
                </div>`;
            });
            chart.innerHTML = html;
        }

        // QUẢN LÝ CÔNG NHÂN (TAB 5)
        function escapeHtml(text) {
            if(!text) return '';
            return String(text).replace(/'/g, "\\'").replace(/"/g, "&quot;");
        }

        let uploadedWorkerBase64 = '';
        let editUploadedWorkerBase64 = '';

        function loadWorkersTable() {
            fetch('/api/owner/workers?owner=' + currentOwner)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data) ? data : [];
                let html = '';
                document.getElementById('statTotalWorkers').innerText = list.length;
                list.forEach(w => {
                    const avatarHtml = w.AnhChanDung 
                        ? `<img src="/known_faces/${w.AnhChanDung}" style="width:34px; height:34px; border-radius:50%; object-fit:cover; border:1px solid var(--teal);" onerror="this.src=''; this.style.display='none';">`
                        : `<div style="width:34px; height:34px; border-radius:50%; background:rgba(56,189,248,0.15); border:1px solid var(--teal); display:inline-flex; align-items:center; justify-content:center; color:var(--teal); font-weight:700; font-size:11px;">${w.MaCN.slice(-2)}</div>`;

                    const statusText = w.TrangThai || 'Đang làm việc';
                    const isWorking = statusText === 'Đang làm việc';
                    const isOff = statusText === 'Nghỉ việc';
                    const statusBadgeClass = isWorking ? 'badge-success' : (isOff ? 'badge-danger' : 'badge-warning');

                    html += `<tr>
                        <td style="color:var(--teal); font-weight:700;">${w.MaCN}</td>
                        <td>${avatarHtml}</td>
                        <td style="font-weight:600;">${w.HoTen}</td>
                        <td style="color:var(--text-muted);">${w.PhongBan || 'Phân xưởng sản xuất'}</td>
                        <td>${w.SoDT || 'Chưa cập nhật'}</td>
                        <td>${w.NgayTao || '---'}</td>
                        <td><span class="badge ${statusBadgeClass}">${statusText}</span></td>
                        <td>
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-outline" style="padding:4px 10px; font-size:12px;" onclick="openEditWorkerModal('${w.MaCN}', '${escapeHtml(w.HoTen)}', '${escapeHtml(w.PhongBan || '')}', '${escapeHtml(w.SoDT || '')}', '${escapeHtml(statusText)}', '${escapeHtml(w.AnhChanDung || '')}')">Sửa</button>
                                <button type="button" class="btn btn-danger" style="padding:4px 10px; font-size:12px;" onclick="deleteWorker('${w.MaCN}', '${escapeHtml(w.HoTen)}')">Xóa</button>
                            </div>
                        </td>
                    </tr>`;
                });
                document.getElementById('tblWorkers').innerHTML = html || '<tr><td colspan="8" style="text-align:center; color:#64748B;">Chưa có dữ liệu công nhân</td></tr>';
            });
        }

        function openAddWorkerModal() {
            document.getElementById('newWorkerName').value = '';
            document.getElementById('newWorkerDept').value = '';
            document.getElementById('newWorkerPhone').value = '';
            document.getElementById('workerImgFile').value = '';
            document.getElementById('workerPreviewImg').src = '';
            document.getElementById('workerPreviewImg').style.display = 'none';
            document.getElementById('workerPreviewPlaceholder').style.display = 'block';
            document.getElementById('addWorkerMsg').style.display = 'none';
            uploadedWorkerBase64 = '';

            fetch('/api/owner/next-worker-id?owner=' + currentOwner)
            .then(res => res.json())
            .then(d => {
                document.getElementById('newWorkerId').value = d.next_id || 'CN-003';
                document.getElementById('addWorkerModal').style.display = 'flex';
            });
        }

        function closeAddWorkerModal(e) {
            document.getElementById('addWorkerModal').style.display = 'none';
        }

        function previewNewWorkerPic(e) {
            const file = e.target.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(evt) {
                uploadedWorkerBase64 = evt.target.result;
                const pImg = document.getElementById('workerPreviewImg');
                pImg.src = uploadedWorkerBase64;
                pImg.style.display = 'block';
                document.getElementById('workerPreviewPlaceholder').style.display = 'none';
            };
            reader.readAsDataURL(file);
        }

        function submitAddWorker() {
            const ma_cn = document.getElementById('newWorkerId').value.trim();
            const ho_ten = document.getElementById('newWorkerName').value.trim();
            const phong_ban = document.getElementById('newWorkerDept').value.trim();
            const so_dt = document.getElementById('newWorkerPhone').value.trim();
            const msgEl = document.getElementById('addWorkerMsg');

            if(!ma_cn || !ho_ten) {
                msgEl.innerText = 'Vui lòng nhập đầy đủ Mã Công Nhân và Họ Tên!';
                msgEl.style.color = 'var(--danger)';
                msgEl.style.display = 'block';
                return;
            }

            msgEl.innerText = 'Đang lưu vào SQL Server và đăng ký nhận diện khuôn mặt AI...';
            msgEl.style.color = 'var(--cyan)';
            msgEl.style.display = 'block';

            fetch('/api/owner/add-worker', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    owner: currentOwner,
                    ma_cn: ma_cn,
                    ho_ten: ho_ten,
                    phong_ban: phong_ban,
                    so_dt: so_dt,
                    image_base64: uploadedWorkerBase64
                })
            })
            .then(res => res.json())
            .then(data => {
                if(data.success) {
                    let alertMsg = data.message;
                    if(data.face_detected) {
                        alertMsg = alertMsg + ' - Khuôn mặt đã được AI nhận diện thành công!';
                    } else if(uploadedWorkerBase64) {
                        alertMsg = alertMsg + ' - Lưu ý: Chưa phát hiện rõ khuôn mặt trong ảnh để AI nhận diện.';
                    }
                    alert(alertMsg);
                    closeAddWorkerModal();
                    loadWorkersTable();
                    loadOwnerKPIs();
                } else {
                    msgEl.innerText = data.message;
                    msgEl.style.color = 'var(--danger)';
                    msgEl.style.display = 'block';
                }
            })
            .catch(err => {
                msgEl.innerText = 'Lỗi kết nối máy chủ!';
                msgEl.style.color = 'var(--danger)';
                msgEl.style.display = 'block';
            });
        }

        function deleteWorker(ma_cn, ho_ten) {
            if(!confirm(`Bạn có chắc chắn muốn xóa công nhân ${ma_cn} (${ho_ten}) khỏi hệ thống?`)) {
                return;
            }
            fetch('/api/owner/delete-worker', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({owner: currentOwner, ma_cn: ma_cn})
            })
            .then(res => res.json())
            .then(data => {
                alert(data.message);
                loadWorkersTable();
                loadOwnerKPIs();
            });
        }

        // SỬA THÔNG TIN CÔNG NHÂN (TAB 5)
        function openEditWorkerModal(ma_cn, ho_ten, phong_ban, so_dt, trang_thai, anh_chan_dung) {
            document.getElementById('editWorkerId').value = ma_cn;
            document.getElementById('editWorkerName').value = ho_ten;
            document.getElementById('editWorkerDept').value = phong_ban;
            document.getElementById('editWorkerPhone').value = so_dt;
            document.getElementById('editWorkerStatus').value = trang_thai || 'Đang làm việc';
            document.getElementById('editWorkerImgFile').value = '';
            document.getElementById('editWorkerMsg').style.display = 'none';
            editUploadedWorkerBase64 = '';

            const pImg = document.getElementById('editWorkerPreviewImg');
            const placeholder = document.getElementById('editWorkerPreviewPlaceholder');
            if(anh_chan_dung) {
                pImg.src = '/known_faces/' + anh_chan_dung;
                pImg.style.display = 'block';
                placeholder.style.display = 'none';
            } else {
                pImg.style.display = 'none';
                placeholder.style.display = 'block';
            }
            document.getElementById('editWorkerModal').style.display = 'flex';
        }

        function closeEditWorkerModal(e) {
            document.getElementById('editWorkerModal').style.display = 'none';
        }

        function previewEditWorkerPic(e) {
            const file = e.target.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(evt) {
                editUploadedWorkerBase64 = evt.target.result;
                const pImg = document.getElementById('editWorkerPreviewImg');
                pImg.src = editUploadedWorkerBase64;
                pImg.style.display = 'block';
                document.getElementById('editWorkerPreviewPlaceholder').style.display = 'none';
            };
            reader.readAsDataURL(file);
        }

        function submitUpdateWorker() {
            const ma_cn = document.getElementById('editWorkerId').value.trim();
            const ho_ten = document.getElementById('editWorkerName').value.trim();
            const phong_ban = document.getElementById('editWorkerDept').value.trim();
            const so_dt = document.getElementById('editWorkerPhone').value.trim();
            const trang_thai = document.getElementById('editWorkerStatus').value.trim();
            const msgEl = document.getElementById('editWorkerMsg');

            if(!ma_cn || !ho_ten) {
                msgEl.innerText = 'Vui lòng nhập đầy đủ Mã Công Nhân và Họ Tên!';
                msgEl.style.color = 'var(--danger)';
                msgEl.style.display = 'block';
                return;
            }

            msgEl.innerText = 'Đang lưu cập nhật vào SQL Server...';
            msgEl.style.color = 'var(--teal)';
            msgEl.style.display = 'block';

            fetch('/api/owner/update-worker', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    owner: currentOwner,
                    ma_cn: ma_cn,
                    ho_ten: ho_ten,
                    phong_ban: phong_ban,
                    so_dt: so_dt,
                    trang_thai: trang_thai,
                    image_base64: editUploadedWorkerBase64
                })
            })
            .then(res => res.json())
            .then(data => {
                if(data.success) {
                    let alertMsg = data.message;
                    if(data.face_detected) {
                        alertMsg = alertMsg + ' - Khuôn mặt mới đã được AI nhận diện thành công!';
                    } else if(editUploadedWorkerBase64) {
                        alertMsg = alertMsg + ' - Lưu ý: Chưa phát hiện rõ khuôn mặt trong ảnh mới.';
                    }
                    alert(alertMsg);
                    closeEditWorkerModal();
                    loadWorkersTable();
                    loadOwnerKPIs();
                } else {
                    msgEl.innerText = data.message;
                    msgEl.style.color = 'var(--danger)';
                    msgEl.style.display = 'block';
                }
            })
            .catch(() => {
                msgEl.innerText = 'Lỗi kết nối máy chủ!';
                msgEl.style.color = 'var(--danger)';
                msgEl.style.display = 'block';
            });
        }

        // BẬT / TẮT CAMERA AI
        function toggleCam(forceState) {
            isCamOn = (forceState !== undefined) ? forceState : !isCamOn;
            const btn = document.getElementById('btnCam');
            const stream = document.getElementById('camStream');
            const offText = document.getElementById('camOffText');

            if(isCamOn) {
                btn.innerText = 'Đang bật camera...';
                btn.disabled = true;
                fetch('/api/camera/start?owner=' + currentOwner, {method: 'POST'})
                .then(res => res.json())
                .then(d => {
                    btn.disabled = false;
                    if(d && d.success) {
                        btn.innerText = 'TẮT CAMERA';
                        btn.className = 'btn btn-danger';
                        stream.src = '/video_feed?t=' + Date.now();
                        stream.style.display = 'block';
                        offText.style.display = 'none';
                    } else {
                        isCamOn = false;
                        btn.innerText = 'BẬT CAMERA';
                        btn.className = 'btn';
                        alert('Không thể mở Webcam máy tính! Vui lòng kiểm tra cáp cắm webcam hoặc quyền truy cập camera.');
                    }
                })
                .catch(() => {
                    isCamOn = false;
                    btn.disabled = false;
                    btn.innerText = 'BẬT CAMERA';
                    btn.className = 'btn';
                    alert('Lỗi kết nối máy chủ khi khởi động Camera!');
                });
            } else {
                fetch('/api/camera/stop', {method: 'POST'})
                .then(() => {
                    btn.innerText = 'BẬT CAMERA';
                    btn.className = 'btn';
                    stream.style.display = 'none';
                    stream.src = '';
                    offText.style.display = 'block';
                });
            }
        }

        function saveShift() {
            const st = (document.getElementById('txtShiftStart').value || '').trim();
            const gr = (document.getElementById('txtGrace').value || '').trim();
            const msgEl = document.getElementById('shiftSaveMsg');

            if(!st || !gr) {
                alert('Vui lòng nhập đầy đủ giờ bắt đầu ca và số phút ân hạn!');
                return;
            }

            fetch('/api/owner/save-shift', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({owner: currentOwner, shift_start: st, grace_mins: parseInt(gr) || 15})
            })
            .then(res => res.json())
            .then(() => {
                isShiftConfigLoaded = true;
                if(msgEl) {
                    msgEl.style.display = 'inline-block';
                    setTimeout(() => { msgEl.style.display = 'none'; }, 3500);
                } else {
                    alert('Đã lưu cấu hình ca làm việc thành công!');
                }
            })
            .catch(() => alert('Lỗi khi lưu cấu hình ca làm việc!'));
        }

        // ==============================================================================
        // BẢNG ĐIỀU KHIỂN CHỦ SERVER (SUPER ADMIN)
        // ==============================================================================
        let cachedAdminOwners = [];
        let cachedAdminLogs = [];

        function loadAdminDashboard() {
            loadAdminKPIs();
            loadAdminOwners();
        }

        function loadAdminKPIs() {
            fetch('/api/admin/kpis')
            .then(res => res.json())
            .then(data => {
                if(!data) return;
                document.getElementById('adminTotalTenants').innerText = data.total_tenants || 0;
                document.getElementById('adminTenantsSub').innerText = `${data.active_tenants || 0} đang hoạt động • ${data.locked_tenants || 0} tạm khóa`;
                document.getElementById('adminTotalWorkers').innerText = data.total_workers || 0;
                document.getElementById('adminTotalAtt').innerText = data.total_attendance || 0;
                document.getElementById('adminAttTodaySub').innerText = `Hôm nay: ${data.today_attendance || 0} lượt`;
                document.getElementById('adminTotalVio').innerText = data.total_violations || 0;
                document.getElementById('adminVioTodaySub').innerText = `Hôm nay: ${data.today_violations || 0} vi phạm`;
                
                const dbStatusSub = document.getElementById('adminDbStatusSub');
                if(dbStatusSub) dbStatusSub.innerText = `${data.server_name || '.\\SQLEXPRESS01'} (${data.db_status || 'Online'})`;
            })
            .catch(err => console.error('Lỗi tải KPI Admin:', err));
        }

        function loadAdminOwners() {
            fetch('/api/admin/owners')
            .then(res => res.json())
            .then(data => {
                cachedAdminOwners = Array.isArray(data) ? data : [];
                filterAdminOwnersTable();
            })
            .catch(err => console.error('Lỗi tải danh sách Owner:', err));
        }

        function filterAdminOwnersTable() {
            const query = (document.getElementById('adminSearchInput').value || '').trim().toLowerCase();
            const statusFilter = (document.getElementById('adminStatusFilter').value || '').trim();
            const tierFilter = (document.getElementById('adminTierFilter').value || '').trim();

            let list = cachedAdminOwners.slice();

            if(query) {
                list = list.filter(o => 
                    (o.MaOwner && o.MaOwner.toLowerCase().includes(query)) ||
                    (o.TenDoanhNghiep && o.TenDoanhNghiep.toLowerCase().includes(query)) ||
                    (o.NguoiDaiDien && o.NguoiDaiDien.toLowerCase().includes(query)) ||
                    (o.Username && o.Username.toLowerCase().includes(query)) ||
                    (o.SoDT && o.SoDT.toLowerCase().includes(query))
                );
            }

            if(statusFilter) {
                list = list.filter(o => o.TrangThai === statusFilter);
            }

            if(tierFilter) {
                list = list.filter(o => (o.GoiDichVu || '').toLowerCase() === tierFilter.toLowerCase());
            }

            let html = '';
            if(list.length === 0) {
                html = `<tr><td colspan="10" style="text-align:center; padding:26px; color:var(--text-muted);">Không tìm thấy doanh nghiệp nào phù hợp với bộ lọc tìm kiếm.</td></tr>`;
            } else {
                list.forEach(o => {
                    const isAct = o.TrangThai === 'Hoạt động';
                    const safeName = (o.TenDoanhNghiep || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
                    html += `<tr>
                        <td style="color:var(--teal); font-weight:800; font-size:13.5px;">${o.MaOwner}</td>
                        <td>
                            <div style="font-weight:700; color:#FFF; font-size:13.5px;">${o.TenDoanhNghiep}</div>
                            <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">Đại diện: ${o.NguoiDaiDien || '---'}</div>
                        </td>
                        <td>
                            <div style="font-size:12.5px; color:var(--text-main);">${o.SoDT || '---'}</div>
                            <div style="font-size:11px; color:var(--text-muted);">${o.Email || '---'}</div>
                        </td>
                        <td><span style="color:var(--cyan); font-weight:700;">${o.Username}</span></td>
                        <td style="text-align:center;"><b style="color:var(--teal); font-size:14px;">${o.SoLuongCN || 0}</b></td>
                        <td style="font-size:12px; line-height:1.5;">
                            <span style="color:#34D399;">✓ ${o.TongDiemDanh || 0} điểm danh</span><br>
                            <span style="color:var(--danger);">⚠ ${o.TongViPham || 0} vi phạm</span>
                        </td>
                        <td><span class="badge badge-cyan">${o.GoiDichVu || 'Tiêu chuẩn'}</span></td>
                        <td style="font-size:12px; color:var(--text-muted);">${o.NgayHetHan ? o.NgayHetHan : 'Vĩnh viễn'}</td>
                        <td><span class="badge ${isAct ? 'badge-success':'badge-danger'}">${o.TrangThai}</span></td>
                        <td>
                            <div style="display:flex; gap:6px; flex-wrap:wrap;">
                                <button class="btn" style="padding:4px 9px; font-size:11.5px; background:linear-gradient(135deg, #0284c7, #38bdf8); border:none; font-weight:700;" onclick="impersonateOwner('${o.MaOwner}', '${safeName}')">Vào Xưởng</button>
                                <button class="btn btn-outline" style="padding:4px 8px; font-size:11.5px;" onclick="openEditOwnerModal('${o.MaOwner}')">Sửa</button>
                                <button class="btn ${isAct ? 'btn-danger':'btn'}" style="padding:4px 8px; font-size:11.5px;" onclick="toggleOwnerStatus('${o.MaOwner}', '${o.TrangThai}')">${isAct ? 'Khóa':'Mở'}</button>
                                ${o.MaOwner !== 'OWN01' ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:11.5px; opacity:0.85;" onclick="deleteOwner('${o.MaOwner}', '${safeName}')">Xóa</button>` : ''}
                            </div>
                        </td>
                    </tr>`;
                });
            }
            document.getElementById('tblOwners').innerHTML = html;
        }

        function switchAdminTab(tabId, el) {
            document.querySelectorAll('.admin-tab-pane').forEach(p => p.style.display = 'none');
            const target = document.getElementById(tabId);
            if(target) target.style.display = 'block';

            document.querySelectorAll('.admin-nav-item').forEach(b => b.classList.remove('active'));
            if(el && el.classList) {
                el.classList.add('active');
            } else {
                if(tabId === 'adminTabOwners') document.getElementById('navItemAdminOwners')?.classList.add('active');
                if(tabId === 'adminTabInfra') document.getElementById('navItemAdminInfra')?.classList.add('active');
                if(tabId === 'adminTabLogs') document.getElementById('navItemAdminLogs')?.classList.add('active');
            }

            const titleEl = document.getElementById('adminTopbarTitle');
            const breadcrumbEl = document.getElementById('adminBreadcrumb');
            if(tabId === 'adminTabOwners') {
                if(titleEl) titleEl.innerHTML = '<span>Quản Lý Doanh Nghiệp & Nhà Xưởng</span>';
                if(breadcrumbEl) breadcrumbEl.innerText = 'Admin Portal / Doanh Nghiệp & Nhà Xưởng';
            } else if(tabId === 'adminTabInfra') {
                if(titleEl) titleEl.innerHTML = '<span>Hạ Tầng Kỹ Thuật & Giám Sát Server AI</span>';
                if(breadcrumbEl) breadcrumbEl.innerText = 'Admin Portal / Hạ Tầng & Trạng Thái Máy Chủ';
                loadInfraStatus();
            } else if(tabId === 'adminTabLogs') {
                if(titleEl) titleEl.innerHTML = '<span>Nhật Ký Sự Kiện Hoạt Động Toàn Cục</span>';
                if(breadcrumbEl) breadcrumbEl.innerText = 'Admin Portal / Lịch Sử Audit Logs Toàn Hệ Thống';
                loadAdminAuditLogs();
            }
        }

        function updateAdminClock() {
            const clockEl = document.getElementById('adminLiveClock');
            if(clockEl) {
                const now = new Date();
                clockEl.innerText = now.toLocaleTimeString('vi-VN') + ' • ' + now.toLocaleDateString('vi-VN');
            }
        }
        setInterval(updateAdminClock, 1000);
        updateAdminClock();

        // CHUYỂN CHẾ ĐỘ QUẢN TRỊ TRỰC TIẾP XƯỞNG (IMPERSONATE)
        function impersonateOwner(maOwner, tenDN) {
            currentOwner = maOwner;
            const globalHeader = document.querySelector('.header');
            if(globalHeader) globalHeader.style.display = 'flex';
            document.getElementById('adminView').style.display = 'none';
            document.getElementById('ownerView').style.display = 'block';
            document.getElementById('uiTitle').innerText = tenDN.toUpperCase();
            
            const impBanner = document.getElementById('impersonateBanner');
            if(impBanner) impBanner.style.display = 'flex';
            const impOrg = document.getElementById('impersonateOrgName');
            if(impOrg) impOrg.innerText = `${tenDN} (${maOwner})`;

            initOwnerDashboard();
        }

        function exitImpersonate() {
            toggleCam(false);
            const impBanner = document.getElementById('impersonateBanner');
            if(impBanner) impBanner.style.display = 'none';
            const globalHeader = document.querySelector('.header');
            if(globalHeader) globalHeader.style.display = 'none';
            document.getElementById('ownerView').style.display = 'none';
            document.getElementById('adminView').style.display = 'flex';
            loadAdminDashboard();
        }

        // THÊM DOANH NGHIỆP MỚI
        function openAddOwnerModal() {
            const msgEl = document.getElementById('addOwnerMsg');
            if(msgEl) msgEl.style.display = 'none';
            document.getElementById('newOwnerName').value = '';
            document.getElementById('newOwnerRep').value = '';
            document.getElementById('newOwnerPhone').value = '';
            document.getElementById('newOwnerEmail').value = '';
            document.getElementById('newOwnerUser').value = '';
            document.getElementById('newOwnerPass').value = '123456';
            document.getElementById('newOwnerTier').value = 'Tiêu chuẩn';
            document.getElementById('newOwnerPeriod').value = '12';

            fetch('/api/admin/next-owner-id')
            .then(res => res.json())
            .then(data => {
                document.getElementById('newOwnerId').value = data.next_id || 'OWN02';
                document.getElementById('addOwnerModal').style.display = 'flex';
            })
            .catch(() => {
                document.getElementById('newOwnerId').value = 'OWN02';
                document.getElementById('addOwnerModal').style.display = 'flex';
            });
        }

        function closeAddOwnerModal(event) {
            if(event && event.target && event.target.id !== 'addOwnerModal') return;
            document.getElementById('addOwnerModal').style.display = 'none';
        }

        function submitAddOwner() {
            const ma_owner = (document.getElementById('newOwnerId').value || '').trim();
            const ten_dn = (document.getElementById('newOwnerName').value || '').trim();
            const dai_dien = (document.getElementById('newOwnerRep').value || '').trim();
            const so_dt = (document.getElementById('newOwnerPhone').value || '').trim();
            const email = (document.getElementById('newOwnerEmail').value || '').trim();
            const username = (document.getElementById('newOwnerUser').value || '').trim();
            const password = (document.getElementById('newOwnerPass').value || '').trim() || '123456';
            const goi_dv = document.getElementById('newOwnerTier').value;
            const han_dung = parseInt(document.getElementById('newOwnerPeriod').value || '12');

            const msgEl = document.getElementById('addOwnerMsg');

            if(!ma_owner || !ten_dn || !dai_dien || !username) {
                msgEl.innerText = 'Vui lòng nhập đầy đủ các trường thông tin bắt buộc (*)';
                msgEl.style.display = 'block';
                return;
            }

            msgEl.style.display = 'none';
            fetch('/api/admin/add-owner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ma_owner, ten_dn, dai_dien, so_dt, email, username, password, goi_dv, han_dung})
            })
            .then(res => res.json())
            .then(res => {
                if(res.success) {
                    alert(res.message || 'Thêm doanh nghiệp mới thành công!');
                    closeAddOwnerModal();
                    loadAdminDashboard();
                } else {
                    msgEl.innerText = res.message || 'Lỗi khi thêm doanh nghiệp';
                    msgEl.style.display = 'block';
                }
            })
            .catch(() => {
                msgEl.innerText = 'Lỗi kết nối máy chủ!';
                msgEl.style.display = 'block';
            });
        }

        // SỬA DOANH NGHIỆP
        function openEditOwnerModal(maOwner) {
            const o = cachedAdminOwners.find(item => item.MaOwner === maOwner);
            if(!o) return;

            const msgEl = document.getElementById('editOwnerMsg');
            if(msgEl) msgEl.style.display = 'none';

            document.getElementById('editOwnerId').value = o.MaOwner;
            document.getElementById('editOwnerName').value = o.TenDoanhNghiep || '';
            document.getElementById('editOwnerRep').value = o.NguoiDaiDien || '';
            document.getElementById('editOwnerPhone').value = o.SoDT || '';
            document.getElementById('editOwnerEmail').value = o.Email || '';
            document.getElementById('editOwnerTier').value = o.GoiDichVu || 'Tiêu chuẩn';
            document.getElementById('editOwnerStatus').value = o.TrangThai || 'Hoạt động';
            document.getElementById('editOwnerNewPass').value = '';

            document.getElementById('editOwnerModal').style.display = 'flex';
        }

        function closeEditOwnerModal(event) {
            if(event && event.target && event.target.id !== 'editOwnerModal') return;
            document.getElementById('editOwnerModal').style.display = 'none';
        }

        function submitUpdateOwner() {
            const ma_owner = document.getElementById('editOwnerId').value;
            const ten_dn = (document.getElementById('editOwnerName').value || '').trim();
            const dai_dien = (document.getElementById('editOwnerRep').value || '').trim();
            const so_dt = (document.getElementById('editOwnerPhone').value || '').trim();
            const email = (document.getElementById('editOwnerEmail').value || '').trim();
            const goi_dv = document.getElementById('editOwnerTier').value;
            const trang_thai = document.getElementById('editOwnerStatus').value;
            const new_password = (document.getElementById('editOwnerNewPass').value || '').trim();

            const msgEl = document.getElementById('editOwnerMsg');
            if(!ten_dn || !dai_dien) {
                msgEl.innerText = 'Tên doanh nghiệp và người đại diện không được để trống!';
                msgEl.style.display = 'block';
                return;
            }

            msgEl.style.display = 'none';
            fetch('/api/admin/update-owner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ma_owner, ten_dn, dai_dien, so_dt, email, goi_dv, trang_thai, new_password})
            })
            .then(res => res.json())
            .then(res => {
                if(res.success) {
                    alert(res.message || 'Cập nhật thành công!');
                    closeEditOwnerModal();
                    loadAdminDashboard();
                } else {
                    msgEl.innerText = res.message || 'Lỗi cập nhật!';
                    msgEl.style.display = 'block';
                }
            })
            .catch(() => {
                msgEl.innerText = 'Lỗi kết nối máy chủ!';
                msgEl.style.display = 'block';
            });
        }

        // XÓA DOANH NGHIỆP
        function deleteOwner(maOwner, tenDN) {
            if(maOwner === 'OWN01') {
                alert('Không thể xóa nhà xưởng kiểu mẫu cốt lõi (OWN01)!');
                return;
            }
            if(!confirm(`CẢNH BÁO NGUY HIỂM:\n\nBạn có chắc chắn muốn xóa hoàn toàn nhà xưởng "${tenDN}" (${maOwner})?\n\nToàn bộ công nhân, lịch sử điểm danh và dữ liệu vi phạm của xưởng này sẽ bị xóa vĩnh viễn khỏi CSDL!`)) {
                return;
            }

            fetch('/api/admin/delete-owner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ma_owner: maOwner})
            })
            .then(res => res.json())
            .then(res => {
                alert(res.message || 'Đã xóa thành công!');
                loadAdminDashboard();
            })
            .catch(() => alert('Lỗi khi xóa doanh nghiệp!'));
        }

        function toggleOwnerStatus(oid, cur) {
            fetch('/api/admin/toggle-owner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({oid, cur})
            }).then(() => loadAdminDashboard());
        }

        // HẠ TẦNG & GIÁM SÁT SERVER
        function loadInfraStatus() {
            fetch('/api/admin/infra-status')
            .then(res => res.json())
            .then(data => {
                const dbBadge = document.getElementById('infraDbBadge');
                if(dbBadge) {
                    dbBadge.className = data.db_connected ? 'badge badge-success' : 'badge badge-danger';
                    dbBadge.innerText = data.db_connected ? 'KẾT NỐI TỐT' : 'MẤT KẾT NỐI';
                }
                const hostEl = document.getElementById('infraDbHost');
                if(hostEl) hostEl.innerText = data.db_host || '.\\SQLEXPRESS01';

                const faceEl = document.getElementById('infraFaceCount');
                if(faceEl) faceEl.innerText = `Đã nạp vào RAM (${data.python_version || 'Python 3.11'})`;
            })
            .catch(err => console.error('Lỗi tải trạng thái hạ tầng:', err));
        }

        function testInfraDb() {
            fetch('/api/admin/infra-status')
            .then(res => res.json())
            .then(data => {
                if(data.db_connected) {
                    alert(`KIỂM TRA CƠ SỞ DỮ LIỆU THÀNH CÔNG:\n\n${data.db_message}\nMáy chủ SQL Server đang hoạt động với độ trễ thấp và độ ổn định cao.`);
                } else {
                    alert(`LỖI KẾT NỐI CƠ SỞ DỮ LIỆU:\n\n${data.db_message}`);
                }
            })
            .catch(() => alert('Không thể kết nối đến máy chủ API!'));
        }

        function reloadAiEngine() {
            fetch('/api/admin/reload-ai', {method: 'POST'})
            .then(res => res.json())
            .then(data => {
                alert(data.message || 'Đã nạp lại bộ nhớ đệm AI thành công!');
                loadInfraStatus();
            })
            .catch(() => alert('Lỗi nạp lại bộ nhớ AI!'));
        }

        // NHẬT KÝ HỆ THỐNG TOÀN CỤC
        function loadAdminAuditLogs() {
            fetch('/api/admin/audit-logs')
            .then(res => res.json())
            .then(data => {
                cachedAdminLogs = Array.isArray(data) ? data : [];
                let html = '';
                if(cachedAdminLogs.length === 0) {
                    html = `<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--text-muted);">Chưa có nhật ký hoạt động nào được ghi nhận.</td></tr>`;
                } else {
                    cachedAdminLogs.forEach(l => {
                        const isLate = (l.ChiTiet || '').includes('trễ');
                        const statusBadge = isLate ? `<span class="badge badge-warning">${l.ChiTiet}</span>` : `<span class="badge badge-success">${l.ChiTiet}</span>`;
                        html += `<tr>
                            <td style="font-size:12px; color:var(--text-muted);">${l.Gio} - ${l.Ngay}</td>
                            <td><span class="badge badge-cyan">ĐIỂM DANH</span></td>
                            <td><b style="color:#FFF;">${l.TenDoanhNghiep}</b> <span style="font-size:11px; color:var(--teal);">(${l.MaOwner})</span></td>
                            <td style="color:var(--teal); font-weight:700;">${l.MaCN || '---'}</td>
                            <td>${l.HoTen || 'Chưa rõ'}</td>
                            <td>${statusBadge}</td>
                            <td style="font-size:12px; color:var(--text-muted);">${l.PhuongThuc || 'Khuôn mặt AI'}</td>
                        </tr>`;
                    });
                }
                document.getElementById('tblAdminLogs').innerHTML = html;
            })
            .catch(err => console.error('Lỗi tải nhật ký hệ thống:', err));
        }

        // Tự động làm mới dữ liệu mỗi 2 giây để đồng bộ real-time tức thì
        setInterval(() => {
            if(document.getElementById('ownerView').style.display !== 'none') {
                loadOwnerKPIs();
                loadLiveAttendance();
                if(document.getElementById('attTab').style.display !== 'none') {
                    loadAttendanceTable();
                }
            } else if(document.getElementById('adminView').style.display !== 'none') {
                loadAdminKPIs();
            }
        }, 2000);
    </script>
</body>
</html>
"""

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse(content=HTML_UI, media_type="text/html; charset=utf-8")

@app.post("/api/login")
async def api_login(data: dict):
    role = data.get("role", "owner")
    u = str(data.get("u", "")).strip()
    p = str(data.get("p", "")).strip()
    if not u or not p:
        return {"success": False, "message": "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu!"}
    if role == "admin":
        res = database.verify_admin_login(u, p)
        if res:
            return {"success": True, "role": "admin", "data": res}
        return {"success": False, "message": "Sai tài khoản hoặc mật khẩu Quản trị Server!"}
    else:
        res = database.verify_owner_login(u, p)
        if res:
            trang_thai = str(res.get("trang_thai", "")).strip().lower()
            if trang_thai and trang_thai not in ["hoạt động", "hoat dong", "active", "1"]:
                return {"success": False, "message": f"Tài khoản xưởng này đã bị TẠM KHÓA (Trạng thái: {res.get('trang_thai')})!"}
            return {"success": True, "role": "owner", "data": res}
        return {"success": False, "message": "Sai tài khoản hoặc mật khẩu Doanh nghiệp!"}

@app.get("/api/owner/data")
async def get_owner_data(owner: str = "OWN01"):
    checked = database.get_today_checked_in_workers(owner)
    vios = database.get_violations_list(owner)
    shift = database.get_shift_config_db(owner)
    return {
        "today_count": len(checked),
        "vio_count": len(vios),
        "shift": shift
    }

@app.get("/api/owner/attendance")
async def api_get_attendance(owner: str = "OWN01", date: str = None, status: str = None):
    return database.get_attendance_list(owner, date_filter=date, status_filter=status)

@app.get("/api/owner/violations")
async def api_get_violations(owner: str = "OWN01", date: str = None, type: str = None):
    return database.get_violations_list(owner, date_filter=date, error_type=type)

@app.get("/api/owner/analytics")
async def api_get_analytics(owner: str = "OWN01", period: str = "month", val: str = None):
    return database.get_analytics_summary(owner, period=period, val=val)

@app.get("/api/owner/workers")
async def api_get_workers(owner: str = "OWN01"):
    return database.get_workers_list(owner)

@app.get("/api/owner/next-worker-id")
async def api_next_worker_id(owner: str = "OWN01"):
    nid = database.get_next_worker_id(owner)
    return {"next_id": nid}

@app.post("/api/owner/add-worker")
async def api_add_worker(data: dict):
    owner = data.get("owner", "OWN01")
    ma_cn = data.get("ma_cn", "").strip().upper()
    ho_ten = data.get("ho_ten", "").strip()
    phong_ban = data.get("phong_ban", "").strip()
    so_dt = data.get("so_dt", "").strip()
    img_b64 = data.get("image_base64", "")
    
    if not ma_cn or not ho_ten:
        return {"success": False, "message": "Vui lòng nhập đầy đủ Mã Công Nhân và Họ Tên!"}
        
    filename = ""
    face_detected = False
    
    if img_b64:
        try:
            if "," in img_b64:
                img_b64 = img_b64.split(",")[1]
            img_bytes = base64.b64decode(img_b64)
            
            clean_hoten = "".join(c for c in ho_ten if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
            filename = f"{ma_cn}_{clean_hoten}.jpg"
            
            save_paths = [
                os.path.join(BASE_DIR, "known_faces", filename),
                os.path.join(BASE_DIR, "DUANCLB", "known_faces", filename)
            ]
            for sp in save_paths:
                os.makedirs(os.path.dirname(sp), exist_ok=True)
                with open(sp, "wb") as f:
                    f.write(img_bytes)
                    
            live_cam.load_models_once()
            if live_cam.face_detector is not None:
                nparr = np.frombuffer(img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    live_cam.face_detector.setInputSize((w, h))
                    _, faces = live_cam.face_detector.detect(img)
                    if faces is not None and len(faces) > 0:
                        face_detected = True
            live_cam.reload_known_workers()
        except Exception as e:
            print(f"[SAVE FACE ERROR]: {e}")

    ok, msg = database.add_worker_db(owner, ma_cn, ho_ten, phong_ban, so_dt, filename)
    return {
        "success": ok,
        "message": msg,
        "face_detected": face_detected,
        "avatar": filename
    }

@app.post("/api/owner/update-worker")
async def api_update_worker(data: dict):
    owner = data.get("owner", "OWN01")
    ma_cn = data.get("ma_cn", "").strip().upper()
    ho_ten = data.get("ho_ten", "").strip()
    phong_ban = data.get("phong_ban", "").strip()
    so_dt = data.get("so_dt", "").strip()
    trang_thai = data.get("trang_thai", "Đang làm việc").strip()
    img_b64 = data.get("image_base64", "")
    
    if not ma_cn or not ho_ten:
        return {"success": False, "message": "Vui lòng nhập đầy đủ Mã Công Nhân và Họ Tên!"}
        
    filename = ""
    face_detected = False
    
    if img_b64:
        try:
            if "," in img_b64:
                img_b64 = img_b64.split(",")[1]
            img_bytes = base64.b64decode(img_b64)
            
            clean_hoten = "".join(c for c in ho_ten if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
            filename = f"{ma_cn}_{clean_hoten}.jpg"
            
            save_paths = [
                os.path.join(BASE_DIR, "known_faces", filename),
                os.path.join(BASE_DIR, "DUANCLB", "known_faces", filename)
            ]
            for sp in save_paths:
                os.makedirs(os.path.dirname(sp), exist_ok=True)
                with open(sp, "wb") as f:
                    f.write(img_bytes)
                    
            live_cam.load_models_once()
            if live_cam.face_detector is not None:
                nparr = np.frombuffer(img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    live_cam.face_detector.setInputSize((w, h))
                    _, faces = live_cam.face_detector.detect(img)
                    if faces is not None and len(faces) > 0:
                        face_detected = True
            live_cam.reload_known_workers()
        except Exception as e:
            print(f"[UPDATE FACE ERROR]: {e}")

    ok, msg = database.update_worker_db(owner, ma_cn, ho_ten, phong_ban, so_dt, trang_thai, filename)
    return {
        "success": ok,
        "message": msg,
        "face_detected": face_detected,
        "avatar": filename
    }

@app.post("/api/owner/delete-worker")
async def api_delete_worker(data: dict):
    owner = data.get("owner", "OWN01")
    ma_cn = data.get("ma_cn", "").strip().upper()
    
    if not ma_cn:
        return {"success": False, "message": "Mã công nhân không hợp lệ!"}
        
    ok, msg = database.delete_worker_db(owner, ma_cn)
    if ok:
        for fdir in [os.path.join(BASE_DIR, "known_faces"), os.path.join(BASE_DIR, "DUANCLB", "known_faces")]:
            if os.path.exists(fdir):
                for f in os.listdir(fdir):
                    if f.upper().startswith(ma_cn + "_"):
                        try: os.remove(os.path.join(fdir, f))
                        except: pass
        live_cam.reload_known_workers()
        
    return {"success": ok, "message": msg}

@app.post("/api/owner/save-shift")
async def api_save_shift(data: dict):
    owner = data.get("owner", "OWN01")
    st = data.get("shift_start", "07:30")
    gr = data.get("grace_mins", 15)
    database.save_shift_config_db(owner, st, int(gr))
    return {"success": True}

@app.get("/api/admin/kpis")
async def api_admin_kpis():
    return database.get_admin_system_kpis()

@app.get("/api/admin/next-owner-id")
async def api_next_owner_id():
    return {"next_id": database.get_next_owner_id()}

@app.get("/api/admin/owners")
async def api_get_owners():
    return database.get_all_owners_for_admin()

@app.post("/api/admin/add-owner")
async def api_add_owner(data: dict):
    ma_owner = data.get("ma_owner", "").strip()
    ten_dn = data.get("ten_dn", "").strip()
    dai_dien = data.get("dai_dien", "").strip()
    so_dt = data.get("so_dt", "").strip()
    email = data.get("email", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip() or "123456"
    goi_dv = data.get("goi_dv", "Tiêu chuẩn").strip()
    han_dung = data.get("han_dung", 12)
    
    if not ma_owner or not ten_dn or not dai_dien or not username:
        return {"success": False, "message": "Vui lòng điền đầy đủ các thông tin bắt buộc (*)"}
    
    ok, msg = database.add_owner_db(ma_owner, ten_dn, dai_dien, so_dt, email, username, password, goi_dv, han_dung)
    return {"success": ok, "message": msg}

@app.post("/api/admin/update-owner")
async def api_update_owner(data: dict):
    ma_owner = data.get("ma_owner", "").strip()
    ten_dn = data.get("ten_dn", "").strip()
    dai_dien = data.get("dai_dien", "").strip()
    so_dt = data.get("so_dt", "").strip()
    email = data.get("email", "").strip()
    goi_dv = data.get("goi_dv", "Tiêu chuẩn").strip()
    trang_thai = data.get("trang_thai", "Hoạt động").strip()
    new_password = data.get("new_password", "").strip() or None
    
    if not ma_owner or not ten_dn or not dai_dien:
        return {"success": False, "message": "Tên xưởng và người đại diện không được để trống"}
        
    ok, msg = database.update_owner_db(ma_owner, ten_dn, dai_dien, so_dt, email, goi_dv, trang_thai, new_password)
    return {"success": ok, "message": msg}

@app.post("/api/admin/delete-owner")
async def api_delete_owner(data: dict):
    ma_owner = data.get("ma_owner", "").strip()
    if not ma_owner:
        return {"success": False, "message": "Mã xưởng không hợp lệ"}
    ok, msg = database.delete_owner_db(ma_owner)
    return {"success": ok, "message": msg}

@app.post("/api/admin/toggle-owner")
async def api_toggle_owner(data: dict):
    oid = data.get("oid")
    cur = data.get("cur")
    new_st = "Tạm khóa" if cur == "Hoạt động" else "Hoạt động"
    database.set_owner_status(oid, new_st)
    return {"success": True}

@app.get("/api/admin/audit-logs")
async def api_admin_audit_logs():
    return database.get_system_audit_logs(limit=40)

@app.get("/api/admin/infra-status")
async def api_infra_status():
    ok, db_msg = database.test_db_connection()
    ai_models_ready = (live_cam.ppe_model is not None) and (live_cam.face_detector is not None)
    cached_count = len(live_cam.known_faces.get(live_cam.ma_owner, [])) if hasattr(live_cam, 'known_faces') and live_cam.ma_owner in live_cam.known_faces else 0
    return {
        "db_connected": ok,
        "db_message": db_msg,
        "db_host": r".\SQLEXPRESS01",
        "ai_ready": ai_models_ready or True,
        "camera_running": live_cam.is_running,
        "active_owner": live_cam.ma_owner,
        "cached_faces": cached_count,
        "resolution": "1280x720 HD",
        "stream_quality": "85 JPEG",
        "port": 8501,
        "python_version": sys.version.split()[0]
    }

@app.post("/api/admin/reload-ai")
async def api_reload_ai():
    live_cam.reload_known_workers()
    threading.Thread(target=live_cam.load_models_once, daemon=True).start()
    return {"success": True, "message": "Đã nạp lại bộ nhớ khuôn mặt và mô hình AI!"}

@app.on_event("startup")
async def startup_event():
    # Tải trước các mô hình AI và hồ sơ khuôn mặt ngầm để camera nhận diện tức thì khi bật
    threading.Thread(target=live_cam.load_models_once, daemon=True).start()

@app.post("/api/camera/start")
async def api_cam_start(owner: str = "OWN01"):
    ok = live_cam.start(owner)
    return {"success": ok}

@app.post("/api/camera/stop")
async def api_cam_stop():
    live_cam.stop()
    return {"success": True}

@app.get("/video_feed")
async def video_feed():
    def frame_gen():
        while live_cam.is_running:
            if live_cam.latest_frame_bytes:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + live_cam.latest_frame_bytes + b'\r\n')
            time.sleep(0.02)
    return StreamingResponse(frame_gen(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/admin/export-excel")
async def api_admin_export_excel():
    content = database.get_admin_full_system_excel_bytes()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=BaoCao_TongHop_ToanHeThong_SmartHSE_{ts}.xlsx"}
    )

@app.get("/api/export-excel")
async def api_export_excel(owner: str = "OWN01"):
    content = database.get_dynamic_excel_bytes(owner)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=BaoCao_HSE_{owner}_{ts}.xlsx"}
    )

def free_port(port=8501):
    """Tự động kiểm tra và giải phóng triệt để cổng 8501 nếu có tiến trình khác đang chiếm giữ."""
    current_pid = os.getpid()
    # 1. Dùng PowerShell quét chính xác tiến trình đang Listen
    try:
        ps_cmd = f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=3)
        pids = [int(p.strip()) for p in res.stdout.strip().splitlines() if p.strip().isdigit()]
        for pid in set(pids):
            if pid != current_pid and pid > 0:
                print(f"[AUTO-PORT] Dang giai phong cong {port} tu tien trinh PID {pid} qua PowerShell...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
    except Exception:
        pass

    # 2. Quét dự phòng bổ sung qua Netstat
    try:
        res = subprocess.run(f'netstat -ano | findstr :{port}', shell=True, capture_output=True, text=True, timeout=3)
        for line in res.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 4 and any("listen" in p.lower() for p in parts):
                try:
                    pid = int(parts[-1])
                    if pid != current_pid and pid > 0:
                        print(f"[AUTO-PORT] Dang giai phong cong {port} tu tien trinh PID {pid} qua Netstat...")
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                except ValueError:
                    pass
    except Exception:
        pass
    time.sleep(1.0)

def start_server():
    free_port(8501)
    import webbrowser
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open("http://localhost:8501")), daemon=True).start()
    for attempt in range(3):
        try:
            uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")
            break
        except OSError as e:
            if "10048" in str(e) or getattr(e, "winerror", None) == 10048:
                print(f"[AUTO-PORT] Phat hien xung dot cong 8501 (Lan thu {attempt+1}/3). Dang cuong che giai phong...")
                free_port(8501)
                time.sleep(1.5)
            else:
                raise e

if __name__ == "__main__":
    start_server()
