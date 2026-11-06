import os
import sys

# Thiết lập UTF-8 toàn diện cho console Windows tránh UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import pyodbc
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# ==============================================================================
# MODULE CƠ SỞ DỮ LIỆU SQL SERVER TRUNG TÂM (SMART HSE VISION - MULTI-TENANT)
# - SERVER: .\SQLEXPRESS01
# - DATABASE: Smart_HSE
# - PHÂN CẤP 3 TẦNG: SERVER_ADMIN -> OWNER_DOANHNGHIEP -> CONGNHAN
# - CÁC BẢNG: SERVER_ADMIN, OWNER_DOANHNGHIEP, CA_LAM_VIEC, CONGNHAN, DIEMDANH, VIPHAMANTOAN
# ==============================================================================

DB_CONFIG = {
    "server": r".\SQLEXPRESS01",
    "database": "Smart_HSE",
    "trusted_connection": "yes",
    "driver": "ODBC Driver 17 for SQL Server"
}

_WORKING_CONN_STR = None

def get_connection():
    """Tạo kết nối cached và an toàn đến SQL Server với hỗ trợ tiếng Việt Unicode UTF-8 chuẩn xác 100%."""
    global _WORKING_CONN_STR
    
    if _WORKING_CONN_STR:
        try:
            return pyodbc.connect(_WORKING_CONN_STR, timeout=2)
        except Exception:
            _WORKING_CONN_STR = None

    servers_to_try = [
        r".\SQLEXPRESS01",
        r"VanKhiem\SQLEXPRESS01",
        r"localhost\SQLEXPRESS01",
        r"127.0.0.1\SQLEXPRESS01"
    ]
    
    # Ưu tiên các driver ODBC hiện đại hỗ trợ UTF-8 chuẩn nhất
    drivers_to_try = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server"
    ]
    
    for s in servers_to_try:
        for drv in drivers_to_try:
            try:
                trust_cert = ";TrustServerCertificate=yes" if "18" in drv else ""
                conn_str = (
                    f"DRIVER={{{drv}}};"
                    f"SERVER={s};"
                    f"DATABASE={DB_CONFIG['database']};"
                    f"Trusted_Connection={DB_CONFIG['trusted_connection']};"
                    f"Connection Timeout=2;{trust_cert}"
                )
                conn = pyodbc.connect(conn_str, timeout=2)
                _WORKING_CONN_STR = conn_str
                return conn
            except Exception:
                continue
    return None

def test_db_connection():
    """Kiểm tra kết nối SQL Server Smart_HSE."""
    conn = get_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            conn.close()
            return True, "Kết nối SQL Server (Smart_HSE) hoạt động tốt!"
        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            return False, f"Lỗi truy vấn SQL: {e}"
    return False, "Không thể kết nối đến SQL Server Smart_HSE."

# ==============================================================================
# 1. QUẢN LÝ TẦNG 1: SERVER ADMIN (BẠN)
# ==============================================================================
def verify_admin_login(username, password):
    """Xác thực tài khoản Chủ Server."""
    conn = get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT AdminID, Username, HoTen FROM SERVER_ADMIN WHERE Username = ? AND PasswordHash = ?", (username, password))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"admin_id": row[0], "username": row[1], "hoten": row[2]}
    except Exception as e:
        print(f"[SQL ADMIN LOGIN ERROR]: {e}")
        try: conn.close()
        except: pass
    return None

def get_all_owners_for_admin():
    """Lấy danh sách toàn bộ các Owner/Doanh nghiệp để Chủ Server quản lý chi tiết."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.MaOwner, o.TenDoanhNghiep, o.NguoiDaiDien, o.SoDT, o.Email, o.Username, 
                   o.GoiDichVu, o.TrangThai,
                   CONVERT(VARCHAR(10), o.NgayKichHoat, 103) AS NgayKichHoat,
                   CONVERT(VARCHAR(10), o.NgayHetHan, 103) AS NgayHetHan,
                   (SELECT COUNT(*) FROM CONGNHAN c WHERE c.MaOwner = o.MaOwner) AS SoLuongCN,
                   (SELECT COUNT(*) FROM DIEMDANH d WHERE d.MaOwner = o.MaOwner) AS TongDiemDanh,
                   (SELECT COUNT(*) FROM VIPHAMANTOAN v WHERE v.MaOwner = o.MaOwner) AS TongViPham
            FROM OWNER_DOANHNGHIEP o
            ORDER BY o.MaOwner ASC
        """)
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        result = [dict(zip(columns, r)) for r in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"[SQL GET OWNERS ERROR]: {e}")
        try: conn.close()
        except: pass
        return []

def get_admin_system_kpis():
    """Lấy số liệu thống kê toàn cục cho Chủ Server (Toàn bộ các xưởng/doanh nghiệp)."""
    conn = get_connection()
    default_stats = {
        "total_tenants": 0,
        "active_tenants": 0,
        "locked_tenants": 0,
        "total_workers": 0,
        "total_attendance": 0,
        "today_attendance": 0,
        "total_violations": 0,
        "today_violations": 0,
        "db_status": "Online (SQL Server)",
        "server_name": DB_CONFIG["server"]
    }
    if not conn:
        default_stats["db_status"] = "Mất kết nối SQL Server"
        return default_stats
    try:
        cur = conn.cursor()
        # 1. Doanh nghiệp
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN TrangThai = N'Hoạt động' THEN 1 ELSE 0 END) FROM OWNER_DOANHNGHIEP")
        row = cur.fetchone()
        total_tenants = int(row[0] or 0)
        active_tenants = int(row[1] or 0)
        locked_tenants = total_tenants - active_tenants

        # 2. Công nhân
        cur.execute("SELECT COUNT(*) FROM CONGNHAN")
        total_workers = int(cur.fetchone()[0] or 0)

        # 3. Điểm danh (tổng + hôm nay)
        cur.execute("SELECT COUNT(*) FROM DIEMDANH")
        total_att = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM DIEMDANH WHERE NgayDD = CAST(GETDATE() AS DATE)")
        today_att = int(cur.fetchone()[0] or 0)

        # 4. Vi phạm an toàn (tổng + hôm nay)
        cur.execute("SELECT COUNT(*) FROM VIPHAMANTOAN")
        total_vio = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM VIPHAMANTOAN WHERE NgayViPham = CAST(GETDATE() AS DATE)")
        today_vio = int(cur.fetchone()[0] or 0)

        conn.close()
        return {
            "total_tenants": total_tenants,
            "active_tenants": active_tenants,
            "locked_tenants": locked_tenants,
            "total_workers": total_workers,
            "total_attendance": total_att,
            "today_attendance": today_att,
            "total_violations": total_vio,
            "today_violations": today_vio,
            "db_status": "Online (SQL Server)",
            "server_name": DB_CONFIG["server"]
        }
    except Exception as e:
        print(f"[SQL GET ADMIN KPIS ERROR]: {e}")
        try: conn.close()
        except: pass
        return default_stats

def get_next_owner_id():
    """Tự động sinh mã nhà xưởng/doanh nghiệp kế tiếp theo định dạng OWN01, OWN02..."""
    conn = get_connection()
    if not conn:
        return "OWN02"
    try:
        cur = conn.cursor()
        cur.execute("SELECT MaOwner FROM OWNER_DOANHNGHIEP")
        rows = cur.fetchall()
        conn.close()
        max_num = 0
        for r in rows:
            val = str(r[0]).strip().upper()
            if val.startswith("OWN"):
                num_str = val[3:]
                if num_str.isdigit():
                    max_num = max(max_num, int(num_str))
        return f"OWN{max_num + 1:02d}"
    except Exception:
        return "OWN02"

