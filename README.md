# SMART HSE VISION - Hệ Thống Điểm Danh & Giám Sát An Toàn Lao Động Đa Doanh Nghiệp (Multi-Tenant SaaS)

Hệ thống quản lý điểm danh khuôn mặt bằng AI và giám sát an toàn lao động (PPE - Personal Protective Equipment) theo thời gian thực dành cho các phân xưởng, nhà máy, công trường và cơ sở y tế.

---

## 🌟 Tính Năng Nổi Bật

### 1. Phân Tầng Đa Doanh Nghiệp (Multi-Tenant Architecture)
* **Tầng 1: Super Admin (Chủ Server)**:
  * Giao diện Sidebar Dashboard chuẩn thị trường SaaS.
  * Giám sát KPIs toàn mạng: Tổng số doanh nghiệp, tổng quân số toàn hệ thống, tổng lượt điểm danh & cảnh báo vi phạm.
  * Quản lý vòng đời xưởng: Tạo xưởng mới, nâng/hạ gói dịch vụ, khóa/mở quyền truy cập, đặt lại mật khẩu.
  * Chế độ **"Vào Xưởng" (Impersonate)**: Cho phép Admin 1 chạm truy cập thẳng vào giao diện vận hành của từng nhà xưởng.
  * Giám sát hạ tầng kỹ thuật: SQL Server Database, bộ nhớ RAM nạp khuôn mặt, mô hình AI Vision.
  * **Xuất Báo Cáo Excel Toàn Mạng (4 Sheets chi tiết)**: Tổng quan doanh nghiệp, Điểm danh toàn mạng, Vi phạm toàn mạng, Danh sách công nhân toàn mạng.
* **Tầng 2: Chủ Doanh Nghiệp / Quản Đốc Nhà Xưởng (Owner)**:
  * Quản lý ca làm việc: Thiết lập giờ bắt đầu, phút ân hạn trễ, giờ tan ca.
  * Quản lý công nhân & Đăng ký khuôn mặt AI.
  * Nhật ký điểm danh thời gian thực (tự động phân loại Đúng giờ / Đi trễ).
  * Danh sách vi phạm an toàn kèm ảnh chụp bằng chứng vi phạm.
  * Biểu đồ phân tích xu hướng tuần/tháng và tỷ lệ tuân thủ PPE.
* **Tầng 3: Công Nhân / Nhân Sự (Worker)**:
  * Điểm danh tự động qua camera nhận diện khuôn mặt thời gian thực (không cần quẹt thẻ hay chạm tay).

### 2. Bộ Máy Trí Tuệ Nhân Tạo (AI Vision Engine)
* **Face Detector**: YuNet (ONNX OpenCV DNN) phát hiện khuôn mặt tốc độ cao, nhận diện được khi ngồi gần/xa webcam.
* **Face Recognizer**: SFace (ONNX Deep Learning) trích xuất đặc trưng vector 128 chiều, so khớp Cosine Distance chuẩn xác.
* **PPE Safety Detector**: YOLOv8 Deep Learning phát hiện Mũ bảo hộ (`helmet`) và Áo phản quang / Áo bảo hộ (`vest`).
* **Kiến trúc Đa Luồng (Decoupled 2-Thread Architecture)**:
  * Luồng 1: Stream video liên tục đạt chuẩn HD 720p mượt mà (30 FPS).
  * Luồng 2: Xử lý suy luận AI chạy ngầm không gây giật lag hay giảm khung hình của camera.

---

## 📁 Cấu Trúc Thư Mục Dự Án

```
DUANCLB/
├── app_core.py                 # File chạy chính: Backend FastAPI & Giao diện Web UI Dashboard
├── database.py                 # Module kết nối SQL Server & xử lý dữ liệu Multi-Tenant
├── DUANCLB.py                  # Module AI Vision (YuNet, SFace, YOLOv8 PPE, FastThreadedCamera)
├── DATADA.sql                  # Script khởi tạo CSDL SQL Server Smart_HSE
├── requirements.txt            # Danh sách thư viện Python cần thiết
├── README.md                   # Tài liệu hướng dẫn sử dụng
├── .gitignore                  # Cấu hình bỏ qua file rác khi đưa lên Git
│
├── models/                     # Thư mục chứa các mô hình Deep Learning
│   ├── face_detection_yunet.onnx
│   ├── face_recognition_sface.onnx
│   └── ppe_yolov8n.pt
│
├── known_faces/                # Thư mục lưu ảnh chân dung công nhân đăng ký nhận diện AI
├── violations/                 # Thư mục lưu ảnh bằng chứng vi phạm an toàn
└── 2_Chay_Web_HTML.bat         # File batch khởi động nhanh hệ thống trên Windows
```

---

## 🚀 Hướng Dẫn Cài Đặt & Chạy Hệ Thống

### 1. Yêu Cầu Hệ Thống
* Hệ điều hành: Windows 10/11 (64-bit).
* Python 3.10 hoặc 3.11.
* Microsoft SQL Server (khuyến nghị SQL Server Express) & SQL Server Management Studio (SSMS).
* ODBC Driver 17 hoặc 18 for SQL Server.
* Webcam hoặc Camera USB kết nối máy tính.

### 2. Cài Đặt Cơ Sở Dữ Liệu
1. Mở SQL Server Management Studio (SSMS), kết nối tới máy chủ SQL Server của bạn (ví dụ: `.\SQLEXPRESS01`).
2. Mở file `DATADA.sql` và nhấn **Execute (F5)** để tạo CSDL `Smart_HSE` cùng cấu trúc 6 bảng dữ liệu.

### 3. Cài Đặt Thư Viện Python
Mở terminal (PowerShell hoặc Command Prompt) tại thư mục dự án và chạy lệnh:
```bash
pip install -r requirements.txt
```

### 4. Khởi Động Ứng Dụng
* **Cách 1**: Nhấp đúp vào file `2_Chay_Web_HTML.bat`.
* **Cách 2**: Chạy lệnh trực tiếp qua terminal:
```bash
python app_core.py
```
* Ứng dụng sẽ tự động giải phóng cổng nếu bị xung đột và mở trình duyệt tại địa chỉ: `http://localhost:8501`.

---

## 🔐 Tài Khoản Đăng Nhập Mặc Định

| Phân Quyền | Tên Đăng Nhập | Mật Khẩu | Mô Tả |
| :--- | :--- | :--- | :--- |
| **Chủ Server (Admin)** | `admin` | `admin123` | Quản trị toàn hệ thống, tạo xưởng mới, xem báo cáo toàn mạng |
| **Chủ Xưởng Mẫu (Owner)** | `khiem_owner` | `123456` | Quản lý nhà xưởng mẫu `OWN01`, camera AI, công nhân, điểm danh |

---

## 🛠️ Công Nghệ Sử Dụng
* **Ngôn ngữ**: Python 3.11
* **Backend**: FastAPI, Uvicorn, RESTful API, WebSockets / Video MJPEG Stream
* **Frontend**: HTML5, CSS3 Glassmorphism, JavaScript ES6 thuần (không phụ thuộc framework nặng)
* **Cơ sở dữ liệu**: Microsoft SQL Server qua `pyodbc`
* **Xử lý ảnh & AI**: OpenCV DNN, Ultralytics YOLOv8, PyTorch, Pillow
* **Báo cáo**: Pandas, OpenPyXL (Excel định dạng nhiều sheet)
