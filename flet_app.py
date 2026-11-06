import os
import sys
import time
import base64
import threading
import flet as ft
from datetime import datetime

# Đảm bảo đường dẫn import
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

import database
import DUANCLB

# ==============================================================================
# SMART HSE VISION - ỨNG DỤNG ĐA NỀN TẢNG (FLUTTER / FLET FOR PYTHON)
# - CHẠY NATIVE TRÊN WINDOWS, MACOS, ANDROID (.APK), IOS
# - PHÂN QUYỀN 3 TẦNG:
#   1. CHỦ SERVER (SUPER ADMIN): Quản lý Owner, Kích hoạt / Khóa xưởng thuê dịch vụ
#   2. CHỦ DOANH NGHIỆP (OWNER): Quản lý Camera AI, Ca làm việc, Điểm danh, Vi phạm
#   3. CÔNG NHÂN (WORKERS): Nhận diện khuôn mặt & Giám sát bảo hộ PPE
# ==============================================================================

def main(page: ft.Page):
    # Cấu hình cửa sổ & Theme Dark Emerald Luxury
    page.title = "Smart HSE Vision - Enterprise AI Safety"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#031412"
    page.padding = 0
    page.window.width = 1200
    page.window.height = 800
    page.window.min_width = 400
    page.window.min_height = 600

    # Trạng thái phiên làm việc (Session State)
    current_user = {"role": None, "data": None}
    camera_holder = {"threaded_cam": None, "is_running": False, "thread": None}

    # ==========================================================================
    # DIALOG THÔNG BÁO TIỆN ÍCH
    # ==========================================================================
    def show_snackbar(message: str, color="#00FFA3"):
        snack = ft.SnackBar(
            content=ft.Text(message, color="#021211", weight=ft.FontWeight.BOLD),
            bgcolor=color,
            duration=3000
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    # ==========================================================================
    # 1. MÀN HÌNH ĐĂNG NHẬP (LOGIN SCREEN)
    # ==========================================================================
    username_input = ft.TextField(
        label="Tên đăng nhập",
        prefix_icon=ft.Icons.PERSON_OUTLINE,
        border_color="#00FFA3",
        cursor_color="#00FFA3",
        focused_border_color="#00F2FE",
        text_style=ft.TextStyle(color="#FFFFFF"),
        width=320,
    )
    password_input = ft.TextField(
        label="Mật khẩu",
        password=True,
        can_reveal_password=True,
        prefix_icon=ft.Icons.LOCK_OUTLINE,
        border_color="#00FFA3",
        cursor_color="#00FFA3",
        focused_border_color="#00F2FE",
        text_style=ft.TextStyle(color="#FFFFFF"),
        width=320,
    )
    role_selector = ft.RadioGroup(
        content=ft.Row(
            controls=[
                ft.Radio(value="owner", label="Chủ Doanh Nghiệp (Owner)"),
                ft.Radio(value="admin", label="Chủ Server (Admin)"),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        value="owner"
    )

    def handle_login(e):
        u = username_input.value.strip() if username_input.value else ""
        p = password_input.value.strip() if password_input.value else ""
        if not u or not p:
            show_snackbar("Vui lòng nhập đầy đủ Tên đăng nhập và Mật khẩu!", "#FF4D4D")
            return

        selected_role = role_selector.value
        if selected_role == "admin":
            admin_data = database.verify_admin_login(u, p)
            if admin_data:
                current_user["role"] = "admin"
                current_user["data"] = admin_data
                show_snackbar(f"Xin chào Chủ Server: {admin_data['hoten']}!")
                render_admin_dashboard()
            else:
                show_snackbar("Sai tài khoản hoặc mật khẩu Quản trị Server!", "#FF4D4D")
        else:
            owner_data = database.verify_owner_login(u, p)
            if owner_data:
                if owner_data["trang_thai"] != "Hoạt động":
                    show_snackbar("Tài khoản xưởng này đã bị TẠM KHÓA hoặc HẾT HẠN! Vui lòng liên hệ Chủ Server.", "#FF4D4D")
                    return
                current_user["role"] = "owner"
                current_user["data"] = owner_data
                show_snackbar(f"Chào mừng: {owner_data['ten_doanh_nghiep']}!")
                render_owner_dashboard()
            else:
                show_snackbar("Sai tài khoản hoặc mật khẩu Doanh nghiệp!", "#FF4D4D")

    login_view = ft.Container(
        expand=True,
        alignment=ft.alignment.center,
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_center,
            end=ft.alignment.bottom_center,
            colors=["#041615", "#020F0E"]
        ),
        content=ft.Card(
            elevation=12,
            color="#05221F",
            shape=ft.RoundedRectangleBorder(radius=18),
            content=ft.Container(
                padding=36,
                border=ft.border.all(1, "#00FFA344"),
                border_radius=18,
                width=420,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                    spacing=16,
                    controls=[
                        ft.Icon(ft.Icons.SECURITY_ROUNDED, size=52, color="#00FFA3"),
                        ft.Text(
                            "SMART HSE VISION",
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            color="#00FFA3"
                        ),
                        ft.Text(
                            "Hệ Thống An Toàn Lao Động Đa Nền Tảng",
                            size=12,
                            color="#81E6D9",
                        ),
                        ft.Divider(color="#00FFA322"),
                        role_selector,
                        username_input,
                        password_input,
                        ft.Container(height=8),
                        ft.ElevatedButton(
                            text="ĐĂNG NHẬP HỆ THỐNG",
                            style=ft.ButtonStyle(
                                bgcolor="#00FFA3",
                                color="#021211",
                                shape=ft.RoundedRectangleBorder(radius=8),
                                padding=18
                            ),
                            width=320,
                            on_click=handle_login
                        ),
                        ft.Text("Hỗ trợ Windows • macOS • Android • iOS", size=11, color="#64748B")
                    ]
                )
            )
        )
    )

    # ==========================================================================
    # 2. MÀN HÌNH CHỦ SERVER (SUPER ADMIN DASHBOARD)
    # ==========================================================================
    def render_admin_dashboard():
        stop_camera()
        owners_list = database.get_all_owners_for_admin()

        def toggle_status(owner_id, current_status):
            new_status = "Tạm khóa" if current_status == "Hoạt động" else "Hoạt động"
            database.set_owner_status(owner_id, new_status)
            show_snackbar(f"Đã chuyển xưởng {owner_id} sang trạng thái: {new_status}!")
            render_admin_dashboard()

        # Tạo bảng quản trị danh sách các Owner
        owner_rows = []
        for o in owners_list:
            is_active = (o.get("TrangThai") == "Hoạt động")
            btn_color = ft.Colors.RED if is_active else ft.Colors.GREEN
            btn_text = "Khóa Xưởng" if is_active else "Kích Hoạt"
            status_badge = ft.Container(
                content=ft.Text(o.get("TrangThai", ""), size=12, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                bgcolor=ft.Colors.GREEN_700 if is_active else ft.Colors.RED_700,
                padding=ft.padding.symmetric(horizontal=10, vertical=4),
                border_radius=6
            )
            owner_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(o.get("MaOwner", ""), weight=ft.FontWeight.BOLD, color="#00FFA3")),
                        ft.DataCell(ft.Text(o.get("TenDoanhNghiep", ""), color="#FFFFFF")),
                        ft.DataCell(ft.Text(o.get("NguoiDaiDien", ""), color="#E2E8F0")),
                        ft.DataCell(ft.Text(o.get("Username", ""), color="#81E6D9")),
                        ft.DataCell(ft.Text(str(o.get("SoLuongCN", 0)), color="#00F2FE")),
                        ft.DataCell(status_badge),
                        ft.DataCell(
                            ft.OutlinedButton(
                                text=btn_text,
                                style=ft.ButtonStyle(color=btn_color),
                                on_click=lambda e, oid=o.get("MaOwner"), st=o.get("TrangThai"): toggle_status(oid, st)
                            )
                        ),
                    ]
                )
            )

        admin_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Mã Xưởng", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Tên Doanh Nghiệp", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Người Đại Diện", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Tài Khoản App", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Quân Số", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Trạng Thái", weight=ft.FontWeight.BOLD, color="#00FFA3")),
                ft.DataColumn(ft.Text("Hành Động", weight=ft.FontWeight.BOLD, color="#00FFA3")),
            ],
            rows=owner_rows,
            border=ft.border.all(1, "#00FFA322"),
            border_radius=10,
            vertical_lines=ft.border.BorderSide(1, "#00FFA311"),
        )

        header = ft.Container(
            padding=ft.padding.all(20),
            bgcolor="#05221F",
            border=ft.border.only(bottom=ft.BorderSide(1, "#00FFA344")),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.ADMIN_PANEL_SETTINGS, color="#00FFA3", size=32),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text("BẢNG ĐIỀU KHIỂN CHỦ SERVER (SUPER ADMIN)", size=18, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                                    ft.Text("Quản trị các doanh nghiệp thuê phần mềm & Giám sát hạ tầng", size=12, color="#81E6D9")
                                ]
                            )
                        ]
                    ),
                    ft.ElevatedButton(
                        text="ĐĂNG XUẤT",
                        style=ft.ButtonStyle(bgcolor="#FF4D4D", color="#FFFFFF"),
                        on_click=lambda e: show_login_screen()
                    )
                ]
            )
        )

        body = ft.Container(
            padding=ft.padding.all(24),
            expand=True,
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                spacing=20,
                controls=[
                    ft.Row(
                        controls=[
                            ft.Card(
                                color="#041F1C",
                                content=ft.Container(
                                    padding=18,
                                    width=240,
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("Tổng Số Doanh Nghiệp", size=13, color="#81E6D9"),
                                            ft.Text(str(len(owners_list)), size=28, weight=ft.FontWeight.BOLD, color="#00FFA3")
                                        ]
                                    )
                                )
                            ),
                            ft.Card(
                                color="#041F1C",
                                content=ft.Container(
                                    padding=18,
                                    width=240,
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("CSDL Trung Tâm", size=13, color="#81E6D9"),
                                            ft.Text("SQL Server Active", size=20, weight=ft.FontWeight.BOLD, color="#00F2FE")
                                        ]
                                    )
                                )
                            ),
                            ft.Card(
                                color="#041F1C",
                                content=ft.Container(
                                    padding=18,
                                    width=240,
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("Bảo Mật Mạng", size=13, color="#81E6D9"),
                                            ft.Text("Tailscale WireGuard", size=20, weight=ft.FontWeight.BOLD, color="#F59E0B")
                                        ]
                                    )
                                )
                            ),
                        ]
                    ),
                    ft.Text("Danh Sách Doanh Nghiệp Sử Dụng Dịch Vụ:", size=16, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                    admin_table
                ]
            )
        )

        page.clean()
        page.add(ft.Column(expand=True, spacing=0, controls=[header, body]))

    # ==========================================================================
    # 3. MÀN HÌNH CHỦ DOANH NGHIỆP (OWNER DASHBOARD)
    # ==========================================================================
    def render_owner_dashboard():
        owner = current_user["data"]
        ma_owner = owner["ma_owner"]
        shift_cfg = database.get_shift_config_db(ma_owner)

        # Trạng thái camera
        cam_image = ft.Image(
            src_base64="",
            width=640,
            height=480,
            fit=ft.ImageFit.CONTAIN,
            border_radius=12
        )
        cam_placeholder = ft.Container(
            width=640,
            height=480,
            bgcolor="#020E0D",
            border=ft.border.all(1, "#00FFA333"),
            border_radius=12,
            alignment=ft.alignment.center,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.VIDEOCAM_OFF, size=64, color="#64748B"),
                    ft.Text("Camera Đang Tắt", size=16, color="#64748B", weight=ft.FontWeight.BOLD),
                    ft.Text("Bấm 'BẬT CAMERA' để bắt đầu AI Vision", size=12, color="#475569")
                ]
            )
        )

        stat_checked = ft.Text("0", size=26, weight=ft.FontWeight.BOLD, color="#00FFA3")
        stat_violations = ft.Text("0", size=26, weight=ft.FontWeight.BOLD, color="#FF4D4D")

        # Nút Bật/Tắt Camera
        btn_toggle_cam = ft.ElevatedButton(
            text="BẬT CAMERA",
            icon=ft.Icons.VIDEOCAM,
            style=ft.ButtonStyle(bgcolor="#00FFA3", color="#021211", padding=16),
        )

        def toggle_camera_action(e):
            if camera_holder["is_running"]:
                stop_camera()
                btn_toggle_cam.text = "BẬT CAMERA"
                btn_toggle_cam.style.bgcolor = "#00FFA3"
                cam_view_container.content = cam_placeholder
                page.update()
                show_snackbar("Đã dừng Camera!")
            else:
                start_camera(ma_owner, cam_image, stat_checked, stat_violations)
                btn_toggle_cam.text = "TẮT CAMERA"
                btn_toggle_cam.style.bgcolor = "#FF4D4D"
                cam_view_container.content = cam_image
                page.update()
                show_snackbar("Đã bật Camera AI siêu tốc DirectShow!")

        btn_toggle_cam.on_click = toggle_camera_action
        cam_view_container = ft.Container(content=cam_placeholder)

        # Cấu hình ca làm việc
        shift_start_field = ft.TextField(label="Giờ bắt đầu", value=shift_cfg.get("shift_start", "07:30"), width=120)
        grace_mins_field = ft.TextField(label="Ân hạn trễ (phút)", value=str(shift_cfg.get("grace_period_mins", 15)), width=120)

        def save_shift_action(e):
            st = shift_start_field.value.strip()
            gr = grace_mins_field.value.strip()
            try:
                database.save_shift_config_db(ma_owner, st, int(gr))
                show_snackbar(f"Đã lưu ca làm việc: {st} (Ân hạn: {gr}p)!")
            except Exception as ex:
                show_snackbar(f"Lỗi lưu ca: {ex}", "#FF4D4D")

        btn_save_shift = ft.ElevatedButton("Lưu Ca Làm", on_click=save_shift_action)

        header = ft.Container(
            padding=ft.padding.all(18),
            bgcolor="#05221F",
            border=ft.border.only(bottom=ft.BorderSide(1, "#00FFA344")),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.FACTORY_OUTLINED, color="#00FFA3", size=32),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(f"{owner['ten_doanh_nghiep'].upper()} (MÃ: {ma_owner})", size=17, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                                    ft.Text(f"Đại diện: {owner['nguoi_dai_dien']} • Đồng bộ SQL Server", size=12, color="#81E6D9")
                                ]
                            )
                        ]
                    ),
                    ft.Row(
                        controls=[
                            ft.OutlinedButton(
                                text="XUẤT BÁO CÁO EXCEL",
                                icon=ft.Icons.DOWNLOAD,
                                style=ft.ButtonStyle(color="#00FFA3"),
                                on_click=lambda e: export_excel_action(ma_owner)
                            ),
                            ft.ElevatedButton(
                                text="ĐĂNG XUẤT",
                                style=ft.ButtonStyle(bgcolor="#FF4D4D", color="#FFFFFF"),
                                on_click=lambda e: show_login_screen()
                            )
                        ]
                    )
                ]
            )
        )

        body = ft.Container(
            padding=ft.padding.all(20),
            expand=True,
            content=ft.Row(
                alignment=ft.MainAxisAlignment.START,
                cross_alignment=ft.CrossAxisAlignment.START,
                spacing=24,
                controls=[
                    # Cột 1: Camera AI & Điều khiển
                    ft.Column(
                        width=660,
                        spacing=14,
                        controls=[
                            cam_view_container,
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    btn_toggle_cam,
                                    ft.Row(
                                        controls=[
                                            shift_start_field,
                                            grace_mins_field,
                                            btn_save_shift
                                        ]
                                    )
                                ]
                            )
                        ]
                    ),
                    # Cột 2: Bảng Thống kê & Danh sách Điểm danh
                    ft.Column(
                        expand=True,
                        spacing=16,
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Card(
                                        color="#041F1C",
                                        content=ft.Container(
                                            padding=16,
                                            width=180,
                                            content=ft.Column(
                                                controls=[
                                                    ft.Text("Đã Điểm Danh", size=12, color="#81E6D9"),
                                                    stat_checked
                                                ]
                                            )
                                        )
                                    ),
                                    ft.Card(
                                        color="#041F1C",
                                        content=ft.Container(
                                            padding=16,
                                            width=180,
                                            content=ft.Column(
                                                controls=[
                                                    ft.Text("Vi Phạm Bảo Hộ", size=12, color="#FFB8B8"),
                                                    stat_violations
                                                ]
                                            )
                                        )
                                    )
                                ]
                            ),
                            ft.Text("Lịch Sử Điểm Danh Gần Nhất:", size=15, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                            build_recent_attendance_table(ma_owner)
                        ]
                    )
                ]
            )
        )

        page.clean()
        page.add(ft.Column(expand=True, spacing=0, controls=[header, body]))

    def build_recent_attendance_table(ma_owner):
        df_att = database.get_attendance_df(ma_owner)
        rows = []
        if not df_att.empty:
            for _, r in df_att.head(8).iterrows():
                rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(str(r.get("Giờ Điểm Danh", "")), color="#81E6D9")),
                            ft.DataCell(ft.Text(str(r.get("Mã Công Nhân", "")), color="#00FFA3", weight=ft.FontWeight.BOLD)),
                            ft.DataCell(ft.Text(str(r.get("Họ và Tên Công Nhân", "")), color="#FFFFFF")),
                            ft.DataCell(ft.Text(str(r.get("Trạng Thái", "")), color="#00F2FE")),
                        ]
                    )
                )
        return ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Giờ", color="#00FFA3")),
                ft.DataColumn(ft.Text("Mã CN", color="#00FFA3")),
                ft.DataColumn(ft.Text("Họ Tên", color="#00FFA3")),
                ft.DataColumn(ft.Text("Trạng Thái", color="#00FFA3")),
            ],
            rows=rows,
            border=ft.border.all(1, "#00FFA322"),
            border_radius=8
        )

    def export_excel_action(ma_owner):
        try:
            excel_bytes = database.get_dynamic_excel_bytes(ma_owner)
            out_file = os.path.join(BASE_DIR, "logs", f"BaoCao_HSE_{ma_owner}.xlsx")
            with open(out_file, "wb") as f:
                f.write(excel_bytes)
            show_snackbar(f"Đã xuất báo cáo Excel thành công: {os.path.basename(out_file)}!")
        except Exception as e:
            show_snackbar(f"Lỗi xuất Excel: {e}", "#FF4D4D")

    # ==========================================================================
    # QUẢN LÝ CAMERA THREADED & AI VISION
    # ==========================================================================
    def start_camera(ma_owner, cam_image, stat_checked, stat_violations):
        if camera_holder["is_running"]:
            return
        camera_holder["threaded_cam"] = DUANCLB.FastThreadedCamera(0)
        camera_holder["is_running"] = True

        person_model, ppe_model, face_detector, face_recognizer = DUANCLB.init_models()
        known_workers = DUANCLB.load_known_workers(os.path.join(BASE_DIR, "known_faces"), face_detector, face_recognizer)
        checked_today = database.get_today_checked_in_workers(ma_owner)

        def camera_loop():
            while camera_holder["is_running"]:
                ret, frame = camera_holder["threaded_cam"].read()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                w, h = frame.shape[1], frame.shape[0]
                face_detector.setInputSize((w, h))

                # 1. Nhận diện khuôn mặt điểm danh
                _, faces = face_detector.detect(frame)
                detected_faces = []
                if faces is not None and len(faces) > 0:
                    for f in faces:
                        fb = f[0:4].astype(int)
                        aligned = face_recognizer.alignCrop(frame, f)
                        feat = face_recognizer.feature(aligned)
                        best_score, mid, mname = 0.0, "UNKNOWN", "Nguoi la (Unknown)"
                        for kname, kinfo in known_workers.items():
                            sc = face_recognizer.match(feat, kinfo["feature"], cv2.FaceRecognizerSF_FR_COSINE)
                            if sc > best_score and sc >= DUANCLB.COSINE_THRESHOLD:
                                best_score = sc
                                mid = kinfo["id"]
                                mname = kinfo["name"]

                        if mname != "Nguoi la (Unknown)" and mname not in checked_today:
                            checked_today.add(mname)
                            DUANCLB.log_attendance(ma_owner, mid, mname)

                        detected_faces.append({"bbox": fb, "id": mid, "name": mname, "center": (fb[0]+fb[2]//2, fb[1]+fb[3]//2)})

                # 2. Quét người & đồ bảo hộ PPE
                results = person_model(frame, classes=[0], conf=0.35, verbose=False)
                for r in results:
                    for box in r.boxes:
                        px1, py1, px2, py2 = map(int, box.xyxy[0])
                        chk, is_safe, vtext = DUANCLB.check_ppe_compliance(frame, (px1, py1, px2, py2), ppe_model)
                        
                        target_id, target_name = "UNKNOWN", "Nguoi la"
                        for df in detected_faces:
                            fc_x, fc_y = df["center"]
                            if px1 <= fc_x <= px2 and py1 <= fc_y <= py2:
                                target_id, target_name = df["id"], df["name"]
                                break

                        DUANCLB.draw_worker_card(frame, (px1, py1, px2, py2), target_id, target_name, chk, is_safe, False)

                # Nén hiển thị Flet
                _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                jpg_as_text = base64.b64encode(buffer).decode("utf-8")
                cam_image.src_base64 = jpg_as_text
                stat_checked.value = str(len(checked_today))
                page.update()
                time.sleep(0.015)

        camera_holder["thread"] = threading.Thread(target=camera_loop, daemon=True)
        camera_holder["thread"].start()

    def stop_camera():
        camera_holder["is_running"] = False
        if camera_holder["threaded_cam"]:
            camera_holder["threaded_cam"].release()
            camera_holder["threaded_cam"] = None

    def show_login_screen():
        stop_camera()
        current_user["role"] = None
        current_user["data"] = None
        page.clean()
        page.add(login_view)

    # Hiển thị ban đầu
    show_login_screen()

if __name__ == "__main__":
    ft.app(target=main, port=8550, view=ft.AppView.WEB_BROWSER)