def add_owner_db(ma_owner, ten_dn, dai_dien, so_dt, email, username, password, goi_dv="Tiêu chuẩn", han_dung_thang=12):
    """Thêm một doanh nghiệp/nhà xưởng mới vào hệ thống và khởi tạo cấu hình ca mặc định."""
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối đến SQL Server!"
    try:
        cur = conn.cursor()
        # Kiểm tra trùng mã owner
        cur.execute("SELECT MaOwner FROM OWNER_DOANHNGHIEP WHERE MaOwner = ?", (ma_owner,))
        if cur.fetchone():
            conn.close()
            return False, f"Mã doanh nghiệp '{ma_owner}' đã tồn tại!"

        # Kiểm tra trùng Username
        cur.execute("SELECT Username FROM OWNER_DOANHNGHIEP WHERE Username = ?", (username,))
        if cur.fetchone():
            conn.close()
            return False, f"Tên đăng nhập '{username}' đã được sử dụng!"
        
        cur.execute("SELECT Username FROM SERVER_ADMIN WHERE Username = ?", (username,))
        if cur.fetchone():
            conn.close()
            return False, f"Tên đăng nhập '{username}' trùng với tài khoản quản trị hệ thống!"

        # Chèn vào OWNER_DOANHNGHIEP
        if han_dung_thang and int(han_dung_thang) > 0:
            cur.execute("""
                INSERT INTO OWNER_DOANHNGHIEP 
                (MaOwner, TenDoanhNghiep, NguoiDaiDien, SoDT, Email, Username, PasswordHash, GoiDichVu, TrangThai, NgayKichHoat, NgayHetHan)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, N'Hoạt động', GETDATE(), DATEADD(month, ?, GETDATE()))
            """, (ma_owner, ten_dn, dai_dien, so_dt, email, username, password, goi_dv, int(han_dung_thang)))
        else:
            cur.execute("""
                INSERT INTO OWNER_DOANHNGHIEP 
                (MaOwner, TenDoanhNghiep, NguoiDaiDien, SoDT, Email, Username, PasswordHash, GoiDichVu, TrangThai, NgayKichHoat, NgayHetHan)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, N'Hoạt động', GETDATE(), NULL)
            """, (ma_owner, ten_dn, dai_dien, so_dt, email, username, password, goi_dv))

        # Khởi tạo ca làm việc mặc định cho doanh nghiệp mới
        cur.execute("""
            INSERT INTO CA_LAM_VIEC (MaOwner, TenCa, GioBatDau, PhutAnHan, GioKetThuc)
            VALUES (?, N'Ca Sáng Hành Chính', '07:30', 15, '17:00')
        """, (ma_owner,))

        conn.commit()
        conn.close()
        return True, "Thêm doanh nghiệp mới thành công!"
    except Exception as e:
        print(f"[SQL ADD OWNER ERROR]: {e}")
        try: conn.close()
        except: pass
        return False, f"Lỗi cơ sở dữ liệu: {e}"

def update_owner_db(ma_owner, ten_dn, dai_dien, so_dt, email, goi_dv, trang_thai, new_password=None):
    """Cập nhật thông tin doanh nghiệp, gói dịch vụ, trạng thái và đặt lại mật khẩu nếu có."""
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối đến SQL Server!"
    try:
        cur = conn.cursor()
        if new_password and str(new_password).strip():
            cur.execute("""
                UPDATE OWNER_DOANHNGHIEP
                SET TenDoanhNghiep = ?, NguoiDaiDien = ?, SoDT = ?, Email = ?, GoiDichVu = ?, TrangThai = ?, PasswordHash = ?
                WHERE MaOwner = ?
            """, (ten_dn, dai_dien, so_dt, email, goi_dv, trang_thai, str(new_password).strip(), ma_owner))
        else:
            cur.execute("""
                UPDATE OWNER_DOANHNGHIEP
                SET TenDoanhNghiep = ?, NguoiDaiDien = ?, SoDT = ?, Email = ?, GoiDichVu = ?, TrangThai = ?
                WHERE MaOwner = ?
            """, (ten_dn, dai_dien, so_dt, email, goi_dv, trang_thai, ma_owner))

        conn.commit()
        conn.close()
        return True, "Cập nhật thông tin doanh nghiệp thành công!"
    except Exception as e:
        print(f"[SQL UPDATE OWNER ERROR]: {e}")
        try: conn.close()
        except: pass
        return False, f"Lỗi cơ sở dữ liệu: {e}"

def delete_owner_db(ma_owner):
    """Xóa doanh nghiệp và dữ liệu liên quan."""
    if ma_owner == "OWN01":
        return False, "Không thể xóa doanh nghiệp kiểu mẫu cốt lõi (OWN01)!"
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối đến SQL Server!"
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM DIEMDANH WHERE MaOwner = ?", (ma_owner,))
        cur.execute("DELETE FROM VIPHAMANTOAN WHERE MaOwner = ?", (ma_owner,))
        cur.execute("DELETE FROM CONGNHAN WHERE MaOwner = ?", (ma_owner,))
        cur.execute("DELETE FROM CA_LAM_VIEC WHERE MaOwner = ?", (ma_owner,))
        cur.execute("DELETE FROM OWNER_DOANHNGHIEP WHERE MaOwner = ?", (ma_owner,))
        conn.commit()
        conn.close()
        return True, f"Đã xóa hoàn toàn doanh nghiệp {ma_owner}!"
    except Exception as e:
        print(f"[SQL DELETE OWNER ERROR]: {e}")
        try: conn.close()
        except: pass
        return False, f"Lỗi khi xóa doanh nghiệp: {e}"

def get_system_audit_logs(limit=30):
    """Lấy danh sách nhật ký hoạt động tổng hợp trên toàn hệ thống."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT TOP (?) 
                'ATTENDANCE' AS LoaiSuKien,
                d.ID,
                CONVERT(VARCHAR(10), d.NgayDD, 103) AS Ngay,
                CONVERT(VARCHAR(8), d.GioDD, 108) AS Gio,
                d.MaOwner,
                ISNULL(o.TenDoanhNghiep, d.MaOwner) AS TenDoanhNghiep,
                d.MaCN,
                ISNULL(c.HoTen, N'Chưa rõ') AS HoTen,
                d.TrangThai AS ChiTiet,
                d.PhuongThuc
            FROM DIEMDANH d
            LEFT JOIN OWNER_DOANHNGHIEP o ON d.MaOwner = o.MaOwner
            LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
            ORDER BY d.ID DESC
        """, (limit,))
        columns = [c[0] for c in cur.description]
        rows = cur.fetchall()
        result = [dict(zip(columns, r)) for r in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"[SQL GET AUDIT LOGS ERROR]: {e}")
        try: conn.close()
        except: pass
        return []

