import os
import sys
import time
import io
import cv2
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from PIL import Image, ImageOps
import streamlit as st

# Cấu hình giao diện Streamlit rộng rãi và chuyên nghiệp
st.set_page_config(
 page_title="Smart HSE Vision - Dashboard",
 page_icon="",
 layout="wide",
 initial_sidebar_state="expanded"
)

# ------------------------------------------------------------------------------
# LUXURY ENTERPRISE UI STYLING - AURORA EMERALD & OCEAN TEAL GRADIENT
# ------------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
 font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Sidebar Gradient Styling */
[data-testid="stSidebar"] {
 background: linear-gradient(180deg, #041615 0%, #062220 50%, #020F0E 100%) !important;
 border-right: 1px solid rgba(0, 255, 163, 0.15) !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] {
 gap: 6px;
}

/* Radio button converted to luxury gradient nav tiles */
[data-testid="stSidebar"] div[role="radiogroup"] > label {
 background: rgba(0, 255, 163, 0.03) !important;
 border: 1px solid rgba(0, 255, 163, 0.08) !important;
 padding: 10px 14px !important;
 border-radius: 10px !important;
 margin-bottom: 4px !important;
 transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
 cursor: pointer !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
 background: rgba(0, 255, 163, 0.12) !important;
 border-color: rgba(0, 255, 163, 0.4) !important;
 transform: translateX(4px);
}

[data-testid="stSidebar"] div[role="radiogroup"] > label[data-checked="true"],
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
 background: linear-gradient(135deg, rgba(0, 242, 254, 0.22) 0%, rgba(0, 255, 163, 0.32) 100%) !important;
 border: 1px solid #00FFA3 !important;
 box-shadow: 0 4px 20px rgba(0, 255, 163, 0.28) !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {
 display: none !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label p {
 font-weight: 600 !important;
 font-size: 13.5px !important;
 color: #F1FDFB !important;
}

/* Metric Cards Gradient Styling */
[data-testid="stMetric"] {
 background: linear-gradient(135deg, rgba(4, 26, 24, 0.6) 0%, rgba(2, 18, 16, 0.8) 100%) !important;
 border: 1px solid rgba(0, 255, 163, 0.18) !important;
 border-radius: 12px !important;
 padding: 12px 18px !important;
 box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2) !important;
}

[data-testid="stMetricLabel"] p {
 font-size: 13px !important;
 font-weight: 600 !important;
 color: #81E6D9 !important;
}

[data-testid="stMetricValue"] div {
 font-size: 28px !important;
 font-weight: 800 !important;
 color: #E6FFFA !important;
}

/* Buttons */
.stButton > button {
 border-radius: 10px !important;
 font-weight: 600 !important;
 transition: all 0.25s ease !important;
}

.stButton > button:hover {
 transform: translateY(-2px);
}

.stButton > button[kind="primary"] {
 background: linear-gradient(135deg, #00E676 0%, #00BFA5 50%, #00897B 100%) !important;
 color: #041615 !important;
 font-weight: 700 !important;
 border: 1px solid #00FFA3 !important;
 box-shadow: 0 4px 18px rgba(0, 255, 163, 0.3) !important;
}

.stButton > button[kind="primary"]:hover {
 box-shadow: 0 6px 24px rgba(0, 255, 163, 0.5) !important;
 transform: translateY(-2px);
}

.stDownloadButton > button {
 background: linear-gradient(135deg, #00C896 0%, #008E76 50%, #005B4C 100%) !important;
 color: white !important;
 border: 1px solid rgba(0, 255, 163, 0.5) !important;
 border-radius: 10px !important;
 font-weight: 600 !important;
 box-shadow: 0 4px 18px rgba(0, 255, 163, 0.25) !important;
 transition: all 0.25s ease !important;
}

.stDownloadButton > button:hover {
 transform: translateY(-2px) !important;
 box-shadow: 0 6px 24px rgba(0, 255, 163, 0.45) !important;
}
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# 1. ĐƯỜNG DẪN DỮ LIỆU & IMPORT MÔ HÌNH
# ------------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
FACES_DIR = os.path.join(BASE_DIR, "known_faces")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
VIOLATIONS_DIR = os.path.join(BASE_DIR, "violations")
EXCEL_PATH = os.path.join(LOGS_DIR, "Smart_HSE_Bao_Cao.xlsx")

os.makedirs(FACES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(VIOLATIONS_DIR, exist_ok=True)

sys.path.append(BASE_DIR)
import DUANCLB
import database

# Cache mô hình AI trong bộ nhớ Streamlit
@st.cache_resource
def load_all_ai_models():
 return DUANCLB.init_models()

# ------------------------------------------------------------------------------
# CACHED DATA & RESOURCE HANDLERS (TỐI ƯU TỐC ĐỘ PHẢN HỒI TỨC THÌ 0.05S)
# ------------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def get_cached_db_status():
 return database.test_db_connection()

@st.cache_data(ttl=15, show_spinner=False)
def get_cached_excel_bytes():
 return database.get_dynamic_excel_bytes()

@st.cache_data(ttl=4, show_spinner=False)
def get_cached_attendance_df():
 return database.get_attendance_df()

@st.cache_data(ttl=4, show_spinner=False)
def get_cached_violations_df():
 return database.get_violations_df()

@st.cache_data(ttl=3, show_spinner=False)
def get_cached_today_checked_in():
 return database.get_today_checked_in_workers()

# ------------------------------------------------------------------------------
# 2. GIAO DIỆN THANH BÊN (SIDEBAR NAVIGATION)
# ------------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
 """
 <div style="text-align: center; padding: 10px 0 16px 0;">
 <div style="display: inline-flex; align-items: center; justify-content: center; width: 48px; height: 48px; background: linear-gradient(135deg, rgba(0, 242, 254, 0.25) 0%, rgba(0, 255, 163, 0.3) 100%); border: 1px solid rgba(0, 255, 163, 0.5); border-radius: 14px; margin-bottom: 10px; box-shadow: 0 4px 20px rgba(0, 255, 163, 0.25);">
 <span style="font-size: 26px;"></span>
 </div>
 <div style="font-size: 20px; font-weight: 800; background: linear-gradient(135deg, #00F2FE 0%, #00D2B4 50%, #00FFA3 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: 0.8px;">
 SMART HSE VISION
 </div>
 <div style="font-size: 11.5px; color: #81E6D9; font-weight: 500; margin-top: 3px;">
 Hệ Thống Điểm Danh & Giám Sát An Toàn
 </div>
 </div>
 """,
 unsafe_allow_html=True
 )
 
 # Trạng thái kết nối SQL Server dạng Gradient Pill Badge (Cached 30s)
 db_ok, _ = get_cached_db_status()
    if db_ok:
    st.markdown(
 """
 <div style="background: linear-gradient(135deg, rgba(0, 255, 163, 0.15) 0%, rgba(0, 210, 180, 0.22) 100%); border: 1px solid rgba(0, 255, 163, 0.45); border-radius: 10px; padding: 8px 12px; text-align: center; margin-bottom: 16px; box-shadow: 0 2px 12px rgba(0, 255, 163, 0.15);">
 <span style="color: #00FFA3; font-weight: 700; font-size: 12.5px; letter-spacing: 0.3px;"> SQL Server: Đã kết nối</span>
 </div>
 """,
 unsafe_allow_html=True
 )
    else:
    st.markdown(
 """
 <div style="background: linear-gradient(135deg, rgba(245, 158, 11, 0.12) 0%, rgba(217, 119, 6, 0.2) 100%); border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 10px; padding: 8px 12px; text-align: center; margin-bottom: 16px;">
 <span style="color: #FBBF24; font-weight: 600; font-size: 12.5px;"> SQL Server: Chưa kết nối</span>
 </div>
 """,
 unsafe_allow_html=True
 )
 
    st.markdown(
 """
 <div style="font-size: 12px; font-weight: 700; color: #81E6D9; text-transform: uppercase; letter-spacing: 0.8px; margin: 12px 0 8px 4px;">
 Phân Hệ Chức Năng
 </div>
 """,
 unsafe_allow_html=True
 )
 
 menu = st.radio(
 label="Chọn phân hệ:",
 options=[
 " Giám Sát Camera AI",
 " Nhật Ký Điểm Danh",
 " Bằng Chứng Vi Phạm",
 " Thống Kê & Báo Cáo",
 " Quản Lý Công Nhân",
 ],
 label_visibility="collapsed",
 index=0
 )
 
 # Tự động tắt camera khi chuyển sang menu khác để tắt hoàn toàn phần cứng camera
    if menu != " Giám Sát Camera AI":
    if st.session_state.get("camera_running", False):
    st.session_state.camera_running = False
 
    st.markdown("---")
    st.markdown(
 """
 <div style="font-size: 12px; font-weight: 700; color: #81E6D9; text-transform: uppercase; letter-spacing: 0.8px; margin: 16px 0 6px 4px;">
 Xuất Báo Cáo
 </div>
 <div style="font-size: 12px; color: #4E878C; margin-bottom: 10px; margin-left: 4px;">
 Trích xuất trực tiếp dữ liệu từ SQL Server sang Excel:
 </div>
 """,
 unsafe_allow_html=True
 )
 
 try:
 excel_bytes = get_cached_excel_bytes()
    st.download_button(
 label=" Tải Báo Cáo Excel (Live)",
 data=excel_bytes,
 file_name=f"Smart_HSE_BaoCao_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
 mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
 use_container_width=True
 )
 except Exception as e:
    st.caption(f"Trạng thái Excel: {e}")
 
    st.markdown(
 """
 <div style="text-align: center; color: #4E878C; font-size: 11px; margin-top: 25px;">
 Smart HSE Enterprise v2.0<br>
 © 2026 AI Vision Safety Tech
 </div>
 """,
 unsafe_allow_html=True
 )

# ==============================================================================
# TRANG 1: GIÁM SÁT CAMERA AI TRỰC TIẾP
# ==============================================================================
if menu == " Giám Sát Camera AI":
    st.title(" Mắt Thần AI - Điểm Danh & Giám Sát An Toàn")
    st.caption("Tự động Bù sáng CLAHE • Điểm danh khuôn mặt AI • Giám sát Mũ & Áo bảo hộ • Đồng bộ SQL Server")
 
    if "camera_running" not in st.session_state:
    st.session_state.camera_running = False
 
 c_btn, c_stats1, c_stats2, c_stats3 = st.columns([1.8, 1, 1, 1])
 
 with c_btn:
    if not st.session_state.camera_running:
    if st.button(" BẬT CAMERA", type="primary", use_container_width=True):
    st.session_state.camera_running = True
    st.rerun()
    else:
    if st.button(" TẮT CAMERA", type="secondary", use_container_width=True):
    st.session_state.camera_running = False
    st.rerun()
 
 metric_total = c_stats1.empty()
 metric_safe = c_stats2.empty()
 metric_vio = c_stats3.empty()
 
 metric_total.metric(" Quân số", "0")
 metric_safe.metric(" An toàn", "0")
 metric_vio.metric(" Vi phạm", "0")
 
    st.divider()
 
 video_col, log_col = st.columns([2.3, 1])
 frame_placeholder = video_col.empty()
 live_log_placeholder = log_col.empty()
 
 # 1. NẾU CAMERA ĐANG TẮT
    if not st.session_state.camera_running:
 frame_placeholder.markdown("""
 <div style="background: linear-gradient(135deg, #031514 0%, #062220 100%); border: 1px solid rgba(0, 255, 163, 0.2); border-radius: 16px; padding: 60px 20px; text-align: center; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);">
 <div style="width: 72px; height: 72px; background: linear-gradient(135deg, rgba(0, 242, 254, 0.15) 0%, rgba(0, 255, 163, 0.2) 100%); border: 1px solid rgba(0, 255, 163, 0.4); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 18px; box-shadow: 0 0 20px rgba(0, 255, 163, 0.2);">
 <span style="font-size: 32px;"></span>
 </div>
 <h3 style="color: #00FFA3; margin: 0 0 10px 0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;">CAMMERA ĐANG TẮT HOÀN TOÀN</h3>
 <p style="color: #81E6D9; font-size: 14.5px; max-width: 480px; margin: 0 auto 20px auto; line-height: 1.6;">
 Webcam phần cứng đã được <b style="color: #E6FFFA;">ngắt kết nối và tắt hẳn 100%</b>. Đèn camera đã tắt hoàn toàn.
 </p>
 <div style="display: inline-block; background: rgba(0, 255, 163, 0.1); border: 1px solid rgba(0, 255, 163, 0.35); border-radius: 8px; padding: 7px 18px; box-shadow: 0 0 12px rgba(0, 255, 163, 0.15);">
 <span style="color: #00FFA3; font-size: 14px; font-weight: 600;"> Nhấn nút ' BẬT CAMERA' ở trên để bắt đầu quét trực tiếp.</span>
 </div>
 </div>
 """, unsafe_allow_html=True)
 
 with live_log_placeholder.container():
    st.markdown("#### **NHẬT KÝ HÔM NAY**")
 today_workers = get_cached_today_checked_in()
    st.success(f" **Đã Check-in:** `{len(today_workers)//2 if len(today_workers)>1 else len(today_workers)}` người")
 for w_name in list(today_workers)[-5:]:
    if not str(w_name).startswith("CN"):
    st.write(f" {w_name}")
 
 # 2. NẾU CAMERA ĐANG BẬT
    else:
 person_model, ppe_model, face_detector, face_recognizer = load_all_ai_models()
 known_workers = DUANCLB.load_known_workers(FACES_DIR, face_detector, face_recognizer)
 
 cap = cv2.VideoCapture(0)
    if not cap.isOpened():
    st.error(" Không thể kết nối Webcam! Vui lòng kiểm tra cổng cắm hoặc quyền camera.")
    st.session_state.camera_running = False
    else:
 try:
 w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
 h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
 face_detector.setInputSize((w, h))
 
 checked_in_workers = database.get_today_checked_in_workers()
 captured_violation_workers = set()
 violation_confirm_counter = {}
 absent_counters = {}
 last_unknown_violation_time = 0
 
 while st.session_state.camera_running:
 ret, frame = cap.read()
    if not ret:
 time.sleep(0.03)
 continue
 
 # 1. Nhận diện khuôn mặt (YuNet + SFace)
 detected_faces = []
 _, faces = face_detector.detect(frame)
    if faces is not None and len(faces) > 0:
 for face in faces:
 fb = face[0:4].astype(int)
 aligned = face_recognizer.alignCrop(frame, face)
 feat = face_recognizer.feature(aligned)
 
 best_score = 0.0
 matched_id = "UNKNOWN"
 matched_name = "Nguoi la (Unknown)"
 
 for kname, kinfo in known_workers.items():
 score = face_recognizer.match(feat, kinfo["feature"], cv2.FaceRecognizerSF_FR_COSINE)
    if score > best_score and score >= DUANCLB.COSINE_THRESHOLD:
 best_score = score
 matched_id = kinfo["id"]
 matched_name = kinfo["name"]
 
    if matched_name != "Nguoi la (Unknown)" and matched_name not in checked_in_workers:
 checked_in_workers.add(matched_name)
 checked_in_workers.add(matched_id)
 DUANCLB.log_attendance(matched_id, matched_name)
 
 detected_faces.append({
 "bbox": fb,
 "id": matched_id,
 "name": matched_name,
 "score": best_score,
 "center": (fb[0] + fb[2] // 2, fb[1] + fb[3] // 2)
 })
 
 # 2. Phát hiện người & PPE
 person_results = person_model(frame, classes=[0], conf=0.30, verbose=False)
 person_boxes = []
 for r in person_results:
 for box in r.boxes:
 person_boxes.append(list(map(int, box.xyxy[0])))
 
    if len(person_boxes) == 0 and len(detected_faces) > 0:
 for f_item in detected_faces:
 fx, fy, fw, fh = f_item["bbox"]
 person_boxes.append([max(0, fx - int(fw*0.5)), max(0, fy - int(fh*0.3)), 
 min(w, fx + int(fw*1.5)), min(h, fy + int(fh*3.5))])
 
 safe_count = 0
 violation_count = 0
 person_count = len(person_boxes)
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
 absent_counters[identified_name] = 0
 
 checklist, is_safe, violation_text = DUANCLB.check_ppe_compliance(frame, (px1, py1, px2, py2), ppe_model)
 
 is_captured = False
 current_t = time.time()
 
    if is_safe:
 safe_count += 1
 violation_confirm_counter[identified_name] = 0
    else:
 violation_count += 1
 violation_confirm_counter[identified_name] = violation_confirm_counter.get(identified_name, 0) + 1
 
    if identified_name != "Nguoi la (Unknown)":
    if identified_name not in captured_violation_workers and violation_confirm_counter[identified_name] >= 3:
 captured_violation_workers.add(identified_name)
 violations_to_capture.append((identified_id, identified_name, violation_text))
 is_captured = (identified_name in captured_violation_workers)
    else:
    if (current_t - last_unknown_violation_time) > 10.0 and violation_confirm_counter[identified_name] >= 3:
 last_unknown_violation_time = current_t
 violations_to_capture.append((identified_id, identified_name, violation_text))
 is_captured = True
    else:
 is_captured = True
 
 DUANCLB.draw_worker_card(frame, (px1, py1, px2, py2), identified_id, identified_name, checklist, is_safe, is_captured)
 
 # Ghi nhận và lưu ảnh bằng chứng đã chèn khung HUD
 for vid, vname, vtext in violations_to_capture:
 DUANCLB.log_violation(frame.copy(), vid, vname, vtext)
 
 # Reset lượt khi vắng mặt
 for worker in list(captured_violation_workers):
    if worker not in visible_workers:
 absent_counters[worker] = absent_counters.get(worker, 0) + 1
    if absent_counters[worker] > DUANCLB.ABSENT_RESET_FRAMES:
 captured_violation_workers.remove(worker)
 absent_counters.pop(worker, None)
 violation_confirm_counter.pop(worker, None)
 
 # Cập nhật KPI
 metric_total.metric(" Quân số", str(person_count))
 metric_safe.metric(" An toàn", str(safe_count))
 metric_vio.metric(" Vi phạm", str(violation_count))
 
 frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
 frame_placeholder.image(frame_rgb, channels="RGB", use_container_width=True)
 
 with live_log_placeholder.container():
    st.markdown("#### **SỰ KIỆN GẦN NHẤT**")
    st.write(f" **Đã Check-in:** `{len(checked_in_workers)//2 if len(checked_in_workers)>1 else len(checked_in_workers)}` người")
 for cw in list(checked_in_workers)[-5:]:
    if not str(cw).startswith("CN"):
    st.success(f" {cw}")
    if captured_violation_workers:
    st.write(" **Đang ghi nhận vi phạm:**")
 for vw in list(captured_violation_workers)[-5:]:
    st.error(f" {vw}")
 
 finally:
 # ĐẢM BẢO GIẢI PHÓNG VÀ TẮT HẲN CAMERA KHI DỪNG HOẶC RỜI TRANG
 cap.release()
 cv2.destroyAllWindows()

# ==============================================================================
# TRANG 2: NHẬT KÝ ĐIỂM DANH (ATTENDANCE)
# ==============================================================================
elif menu == " Nhật Ký Điểm Danh":
    st.title(" Nhật Ký Điểm Danh Công Nhân (SQL Server)")
    st.markdown("Toàn bộ lịch sử Check-in tự động được truy xuất trực tiếp từ bảng `DIEMDANH` trong CSDL SQL Server.")
 
 df_att = get_cached_attendance_df()
 
    if not df_att.empty:
 c1, c2, c3 = st.columns([1, 1, 2])
 search_name = c1.text_input(" Tìm theo tên hoặc Mã NV:")
 date_filter = c2.selectbox(" Lọc theo Ngày:", ["Tất cả"] + list(df_att["Ngày"].unique()))
 
 filtered_df = df_att.copy()
    if search_name:
 filtered_df = filtered_df[
 filtered_df["Họ và Tên Công Nhân"].astype(str).str.contains(search_name, case=False) |
 filtered_df["Mã Công Nhân"].astype(str).str.contains(search_name, case=False)
 ]
    if date_filter != "Tất cả":
 filtered_df = filtered_df[filtered_df["Ngày"] == date_filter]
 
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)
    st.caption(f"Tổng số lượt ghi nhận: {len(filtered_df)} dòng.")
    else:
    st.info("Chưa có dữ liệu điểm danh nào trong SQL Server.")

# ==============================================================================
# TRANG 3: BẰNG CHỨNG VI PHẠM (EVIDENCE GALLERY)
# ==============================================================================
elif menu == " Bằng Chứng Vi Phạm":
    st.title(" Thư Viện Ảnh Bằng Chứng Vi Phạm An Toàn (PPE)")
    st.markdown("Hình ảnh Snapshot tự động được camera AI lưu lại khi phát hiện vi phạm, liên kết với bảng `VIPHAMANTOAN`.")
 
 df_vio = get_cached_violations_df()
 
    if not df_vio.empty:
    st.markdown(f"**Tổng số ca vi phạm đã lưu vết:** `{len(df_vio)}` trường hợp.")
    st.divider()
 
 cols = st.columns(3)
 for i, row in df_vio.iloc[::-1].iterrows():
 col_idx = i % 3
 img_name = str(row["File Ảnh Bằng Chứng"])
 img_path = os.path.join(VIOLATIONS_DIR, img_name)
 
 with cols[col_idx]:
    if os.path.exists(img_path):
    st.image(img_path, use_container_width=True)
    else:
    st.warning(f"File ảnh: {img_name}")
 
    st.error(f" **{row['Họ và Tên Công Nhân']}** `[{row['Mã Công Nhân']}]`")
    st.write(f" **Thời gian:** `{row['Giờ Vi Phạm']} - {row['Ngày']}`")
    st.write(f" **Lỗi:** {row['Lỗi Vi Phạm Cụ Thể']}")
    st.caption(f"Mức độ: **{row['Mức Độ Cảnh Báo']}** | Xử lý: **{row['Trạng Thái Xử Lý']}**")
    st.divider()
    else:
    st.success(" Tuyệt vời! Chưa có trường hợp vi phạm nào được ghi nhận.")

# ==============================================================================
# TRANG 4: THỐNG KÊ & BÁO CÁO (ANALYTICS)
# ==============================================================================
elif menu == " Thống Kê & Báo Cáo":
    st.title(" Báo Cáo Phân Tích & Thống Kê An Toàn Lao Động")
 
 df_att = get_cached_attendance_df()
 df_vio = get_cached_violations_df()
 
 total_att = len(df_att)
 total_vio = len(df_vio)
 safe_est = max(0, total_att - total_vio)
 
 k1, k2, k3, k4 = st.columns(4)
 k1.metric(" Tổng lượt Check-in", f"{total_att}")
 k2.metric(" Lượt An toàn", f"{safe_est}")
 k3.metric(" Lượt Vi phạm PPE", f"{total_vio}")
 compliance_rate = (safe_est / total_att * 100) if total_att > 0 else 100
 k4.metric(" Tỷ lệ Tuân thủ HSE", f"{compliance_rate:.1f}%")
 
    st.divider()
 
 c_chart1, c_chart2 = st.columns(2)
 
 with c_chart1:
    st.markdown("#### **Tỷ Lệ Tuân Thủ Trang Bị An Toàn**")
 fig_pie = go.Figure(data=[go.Pie(
 labels=["An toàn đạt chuẩn", "Vi phạm trang bị"],
 values=[safe_est, total_vio],
 hole=.45,
 marker_colors=["#2ca02c", "#d62728"]
 )])
 fig_pie.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig_pie, use_container_width=True)
 
 with c_chart2:
    st.markdown("#### **Phân Bố Các Lỗi Vi Phạm Phổ Biến**")
    if not df_vio.empty:
 helmet_err = df_vio["Lỗi Vi Phạm Cụ Thể"].astype(str).str.contains("mu", case=False).sum()
 vest_err = df_vio["Lỗi Vi Phạm Cụ Thể"].astype(str).str.contains("ao", case=False).sum()
 
 fig_bar = px.bar(
 x=["Không đội mũ bảo hộ", "Không mặc áo phản quang"],
 y=[helmet_err, vest_err],
 labels={"x": "Loại lỗi vi phạm", "y": "Số ca vi phạm"},
 color=["Mũ", "Áo"],
 color_discrete_sequence=["#ff7f0e", "#1f77b4"]
 )
 fig_bar.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
    st.plotly_chart(fig_bar, use_container_width=True)
    else:
    st.info("Chưa có vi phạm để vẽ biểu đồ phân bố lỗi.")

# ==============================================================================
# TRANG 5: QUẢN LÝ CÔNG NHÂN
# ==============================================================================
elif menu == " Quản Lý Công Nhân":
    st.title(" Quản Lý Danh Sách & Đăng Ký Công Nhân Mới")
    st.caption("Cơ sở dữ liệu khuôn mặt AI YuNet + SFace • Tự động đồng bộ với bảng CONGNHAN trong SQL Server")
    st.divider()
 
 col_list, col_add = st.columns([1.15, 1], gap="large")
 image_files = sorted([f for f in os.listdir(FACES_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
 
 with col_list:
    st.markdown(
 f"""
 <div style="background: linear-gradient(135deg, rgba(0, 255, 163, 0.08) 0%, rgba(0, 210, 180, 0.12) 100%); border: 1px solid rgba(0, 255, 163, 0.25); border-radius: 12px; padding: 12px 18px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center;">
 <span style="color: #00FFA3; font-weight: 700; font-size: 15px;"> Danh Sách Công Nhân Đã Đăng Ký</span>
 <span style="background: rgba(0, 255, 163, 0.2); color: #E6FFFA; padding: 2px 10px; border-radius: 20px; font-weight: 700; font-size: 12px; border: 1px solid rgba(0, 255, 163, 0.4);">{len(image_files)} Hồ sơ</span>
 </div>
 """,
 unsafe_allow_html=True
 )
 
    if image_files:
 for f in image_files:
 fpath = os.path.join(FACES_DIR, f)
 clean_name = os.path.splitext(f)[0]
 
 # Trích xuất Mã công nhân và Tên nếu theo định dạng ID_TEN
 parts = clean_name.split("_", 1)
 worker_id_disp = parts[0] if len(parts) > 1 and parts[0].startswith("CN") else "CN"
 worker_name_disp = parts[1].replace("_", " ") if len(parts) > 1 and parts[0].startswith("CN") else clean_name.replace("_", " ")
 
 c_card = st.container()
 with c_card:
 col_img, col_info, col_del = st.columns([1, 2.8, 1.1])
 with col_img:
    st.image(fpath, width=75)
 with col_info:
    st.markdown(f"<span style='color: #00FFA3; font-weight: 700; font-size: 12px; background: rgba(0, 255, 163, 0.12); padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(0, 255, 163, 0.3);'>Mã: {worker_id_disp}</span>", unsafe_allow_html=True)
    st.markdown(f"<div style='color: #F1FDFB; font-weight: 700; font-size: 15px; margin-top: 4px;'> {worker_name_disp}</div>", unsafe_allow_html=True)
 with col_del:
    st.write("")
    if st.button(" Xóa", key=f"del_{f}", use_container_width=True):
 os.remove(fpath)
    st.success(f"Đã xóa {worker_name_disp}!")
    st.rerun()
    st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid rgba(255,255,255,0.06);'>", unsafe_allow_html=True)
    else:
    st.info("Chưa có hồ sơ công nhân nào trong hệ thống. Vui lòng thêm công nhân ở khung bên phải.")
 
 with col_add:
    st.markdown(
 """
 <div style="background: linear-gradient(135deg, rgba(0, 242, 254, 0.08) 0%, rgba(0, 255, 163, 0.12) 100%); border: 1px solid rgba(0, 255, 163, 0.25); border-radius: 12px; padding: 12px 18px; margin-bottom: 18px;">
 <span style="color: #00FFA3; font-weight: 700; font-size: 15px;"> Thêm Công Nhân Mới</span>
 </div>
 """,
 unsafe_allow_html=True
 )
 
 new_id = st.text_input("Mã Công Nhân (Ví dụ: CN-002):", value=f"CN-{len(image_files)+1:03d}")
 new_name = st.text_input("Họ và Tên (Không dấu, ví dụ: Nguyen Van B):")
 
 upload_mode = st.radio("Cách thêm ảnh chân dung:", [" Tải file ảnh lên", " Chụp trực tiếp bằng Camera"], horizontal=True)
 
 img_to_save = None
    if upload_mode == " Tải file ảnh lên":
 uploaded_file = st.file_uploader("Chọn file ảnh chân dung rõ mặt (.jpg, .png):", type=["jpg", "jpeg", "png"])
    if uploaded_file is not None:
 img_to_save = Image.open(uploaded_file)
    st.image(img_to_save, caption="Xem trước ảnh tải lên", width=150)
    else:
 cam_photo = st.camera_input("Chụp ảnh chân dung:")
    if cam_photo is not None:
 img_to_save = Image.open(cam_photo)
 
    st.write("")
    if st.button(" Lưu & Đăng Ký Hồ Sơ Mới", type="primary", use_container_width=True):
    if not new_name.strip():
    st.error("Vui lòng nhập Họ và Tên công nhân!")
 elif img_to_save is None:
    st.error("Vui lòng tải ảnh hoặc chụp ảnh chân dung!")
    else:
 clean_filename = f"{new_id.strip().upper()}_{new_name.strip().replace(' ', '_')}.jpg"
 save_path = os.path.join(FACES_DIR, clean_filename)
 img_to_save = ImageOps.exif_transpose(img_to_save)
 img_to_save.save(save_path)
 
 try:
 conn = database.get_connection()
    if conn:
 cursor = conn.cursor()
 cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaCN = ?", (new_id.strip().upper(),))
    if not cursor.fetchone():
 cursor.execute(
 "INSERT INTO CONGNHAN (MaCN, HoTen) VALUES (?, ?)", 
 (new_id.strip().upper(), new_name.strip())
 )
 conn.commit()
 conn.close()
 except Exception as e:
 print(f"[SQL INSERT WORKER ERROR]: {e}")
 
    st.success(f" Đã đăng ký thành công công nhân: [{new_id}] {new_name} vào Hệ thống & SQL Server!")
    st.rerun()

# ==============================================================================