def set_owner_status(ma_owner, status):
    """Chủ Server kích hoạt hoặc khóa tài khoản Owner ('Hoạt động', 'Tạm khóa')."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE OWNER_DOANHNGHIEP SET TrangThai = ? WHERE MaOwner = ?", (status, ma_owner))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[SQL SET OWNER STATUS ERROR]: {e}")
        try: conn.close()
        except: pass
        return False

# ==============================================================================
# 2. QUẢN LÝ TẦNG 2: OWNER (CHỦ DOANH NGHIỆP / NHÀ XƯỞNG)
# ==============================================================================
def verify_owner_login(username, password):
    """Xác thực tài khoản Owner đăng nhập app."""
    conn = get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MaOwner, TenDoanhNghiep, NguoiDaiDien, TrangThai 
            FROM OWNER_DOANHNGHIEP 
            WHERE Username = ? AND PasswordHash = ?
        """, (username, password))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "ma_owner": row[0],
                "ten_doanh_nghiep": row[1],
                "nguoi_dai_dien": row[2],
                "trang_thai": row[3]
            }
    except Exception as e:
        print(f"[SQL OWNER LOGIN ERROR]: {e}")
        try: conn.close()
        except: pass
    return None

def get_shift_config_db(ma_owner="OWN01"):
    """Lấy cấu hình ca làm việc của từng Owner từ SQL Server."""
    conn = get_connection()
    default_cfg = {
        "shift_name": "Ca Sáng Hành Chính",
        "shift_start": "07:30",
        "grace_period_mins": 15,
        "shift_end": "17:00"
    }
    if not conn:
        return default_cfg
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TenCa, CONVERT(VARCHAR(5), GioBatDau, 108), PhutAnHan, CONVERT(VARCHAR(5), GioKetThuc, 108)
            FROM CA_LAM_VIEC WHERE MaOwner = ?
        """, (ma_owner,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "shift_name": row[0] or default_cfg["shift_name"],
                "shift_start": row[1] or default_cfg["shift_start"],
                "grace_period_mins": int(row[2]) if row[2] is not None else 15,
                "shift_end": row[3] or default_cfg["shift_end"]
            }
    except Exception as e:
        print(f"[SQL GET SHIFT ERROR]: {e}")
        try: conn.close()
        except: pass
    return default_cfg

def save_shift_config_db(ma_owner, shift_start, grace_mins, shift_end="17:00", shift_name="Ca Sáng"):
    """Lưu cấu hình ca làm việc cho Owner vào SQL Server."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ID FROM CA_LAM_VIEC WHERE MaOwner = ?", (ma_owner,))
        if cursor.fetchone():
            cursor.execute("""
                UPDATE CA_LAM_VIEC 
                SET TenCa = ?, GioBatDau = ?, PhutAnHan = ?, GioKetThuc = ?
                WHERE MaOwner = ?
            """, (shift_name, shift_start, int(grace_mins), shift_end, ma_owner))
        else:
            cursor.execute("""
                INSERT INTO CA_LAM_VIEC (MaOwner, TenCa, GioBatDau, PhutAnHan, GioKetThuc)
                VALUES (?, ?, ?, ?, ?)
            """, (ma_owner, shift_name, shift_start, int(grace_mins), shift_end))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[SQL SAVE SHIFT ERROR]: {e}")
        try: conn.close()
        except: pass
        return False

# ==============================================================================
# 3. QUẢN LÝ TẦNG 3: CÔNG NHÂN, ĐIỂM DANH & VI PHẠM (THEO TỪNG OWNER)
# ==============================================================================
def get_today_checked_in_workers(ma_owner="OWN01"):
    """Lấy danh sách công nhân đã điểm danh hôm nay của Owner để chống trùng lặp."""
    checked_set = set()
    conn = get_connection()
    if not conn:
        return checked_set
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT d.MaCN, ISNULL(c.HoTen, d.MaCN)
            FROM DIEMDANH d
            LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
            WHERE d.MaOwner = ? AND CAST(d.NgayDD AS DATE) = ?
        """, (ma_owner, today_str))
        for r in cursor.fetchall():
            if r[0]: checked_set.add(r[0])
            if r[1]: checked_set.add(r[1])
        conn.close()
    except Exception as e:
        print(f"[SQL GET TODAY CHECKIN ERROR]: {e}")
        try: conn.close()
        except: pass
    return checked_set

def log_attendance_db(ma_owner, worker_id, worker_name=""):
    """Ghi nhận điểm danh vào SQL Server theo đúng ca làm việc của Owner."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    shift_cfg = get_shift_config_db(ma_owner)
    shift_start_str = shift_cfg.get("shift_start", "07:30")
    grace_mins = int(shift_cfg.get("grace_period_mins", 15))
    
    is_late = False
    late_mins = 0
    try:
        check_t = datetime.strptime(time_str, "%H:%M:%S").time()
        start_t = datetime.strptime(shift_start_str, "%H:%M").time()
        check_total_mins = check_t.hour * 60 + check_t.minute
        start_total_mins = start_t.hour * 60 + start_t.minute + grace_mins
        if check_total_mins > start_total_mins:
            is_late = True
            late_mins = check_total_mins - (start_t.hour * 60 + start_t.minute)
    except Exception:
        pass
        
    if worker_id and worker_id != "UNKNOWN":
        status = f"Đi trễ ({late_mins}p)" if is_late else "Đúng giờ"
        note = f"Trễ {late_mins}p (Quy định: {shift_start_str})" if is_late else f"Đúng giờ ({shift_cfg.get('shift_name', 'Ca sáng')})"
    else:
        status = "Không hợp lệ"
        note = "Người lạ chưa đăng ký"
        
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        # Đảm bảo công nhân có trong xưởng
        if worker_id and worker_id != "UNKNOWN":
            cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaCN = ? AND MaOwner = ?", (worker_id, ma_owner))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO CONGNHAN (MaCN, MaOwner, HoTen) VALUES (?, ?, ?)", (worker_id, ma_owner, worker_name or worker_id))
                conn.commit()

        cursor.execute("""
            INSERT INTO DIEMDANH (MaOwner, MaCN, NgayDD, GioDD, TrangThai, PhuongThuc, GhiChu)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ma_owner, worker_id, date_str, time_str, status, "Khuôn mặt AI", note))
        conn.commit()
        conn.close()
        print(f"[SQL SERVER - DIEM DANH] Owner [{ma_owner}]: [{worker_id}] {worker_name} -> {status}")
        return True
    except Exception as e:
        print(f"[SQL ATTENDANCE ERROR]: {e}")
        try: conn.close()
        except: pass
        return False

def log_violation_db(ma_owner, worker_id, worker_name, violation_details, image_filename, severity_level="Cảnh báo"):
    """Ghi nhận vi phạm an toàn vào SQL Server gắn với Owner."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    conn = get_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if worker_id and worker_id != "UNKNOWN":
            cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaCN = ? AND MaOwner = ?", (worker_id, ma_owner))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO CONGNHAN (MaCN, MaOwner, HoTen) VALUES (?, ?, ?)", (worker_id, ma_owner, worker_name or worker_id))
                conn.commit()

        macn_val = worker_id if worker_id != "UNKNOWN" else None
        muc_do = severity_level if severity_level in ["Nhắc nhở", "Cảnh báo", "Nghiêm trọng"] else "Cảnh báo"
        trang_thai_xl = "Cảnh báo" if muc_do == "Cảnh báo" else ("Kỷ luật" if muc_do == "Nghiêm trọng" else "Nhắc nhở")
        
        cursor.execute("""
            INSERT INTO VIPHAMANTOAN (MaOwner, MaCN, NgayViPham, GioViPham, LoiViPham, MinhChung, MucDoCanhBao, TrangThaiXuLy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ma_owner, macn_val, date_str, time_str, violation_details, image_filename, muc_do, trang_thai_xl))
        conn.commit()
        conn.close()
        print(f"[SQL SERVER - VI PHAM] Owner [{ma_owner}]: [{worker_id}] {worker_name} - {violation_details}")
        return True
    except Exception as e:
        print(f"[SQL VIOLATION ERROR]: {e}")
        try: conn.close()
        except: pass
        return False

def get_attendance_df(ma_owner="OWN01"):
    """Lấy dữ liệu điểm danh theo Owner thành DataFrame."""
    conn = get_connection()
    if not conn:
        return pd.DataFrame()
    try:
        cursor = conn.cursor()
        query = """
            SELECT d.ID AS [STT], 
                   CONVERT(VARCHAR(10), d.NgayDD, 103) AS [Ngày], 
                   CONVERT(VARCHAR(8), d.GioDD, 108) AS [Giờ Điểm Danh], 
                   d.MaCN AS [Mã Công Nhân], 
                   ISNULL(c.HoTen, N'Chưa rõ') AS [Họ và Tên Công Nhân], 
                   d.TrangThai AS [Trạng Thái], 
                   d.PhuongThuc AS [Phương Thức Nhận Diện]
            FROM DIEMDANH d
            LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
            WHERE d.MaOwner = ?
            ORDER BY d.ID DESC
        """
        cursor.execute(query, (ma_owner,))
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        df = pd.DataFrame([list(r) for r in rows], columns=columns)
        conn.close()
        return df
    except Exception as e:
        print(f"[SQL FETCH ATTENDANCE ERROR]: {e}")
        try: conn.close()
        except: pass
        return pd.DataFrame()

def get_violations_df(ma_owner="OWN01"):
    """Lấy dữ liệu vi phạm an toàn theo Owner thành DataFrame."""
    conn = get_connection()
    if not conn:
        return pd.DataFrame()
    try:
        cursor = conn.cursor()
        query = """
            SELECT v.ID AS [STT], 
                   CONVERT(VARCHAR(10), v.NgayViPham, 103) AS [Ngày], 
                   CONVERT(VARCHAR(8), v.GioViPham, 108) AS [Giờ Vi Phạm], 
                   ISNULL(v.MaCN, 'UNKNOWN') AS [Mã Công Nhân], 
                   ISNULL(c.HoTen, N'Người lạ (Unknown)') AS [Họ và Tên Công Nhân], 
                   v.LoiViPham AS [Lỗi Vi Phạm Cụ Thể], 
                   v.MucDoCanhBao AS [Mức Độ Cảnh Báo],
                   v.MinhChung AS [File Ảnh Bằng Chứng],
                   v.TrangThaiXuLy AS [Trạng Thái Xử Lý]
            FROM VIPHAMANTOAN v
            LEFT JOIN CONGNHAN c ON v.MaCN = c.MaCN AND v.MaOwner = c.MaOwner
            WHERE v.MaOwner = ?
            ORDER BY v.ID DESC
        """
        cursor.execute(query, (ma_owner,))
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        df = pd.DataFrame([list(r) for r in rows], columns=columns)
        conn.close()
        return df
    except Exception as e:
        print(f"[SQL FETCH VIOLATIONS ERROR]: {e}")
        try: conn.close()
        except: pass
        return pd.DataFrame()

def get_violations_list(ma_owner="OWN01", date_filter=None, error_type=None):
    """Lấy danh sách vi phạm của Owner dưới dạng List[Dict] kèm bộ lọc linh hoạt."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        query = """
            SELECT v.ID, 
                   CONVERT(VARCHAR(10), v.NgayViPham, 103) AS Ngay, 
                   CONVERT(VARCHAR(8), v.GioViPham, 108) AS Gio, 
                   ISNULL(v.MaCN, 'UNKNOWN') AS MaCN, 
                   ISNULL(c.HoTen, N'Người lạ (Chưa ĐK)') AS HoTen, 
                   v.LoiViPham, 
                   v.MucDoCanhBao,
                   ISNULL(v.MinhChung, '') AS MinhChung,
                   ISNULL(v.TrangThaiXuLy, N'Cảnh báo') AS TrangThaiXuLy
            FROM VIPHAMANTOAN v
            LEFT JOIN CONGNHAN c ON v.MaCN = c.MaCN AND v.MaOwner = c.MaOwner
            WHERE v.MaOwner = ?
        """
        params = [ma_owner]
        if date_filter:
            query += " AND CAST(v.NgayViPham AS DATE) = ?"
            params.append(date_filter)
        if error_type and error_type != "ALL":
            if error_type == "HELMET":
                query += " AND (v.LoiViPham LIKE '%mu%' OR v.LoiViPham LIKE '%Mu%')"
            elif error_type == "VEST":
                query += " AND (v.LoiViPham LIKE '%ao%' OR v.LoiViPham LIKE '%Ao%')"
            elif error_type == "SHOES":
                query += " AND (v.LoiViPham LIKE '%giay%' OR v.LoiViPham LIKE '%Giay%')"
        
        query += " ORDER BY v.ID DESC"
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "ngay": r[1],
                "gio": r[2],
                "ma_cn": r[3],
                "ho_ten": r[4],
                "loi_vi_pham": r[5],
                "muc_do": r[6],
                "minh_chung": r[7],
                "trang_thai": r[8]
            })
        conn.close()
        return result
    except Exception as e:
        print(f"[SQL GET VIOLATIONS LIST ERROR]: {e}")
        try: conn.close()
        except: pass
        return []

def get_attendance_list(ma_owner="OWN01", date_filter=None, status_filter=None):
    """Lấy danh sách điểm danh của Owner dưới dạng List[Dict] kèm bộ lọc linh hoạt."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        query = """
            SELECT d.ID, 
                   CONVERT(VARCHAR(10), d.NgayDD, 103) AS Ngay, 
                   CONVERT(VARCHAR(8), d.GioDD, 108) AS Gio, 
                   ISNULL(d.MaCN, 'CN-001') AS MaCN, 
                   ISNULL(c.HoTen, N'Pham Van Khiem') AS HoTen, 
                   d.TrangThai, 
                   d.PhuongThuc
            FROM DIEMDANH d
            LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
            WHERE d.MaOwner = ?
        """
        params = [ma_owner]
        if date_filter:
            query += " AND CAST(d.NgayDD AS DATE) = ?"
            params.append(date_filter)
        if status_filter and status_filter != "ALL":
            if status_filter == "ON_TIME":
                query += " AND (d.TrangThai = N'Hợp lệ' OR d.TrangThai LIKE N'%Đúng%')"
            elif status_filter == "LATE":
                query += " AND d.TrangThai LIKE N'%trễ%'"
                
        query += " ORDER BY d.ID DESC"
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "ngay": r[1],
                "gio": r[2],
                "ma_cn": r[3],
                "ho_ten": r[4],
                "trang_thai": r[5],
                "phuong_thuc": r[6]
            })
        conn.close()
        return result
    except Exception as e:
        print(f"[SQL GET ATTENDANCE LIST ERROR]: {e}")
        try: conn.close()
        except: pass
        return []

def get_analytics_summary(ma_owner="OWN01", period="day", val=None):
    """Thống kê chuyên sâu theo Ngày / Tháng / Năm cho Owner."""
    conn = get_connection()
    if not conn:
        return {}
    try:
        cursor = conn.cursor()
        now = datetime.now()
        
        if period == "day":
            target_date = val if val else now.strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongDD,
                    SUM(CASE WHEN TrangThai = N'Hợp lệ' OR TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH
                WHERE MaOwner = ? AND CAST(NgayDD AS DATE) = ?
            """, (ma_owner, target_date))
            att_stat = cursor.fetchone()
            total_att = att_stat[0] or 0
            on_time = att_stat[1] or 0
            late = att_stat[2] or 0
            on_time_rate = round((on_time / total_att) * 100, 1) if total_att > 0 else 0.0
            late_rate = round((late / total_att) * 100, 1) if total_att > 0 else 0.0
            
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongVio,
                    SUM(CASE WHEN LoiViPham LIKE '%mu%' OR LoiViPham LIKE '%Mu%' THEN 1 ELSE 0 END) AS ThieuMu,
                    SUM(CASE WHEN LoiViPham LIKE '%ao%' OR LoiViPham LIKE '%Ao%' THEN 1 ELSE 0 END) AS ThieuAo,
                    SUM(CASE WHEN LoiViPham LIKE '%giay%' OR LoiViPham LIKE '%Giay%' THEN 1 ELSE 0 END) AS ThieuGiay
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND CAST(NgayViPham AS DATE) = ?
            """, (ma_owner, target_date))
            vio_stat = cursor.fetchone()
            total_vio = vio_stat[0] or 0
            no_helmet = vio_stat[1] or 0
            no_vest = vio_stat[2] or 0
            no_shoes = vio_stat[3] or 0
            
            cursor.execute("""
                SELECT DATEPART(HOUR, GioDD) AS Gio, COUNT(*) AS SoLuong
                FROM DIEMDANH
                WHERE MaOwner = ? AND CAST(NgayDD AS DATE) = ?
                GROUP BY DATEPART(HOUR, GioDD)
            """, (ma_owner, target_date))
            att_by_hour = dict(cursor.fetchall())
            
            cursor.execute("""
                SELECT DATEPART(HOUR, GioViPham) AS Gio, COUNT(*) AS SoLuong
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND CAST(NgayViPham AS DATE) = ?
                GROUP BY DATEPART(HOUR, GioViPham)
            """, (ma_owner, target_date))
            vio_by_hour = dict(cursor.fetchall())
            
            timeline = []
            for h in range(7, 18):
                timeline.append({
                    "label": f"{h:02d}:00",
                    "att": att_by_hour.get(h, 0),
                    "vio": vio_by_hour.get(h, 0)
                })

            cursor.execute("""
                SELECT 
                    ISNULL(d.MaCN, 'CN-001') AS MaCN,
                    ISNULL(c.HoTen, N'Pham Van Khiem') AS HoTen,
                    COUNT(*) AS TongCa,
                    SUM(CASE WHEN d.TrangThai = N'Hợp lệ' OR d.TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN d.TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH d
                LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
                WHERE d.MaOwner = ? AND CAST(d.NgayDD AS DATE) = ?
                GROUP BY d.MaCN, c.HoTen
                ORDER BY TongCa DESC
            """, (ma_owner, target_date))
            punctuality = []
            for r in cursor.fetchall():
                t_ca = r[2] or 0
                dg = r[3] or 0
                dt = r[4] or 0
                pct = round((dg / t_ca) * 100, 1) if t_ca > 0 else 0.0
                dg_label = "Xuất sắc" if pct >= 90 else ("Đúng giờ tốt" if pct >= 70 else "Cần nhắc nhở")
                punctuality.append({
                    "ma_cn": r[0], "ho_ten": r[1], "tong_ca": t_ca, "dung_gio": dg, "di_tre": dt,
                    "ty_le": pct, "danh_gia": dg_label
                })
                
            compliance_rate = 100.0 if (total_att + total_vio == 0) else round((total_att / (total_att + total_vio)) * 100, 1)

            conn.close()
            return {
                "period": "day",
                "target": target_date,
                "total_att": total_att,
                "on_time": on_time,
                "late": late,
                "on_time_rate": on_time_rate,
                "late_rate": late_rate,
                "punctuality": punctuality,
                "total_vio": total_vio,
                "no_helmet": no_helmet,
                "no_vest": no_vest,
                "no_shoes": no_shoes,
                "compliance_rate": compliance_rate,
                "timeline": timeline,
                "top_violators": []
            }

        elif period == "month":
            if not val or "-" not in val:
                val = now.strftime("%Y-%m")
            y_str, m_str = val.split("-")[:2]
            y, m = int(y_str), int(m_str)
            
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongDD,
                    SUM(CASE WHEN TrangThai = N'Hợp lệ' OR TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH
                WHERE MaOwner = ? AND YEAR(NgayDD) = ? AND MONTH(NgayDD) = ?
            """, (ma_owner, y, m))
            att_stat = cursor.fetchone()
            total_att = att_stat[0] or 0
            on_time = att_stat[1] or 0
            late = att_stat[2] or 0
            on_time_rate = round((on_time / total_att) * 100, 1) if total_att > 0 else 0.0
            late_rate = round((late / total_att) * 100, 1) if total_att > 0 else 0.0
            
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongVio,
                    SUM(CASE WHEN LoiViPham LIKE '%mu%' OR LoiViPham LIKE '%Mu%' THEN 1 ELSE 0 END) AS ThieuMu,
                    SUM(CASE WHEN LoiViPham LIKE '%ao%' OR LoiViPham LIKE '%Ao%' THEN 1 ELSE 0 END) AS ThieuAo,
                    SUM(CASE WHEN LoiViPham LIKE '%giay%' OR LoiViPham LIKE '%Giay%' THEN 1 ELSE 0 END) AS ThieuGiay
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND YEAR(NgayViPham) = ? AND MONTH(NgayViPham) = ?
            """, (ma_owner, y, m))
            vio_stat = cursor.fetchone()
            total_vio = vio_stat[0] or 0
            no_helmet = vio_stat[1] or 0
            no_vest = vio_stat[2] or 0
            no_shoes = vio_stat[3] or 0
            
            cursor.execute("""
                SELECT DAY(NgayDD) AS Ngay, COUNT(*) AS SoLuong
                FROM DIEMDANH
                WHERE MaOwner = ? AND YEAR(NgayDD) = ? AND MONTH(NgayDD) = ?
                GROUP BY DAY(NgayDD)
            """, (ma_owner, y, m))
            att_by_day = dict(cursor.fetchall())
            
            cursor.execute("""
                SELECT DAY(NgayViPham) AS Ngay, COUNT(*) AS SoLuong
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND YEAR(NgayViPham) = ? AND MONTH(NgayViPham) = ?
                GROUP BY DAY(NgayViPham)
            """, (ma_owner, y, m))
            vio_by_day = dict(cursor.fetchall())
            
            timeline = []
            for d in range(1, 32):
                if d in att_by_day or d in vio_by_day:
                    timeline.append({
                        "label": f"Ngày {d:02d}",
                        "att": att_by_day.get(d, 0),
                        "vio": vio_by_day.get(d, 0)
                    })
            if not timeline:
                for d in range(1, 6):
                    timeline.append({"label": f"Ngày {d:02d}", "att": 0, "vio": 0})

            cursor.execute("""
                SELECT TOP 5 ISNULL(v.MaCN, 'UNKNOWN') AS MaCN, ISNULL(c.HoTen, N'Người lạ') AS HoTen, COUNT(*) AS SoLan
                FROM VIPHAMANTOAN v
                LEFT JOIN CONGNHAN c ON v.MaCN = c.MaCN AND v.MaOwner = c.MaOwner
                WHERE v.MaOwner = ? AND YEAR(v.NgayViPham) = ? AND MONTH(v.NgayViPham) = ?
                GROUP BY v.MaCN, c.HoTen
                ORDER BY SoLan DESC
            """, (ma_owner, y, m))
            top_violators = [{"ma_cn": r[0], "ho_ten": r[1], "so_lan": r[2]} for r in cursor.fetchall()]

            cursor.execute("""
                SELECT 
                    ISNULL(d.MaCN, 'CN-001') AS MaCN,
                    ISNULL(c.HoTen, N'Pham Van Khiem') AS HoTen,
                    COUNT(*) AS TongCa,
                    SUM(CASE WHEN d.TrangThai = N'Hợp lệ' OR d.TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN d.TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH d
                LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
                WHERE d.MaOwner = ? AND YEAR(d.NgayDD) = ? AND MONTH(d.NgayDD) = ?
                GROUP BY d.MaCN, c.HoTen
                ORDER BY TongCa DESC
            """, (ma_owner, y, m))
            punctuality = []
            for r in cursor.fetchall():
                t_ca = r[2] or 0
                dg = r[3] or 0
                dt = r[4] or 0
                pct = round((dg / t_ca) * 100, 1) if t_ca > 0 else 0.0
                dg_label = "Xuất sắc" if pct >= 90 else ("Đúng giờ tốt" if pct >= 70 else "Cần nhắc nhở")
                punctuality.append({
                    "ma_cn": r[0], "ho_ten": r[1], "tong_ca": t_ca, "dung_gio": dg, "di_tre": dt,
                    "ty_le": pct, "danh_gia": dg_label
                })
            
            compliance_rate = 100.0 if (total_att + total_vio == 0) else round((total_att / (total_att + total_vio)) * 100, 1)

            conn.close()
            return {
                "period": "month",
                "target": f"Tháng {m:02d}/{y}",
                "total_att": total_att,
                "on_time": on_time,
                "late": late,
                "on_time_rate": on_time_rate,
                "late_rate": late_rate,
                "punctuality": punctuality,
                "total_vio": total_vio,
                "no_helmet": no_helmet,
                "no_vest": no_vest,
                "no_shoes": no_shoes,
                "compliance_rate": compliance_rate,
                "timeline": timeline,
                "top_violators": top_violators
            }

        elif period == "year":
            y = int(val) if val and str(val).isdigit() else now.year
            
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongDD,
                    SUM(CASE WHEN TrangThai = N'Hợp lệ' OR TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH
                WHERE MaOwner = ? AND YEAR(NgayDD) = ?
            """, (ma_owner, y))
            att_stat = cursor.fetchone()
            total_att = att_stat[0] or 0
            on_time = att_stat[1] or 0
            late = att_stat[2] or 0
            on_time_rate = round((on_time / total_att) * 100, 1) if total_att > 0 else 0.0
            late_rate = round((late / total_att) * 100, 1) if total_att > 0 else 0.0
            
            cursor.execute("""
                SELECT 
                    COUNT(*) AS TongVio,
                    SUM(CASE WHEN LoiViPham LIKE '%mu%' OR LoiViPham LIKE '%Mu%' THEN 1 ELSE 0 END) AS ThieuMu,
                    SUM(CASE WHEN LoiViPham LIKE '%ao%' OR LoiViPham LIKE '%Ao%' THEN 1 ELSE 0 END) AS ThieuAo,
                    SUM(CASE WHEN LoiViPham LIKE '%giay%' OR LoiViPham LIKE '%Giay%' THEN 1 ELSE 0 END) AS ThieuGiay
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND YEAR(NgayViPham) = ?
            """, (ma_owner, y))
            vio_stat = cursor.fetchone()
            total_vio = vio_stat[0] or 0
            no_helmet = vio_stat[1] or 0
            no_vest = vio_stat[2] or 0
            no_shoes = vio_stat[3] or 0
            
            cursor.execute("""
                SELECT MONTH(NgayDD) AS Thang, COUNT(*) AS SoLuong
                FROM DIEMDANH
                WHERE MaOwner = ? AND YEAR(NgayDD) = ?
                GROUP BY MONTH(NgayDD)
            """, (ma_owner, y))
            att_by_month = dict(cursor.fetchall())
            
            cursor.execute("""
                SELECT MONTH(NgayViPham) AS Thang, COUNT(*) AS SoLuong
                FROM VIPHAMANTOAN
                WHERE MaOwner = ? AND YEAR(NgayViPham) = ?
                GROUP BY MONTH(NgayViPham)
            """, (ma_owner, y))
            vio_by_month = dict(cursor.fetchall())
            
            timeline = []
            for m_idx in range(1, 13):
                timeline.append({
                    "label": f"T{m_idx:02d}",
                    "att": att_by_month.get(m_idx, 0),
                    "vio": vio_by_month.get(m_idx, 0)
                })
                
            cursor.execute("""
                SELECT TOP 5 ISNULL(v.MaCN, 'UNKNOWN') AS MaCN, ISNULL(c.HoTen, N'Người lạ') AS HoTen, COUNT(*) AS SoLan
                FROM VIPHAMANTOAN v
                LEFT JOIN CONGNHAN c ON v.MaCN = c.MaCN AND v.MaOwner = c.MaOwner
                WHERE v.MaOwner = ? AND YEAR(v.NgayViPham) = ?
                GROUP BY v.MaCN, c.HoTen
                ORDER BY SoLan DESC
            """, (ma_owner, y))
            top_violators = [{"ma_cn": r[0], "ho_ten": r[1], "so_lan": r[2]} for r in cursor.fetchall()]

            cursor.execute("""
                SELECT 
                    ISNULL(d.MaCN, 'CN-001') AS MaCN,
                    ISNULL(c.HoTen, N'Pham Van Khiem') AS HoTen,
                    COUNT(*) AS TongCa,
                    SUM(CASE WHEN d.TrangThai = N'Hợp lệ' OR d.TrangThai LIKE N'%Đúng%' THEN 1 ELSE 0 END) AS DungGio,
                    SUM(CASE WHEN d.TrangThai LIKE N'%trễ%' THEN 1 ELSE 0 END) AS DiTre
                FROM DIEMDANH d
                LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
                WHERE d.MaOwner = ? AND YEAR(d.NgayDD) = ?
                GROUP BY d.MaCN, c.HoTen
                ORDER BY TongCa DESC
            """, (ma_owner, y))
            punctuality = []
            for r in cursor.fetchall():
                t_ca = r[2] or 0
                dg = r[3] or 0
                dt = r[4] or 0
                pct = round((dg / t_ca) * 100, 1) if t_ca > 0 else 0.0
                dg_label = "Xuất sắc" if pct >= 90 else ("Đúng giờ tốt" if pct >= 70 else "Cần nhắc nhở")
                punctuality.append({
                    "ma_cn": r[0], "ho_ten": r[1], "tong_ca": t_ca, "dung_gio": dg, "di_tre": dt,
                    "ty_le": pct, "danh_gia": dg_label
                })
            
            compliance_rate = 100.0 if (total_att + total_vio == 0) else round((total_att / (total_att + total_vio)) * 100, 1)

            conn.close()
            return {
                "period": "year",
                "target": f"Năm {y}",
                "total_att": total_att,
                "on_time": on_time,
                "late": late,
                "on_time_rate": on_time_rate,
                "late_rate": late_rate,
                "punctuality": punctuality,
                "total_vio": total_vio,
                "no_helmet": no_helmet,
                "no_vest": no_vest,
                "no_shoes": no_shoes,
                "compliance_rate": compliance_rate,
                "timeline": timeline,
                "top_violators": top_violators
            }
    except Exception as e:
        print(f"[SQL ANALYTICS ERROR]: {e}")
        try: conn.close()
        except: pass
        return {}

def get_next_worker_id(ma_owner="OWN01"):
    """Tự động tính mã công nhân tiếp theo (ví dụ: CN-003)."""
    conn = get_connection()
    if not conn:
        return "CN-001"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaOwner = ?", (ma_owner,))
        rows = cursor.fetchall()
        max_num = 0
        for r in rows:
            m = str(r[0]).strip().upper()
            if m.startswith("CN-"):
                num_str = m[3:]
                if num_str.isdigit():
                    max_num = max(max_num, int(num_str))
        conn.close()
        return f"CN-{(max_num + 1):03d}"
    except Exception:
        try: conn.close()
        except: pass
        return "CN-001"

def add_worker_db(ma_owner, ma_cn, ho_ten, phong_ban="", so_dt="", anh_chan_dung=""):
    """Thêm mới hoặc cập nhật công nhân vào SQL Server."""
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối cơ sở dữ liệu SQL Server!"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MaCN FROM CONGNHAN WHERE MaOwner = ? AND MaCN = ?", (ma_owner, ma_cn))
        if cursor.fetchone():
            cursor.execute("""
                UPDATE CONGNHAN 
                SET HoTen = ?, PhongBan = ?, SoDT = ?, AnhChanDung = ISNULL(NULLIF(?, ''), AnhChanDung)
                WHERE MaOwner = ? AND MaCN = ?
            """, (ho_ten, phong_ban, so_dt, anh_chan_dung, ma_owner, ma_cn))
            conn.commit()
            conn.close()
            return True, f"Đã cập nhật thông tin công nhân {ma_cn} ({ho_ten}) thành công!"
        else:
            cursor.execute("""
                INSERT INTO CONGNHAN (MaCN, MaOwner, HoTen, PhongBan, SoDT, AnhChanDung, NgayTao)
                VALUES (?, ?, ?, ?, ?, ?, GETDATE())
            """, (ma_cn, ma_owner, ho_ten, phong_ban, so_dt, anh_chan_dung))
            conn.commit()
            conn.close()
            return True, f"Đã thêm công nhân {ma_cn} ({ho_ten}) vào hệ thống thành công!"
    except Exception as e:
        try: conn.close()
        except: pass
        return False, f"Lỗi SQL: {e}"

def update_worker_db(ma_owner, ma_cn, ho_ten, phong_ban="", so_dt="", trang_thai="Đang làm việc", anh_chan_dung=""):
    """Cập nhật thông tin toàn diện của công nhân trong SQL Server."""
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối cơ sở dữ liệu SQL Server!"
    try:
        cursor = conn.cursor()
        if anh_chan_dung:
            cursor.execute("""
                UPDATE CONGNHAN 
                SET HoTen = ?, PhongBan = ?, SoDT = ?, TrangThai = ?, AnhChanDung = ?
                WHERE MaOwner = ? AND MaCN = ?
            """, (ho_ten, phong_ban, so_dt, trang_thai, anh_chan_dung, ma_owner, ma_cn))
        else:
            cursor.execute("""
                UPDATE CONGNHAN 
                SET HoTen = ?, PhongBan = ?, SoDT = ?, TrangThai = ?
                WHERE MaOwner = ? AND MaCN = ?
            """, (ho_ten, phong_ban, so_dt, trang_thai, ma_owner, ma_cn))
        conn.commit()
        conn.close()
        return True, f"Đã cập nhật thông tin công nhân {ma_cn} ({ho_ten}) thành công!"
    except Exception as e:
        try: conn.close()
        except: pass
        return False, f"Lỗi SQL: {e}"

def delete_worker_db(ma_owner, ma_cn):
    """Xóa công nhân khỏi CSDL của Owner (gỡ liên kết khóa ngoại an toàn)."""
    conn = get_connection()
    if not conn:
        return False, "Không thể kết nối cơ sở dữ liệu SQL Server!"
    try:
        cursor = conn.cursor()
        # Gỡ liên kết khóa ngoại trước khi xóa để không bị lỗi REFERENCE constraint
        cursor.execute("UPDATE DIEMDANH SET MaCN = NULL WHERE MaOwner = ? AND MaCN = ?", (ma_owner, ma_cn))
        cursor.execute("UPDATE VIPHAMANTOAN SET MaCN = NULL WHERE MaOwner = ? AND MaCN = ?", (ma_owner, ma_cn))
        cursor.execute("DELETE FROM CONGNHAN WHERE MaOwner = ? AND MaCN = ?", (ma_owner, ma_cn))
        conn.commit()
        conn.close()
        return True, f"Đã xóa công nhân {ma_cn} thành công!"
    except Exception as e:
        try: conn.close()
        except: pass
        return False, f"Lỗi SQL khi xóa: {e}"

def get_workers_list(ma_owner="OWN01"):
    """Lấy danh sách công nhân của Owner."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MaCN, HoTen, PhongBan, SoDT, AnhChanDung, 
                   CONVERT(VARCHAR(10), NgayTao, 103) AS NgayTao,
                   ISNULL(TrangThai, N'Đang làm việc') AS TrangThai
            FROM CONGNHAN WHERE MaOwner = ?
            ORDER BY NgayTao DESC
        """, (ma_owner,))
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        result = [dict(zip(columns, r)) for r in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"[SQL GET WORKERS ERROR]: {e}")
        try: conn.close()
        except: pass
        return []

def get_dynamic_excel_bytes(ma_owner="OWN01"):
    """Tạo báo cáo Excel 2 sheet tải trực tiếp từ SQL Server cho Owner."""
    import io
    df_att = get_attendance_df(ma_owner)
    df_vio = get_violations_df(ma_owner)
    
    wb = openpyxl.Workbook()
    
    # Sheet 1: DIEM_DANH
    ws1 = wb.active
    ws1.title = "DIEM_DANH"
    headers_att = ["STT", "Ngày", "Giờ Điểm Danh", "Mã Công Nhân", "Họ và Tên Công Nhân", "Trạng Thái", "Phương Thức Nhận Diện"]
    ws1.append(headers_att)
    
    fill_blue = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for cell in ws1[1]:
        cell.fill = fill_blue
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    for _, row in df_att.iterrows():
        ws1.append(list(row))
        
    # Sheet 2: VI_PHAM_AN_TOAN
    ws2 = wb.create_sheet(title="VI_PHAM_AN_TOAN")
    headers_vio = ["STT", "Ngày", "Giờ Vi Phạm", "Mã Công Nhân", "Họ và Tên Công Nhân", "Lỗi Vi Phạm Cụ Thể", "Mức Độ Cảnh Báo", "File Ảnh Bằng Chứng", "Trạng Thái Xử Lý"]
    ws2.append(headers_vio)
    
    fill_red = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    for cell in ws2[1]:
        cell.fill = fill_red
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    for _, row in df_vio.iterrows():
        ws2.append(list(row))
        
    for ws in [ws1, ws2]:
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 13)
            
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

def get_admin_full_system_excel_bytes():
    """Tạo báo cáo Excel toàn hệ thống chi tiết 4 sheet chuyên nghiệp dành riêng cho Chủ Server."""
    import io
    conn = get_connection()
    if not conn:
        return b""
    
    wb = openpyxl.Workbook()
    
    # Font & Fills
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center")
    
    fill_navy = PatternFill(start_color="0A2540", end_color="0A2540", fill_type="solid")
    fill_blue = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    fill_red = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    fill_green = PatternFill(start_color="196F3D", end_color="196F3D", fill_type="solid")
    
    cur = conn.cursor()
    
    # SHEET 1: TỔNG QUAN DOANH NGHIỆP & THUÊ BAO
    ws1 = wb.active
    ws1.title = "TONG_QUAN_DOANH_NGHIEP"
    headers_dn = [
        "STT", "Mã Xưởng", "Tên Doanh Nghiệp / Nhà Xưởng", "Người Đại Diện", 
        "Số Điện Thoại", "Email Liên Hệ", "Tài Khoản App", "Gói Dịch Vụ", 
        "Ngày Kích Hoạt", "Ngày Hết Hạn", "Trạng Thái", "Quân Số (Công Nhân)", 
        "Tổng Lượt Điểm Danh", "Tổng Vi Phạm An Toàn"
    ]
    ws1.append(headers_dn)
    for cell in ws1[1]:
        cell.fill = fill_navy
        cell.font = font_header
        cell.alignment = align_center
        
    cur.execute("""
        SELECT o.MaOwner, o.TenDoanhNghiep, o.NguoiDaiDien, o.SoDT, ISNULL(o.Email, N'---'), 
               o.Username, o.GoiDichVu, 
               CONVERT(VARCHAR(10), o.NgayKichHoat, 103),
               ISNULL(CONVERT(VARCHAR(10), o.NgayHetHan, 103), N'Vĩnh viễn'),
               o.TrangThai,
               (SELECT COUNT(*) FROM CONGNHAN c WHERE c.MaOwner = o.MaOwner) AS SoCN,
               (SELECT COUNT(*) FROM DIEMDANH d WHERE d.MaOwner = o.MaOwner) AS TongDD,
               (SELECT COUNT(*) FROM VIPHAMANTOAN v WHERE v.MaOwner = o.MaOwner) AS TongVP
        FROM OWNER_DOANHNGHIEP o
        ORDER BY o.MaOwner ASC
    """)
    for idx, r in enumerate(cur.fetchall(), 1):
        ws1.append([idx] + list(r))
        
    # SHEET 2: ĐIỂM DANH TOÀN MẠNG
    ws2 = wb.create_sheet(title="DIEM_DANH_TOAN_MANG")
    headers_att = [
        "STT", "Mã Xưởng", "Tên Doanh Nghiệp", "Ngày", "Giờ Điểm Danh", 
        "Mã Công Nhân", "Họ và Tên", "Phòng Ban", "Trạng Thái", "Phương Thức Nhận Diện"
    ]
    ws2.append(headers_att)
    for cell in ws2[1]:
        cell.fill = fill_blue
        cell.font = font_header
        cell.alignment = align_center
        
    cur.execute("""
        SELECT d.MaOwner, ISNULL(o.TenDoanhNghiep, d.MaOwner),
               CONVERT(VARCHAR(10), d.NgayDD, 103),
               CONVERT(VARCHAR(8), d.GioDD, 108),
               d.MaCN, ISNULL(c.HoTen, N'Chưa rõ'),
               ISNULL(c.PhongBan, N'Chưa cập nhật'),
               d.TrangThai, d.PhuongThuc
        FROM DIEMDANH d
        LEFT JOIN OWNER_DOANHNGHIEP o ON d.MaOwner = o.MaOwner
        LEFT JOIN CONGNHAN c ON d.MaCN = c.MaCN AND d.MaOwner = c.MaOwner
        ORDER BY d.ID DESC
    """)
    for idx, r in enumerate(cur.fetchall(), 1):
        ws2.append([idx] + list(r))
        
    # SHEET 3: VI PHẠM AN TOÀN TOÀN MẠNG
    ws3 = wb.create_sheet(title="VI_PHAM_AN_TOAN_TOAN_MANG")
    headers_vio = [
        "STT", "Mã Xưởng", "Tên Doanh Nghiệp", "Ngày Vi Phạm", "Giờ Vi Phạm", 
        "Mã Công Nhân", "Họ và Tên", "Phòng Ban", "Lỗi Vi Phạm Cụ Thể", 
        "Mức Độ Cảnh Báo", "Trạng Thái Xử Lý", "File Ảnh Minh Chứng"
    ]
    ws3.append(headers_vio)
    for cell in ws3[1]:
        cell.fill = fill_red
        cell.font = font_header
        cell.alignment = align_center
        
    cur.execute("""
        SELECT v.MaOwner, ISNULL(o.TenDoanhNghiep, v.MaOwner),
               CONVERT(VARCHAR(10), v.NgayViPham, 103),
               CONVERT(VARCHAR(8), v.GioViPham, 108),
               v.MaCN, ISNULL(c.HoTen, N'Người lạ / Chưa rõ'),
               ISNULL(c.PhongBan, N'Chưa cập nhật'),
               v.LoiViPham, v.MucDoCanhBao, v.TrangThaiXuLy, v.MinhChung
        FROM VIPHAMANTOAN v
        LEFT JOIN OWNER_DOANHNGHIEP o ON v.MaOwner = o.MaOwner
        LEFT JOIN CONGNHAN c ON v.MaCN = c.MaCN AND v.MaOwner = c.MaOwner
        ORDER BY v.ID DESC
    """)
    for idx, r in enumerate(cur.fetchall(), 1):
        ws3.append([idx] + list(r))

    # SHEET 4: TOÀN BỘ CÔNG NHÂN HỆ THỐNG
    ws4 = wb.create_sheet(title="CONG_NHAN_TOAN_MANG")
    headers_w = [
        "STT", "Mã Xưởng", "Tên Doanh Nghiệp", "Mã Công Nhân", "Họ và Tên", 
        "Phòng Ban / Phân Xưởng", "Số Điện Thoại", "Ngày Đăng Ký", "Đã Đăng Ký AI Face", "Trạng Thái Làm Việc"
    ]
    ws4.append(headers_w)
    for cell in ws4[1]:
        cell.fill = fill_green
        cell.font = font_header
        cell.alignment = align_center
        
    cur.execute("""
        SELECT c.MaOwner, ISNULL(o.TenDoanhNghiep, c.MaOwner),
               c.MaCN, c.HoTen, ISNULL(c.PhongBan, N'Chưa cập nhật'),
               ISNULL(c.SoDT, N'Chưa cập nhật'),
               CONVERT(VARCHAR(10), c.NgayTao, 103),
               CASE WHEN c.AnhChanDung IS NOT NULL THEN N'Đã đăng ký AI Face' ELSE N'Chưa có dữ liệu Face' END,
               c.TrangThai
        FROM CONGNHAN c
        LEFT JOIN OWNER_DOANHNGHIEP o ON c.MaOwner = o.MaOwner
        ORDER BY c.MaOwner ASC, c.MaCN ASC
    """)
    for idx, r in enumerate(cur.fetchall(), 1):
        ws4.append([idx] + list(r))

    # Tự động canh chỉnh độ rộng các cột
    for ws in [ws1, ws2, ws3, ws4]:
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    conn.close()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
