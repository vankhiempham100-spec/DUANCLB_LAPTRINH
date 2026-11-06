USE master;
GO

IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = 'Smart_HSE')
BEGIN
    CREATE DATABASE Smart_HSE;
END
GO

USE Smart_HSE;
GO

-- ==============================================================================
-- 1. TẦNG 1: TÀI KHOẢN CHỦ SERVER (SUPER ADMIN - LÀ BẠN)
-- ==============================================================================
IF OBJECT_ID('SERVER_ADMIN', 'U') IS NULL
BEGIN
    CREATE TABLE SERVER_ADMIN (
        AdminID INT IDENTITY(1,1) PRIMARY KEY,
        Username VARCHAR(50) UNIQUE NOT NULL,
        PasswordHash VARCHAR(255) NOT NULL,
        HoTen NVARCHAR(100) NOT NULL,
        SoDT VARCHAR(15),
        NgayTao DATETIME DEFAULT GETDATE()
    );
END
GO

-- ==============================================================================
-- 2. TẦNG 2: CHỦ DOANH NGHIỆP / NHÀ XƯỞNG THUÊ DỊCH VỤ (OWNER / TENANT)
-- ==============================================================================
IF OBJECT_ID('OWNER_DOANHNGHIEP', 'U') IS NULL
BEGIN
    CREATE TABLE OWNER_DOANHNGHIEP (
        MaOwner VARCHAR(20) PRIMARY KEY,
        TenDoanhNghiep NVARCHAR(150) NOT NULL,
        NguoiDaiDien NVARCHAR(100) NOT NULL,
        SoDT VARCHAR(15),
        Email VARCHAR(100),
        Username VARCHAR(50) UNIQUE NOT NULL,
        PasswordHash VARCHAR(255) NOT NULL,
        GoiDichVu NVARCHAR(50) DEFAULT N'Tiêu chuẩn',
        TrangThai NVARCHAR(30) DEFAULT N'Hoạt động'
            CONSTRAINT CK_Owner_TrangThai CHECK (TrangThai IN (N'Hoạt động', N'Tạm khóa', N'Hết hạn')),
        NgayKichHoat DATETIME DEFAULT GETDATE(),
        NgayHetHan DATETIME NULL
    );
END
GO

-- ==============================================================================
-- 3. CẤU HÌNH CA LÀM VIỆC THEO TỪNG OWNER (TỰ ĐIỀU CHỈNH GIỜ LÀM & ÂN HẠN TRỄ)
-- ==============================================================================
IF OBJECT_ID('CA_LAM_VIEC', 'U') IS NULL
BEGIN
    CREATE TABLE CA_LAM_VIEC (
        ID INT IDENTITY(1,1) PRIMARY KEY,
        MaOwner VARCHAR(20) NOT NULL,
        TenCa NVARCHAR(50) DEFAULT N'Ca Sáng Hành Chính',
        GioBatDau TIME NOT NULL DEFAULT '07:30:00',
        PhutAnHan INT NOT NULL DEFAULT 15,
        GioKetThuc TIME NOT NULL DEFAULT '17:00:00',
        CONSTRAINT FK_CaLam_Owner FOREIGN KEY (MaOwner) REFERENCES OWNER_DOANHNGHIEP(MaOwner) ON DELETE CASCADE
    );
END
GO

-- ==============================================================================
-- 4. TẦNG 3: BẢNG CÔNG NHÂN (THUỘC QUẢN LÝ CỦA TỪNG OWNER)
-- ==============================================================================
IF OBJECT_ID('CONGNHAN', 'U') IS NULL
BEGIN
    CREATE TABLE CONGNHAN (
        MaCN VARCHAR(20) NOT NULL,
        MaOwner VARCHAR(20) NOT NULL,
        HoTen NVARCHAR(100) NOT NULL,
        PhongBan NVARCHAR(100) NULL,
        SoDT VARCHAR(15),
        AnhChanDung VARCHAR(255) NULL,
        NgayTao DATETIME DEFAULT GETDATE(),
        PRIMARY KEY (MaCN, MaOwner),
        CONSTRAINT FK_CN_Owner FOREIGN KEY (MaOwner) REFERENCES OWNER_DOANHNGHIEP(MaOwner) ON DELETE CASCADE
    );
END
GO

-- ==============================================================================
-- 5. BẢNG ĐIỂM DANH (THEO TỪNG OWNER VÀ CÔNG NHÂN)
-- ==============================================================================
IF OBJECT_ID('DIEMDANH', 'U') IS NULL
BEGIN
    CREATE TABLE DIEMDANH (
        ID INT IDENTITY(1,1) PRIMARY KEY,
        MaOwner VARCHAR(20) NOT NULL,
        MaCN VARCHAR(20) NULL,
        NgayĐD DATE NOT NULL,
        GioĐD TIME NOT NULL,
        TrangThai NVARCHAR(50) NOT NULL DEFAULT N'Hợp lệ',
        PhuongThuc NVARCHAR(50) DEFAULT N'Khuôn mặt AI',
        GhiChu NVARCHAR(250) NULL,
        CONSTRAINT FK_DD_Owner FOREIGN KEY (MaOwner) REFERENCES OWNER_DOANHNGHIEP(MaOwner)
    );
END
GO

-- ==============================================================================
-- 6. BẢNG VI PHẠM AN TOÀN
-- ==============================================================================
IF OBJECT_ID('VIPHAMANTOAN', 'U') IS NULL
BEGIN
    CREATE TABLE VIPHAMANTOAN (
        ID INT IDENTITY(1,1) PRIMARY KEY,
        MaOwner VARCHAR(20) NOT NULL,
        MaCN VARCHAR(20) NULL,
        NgayViPham DATE NOT NULL,
        GioViPham TIME NOT NULL,
        LoiViPham NVARCHAR(250) NOT NULL,
        MinhChung VARCHAR(255),
        MucDoCanhBao NVARCHAR(50) DEFAULT N'Cảnh báo'
            CONSTRAINT CK_ViPham_MucDo CHECK (MucDoCanhBao IN (N'Nhắc nhở', N'Cảnh báo', N'Nghiêm trọng')),
        TrangThaiXuLy NVARCHAR(50) DEFAULT N'Cảnh báo'
            CONSTRAINT CK_XuLy_MucDo CHECK (TrangThaiXuLy IN (N'Nhắc nhở', N'Cảnh báo', N'Kỷ luật')),
        CONSTRAINT FK_VPAT_Owner FOREIGN KEY (MaOwner) REFERENCES OWNER_DOANHNGHIEP(MaOwner)
    );
END
GO

-- ==============================================================================
-- 7. KHỞI TẠO DỮ LIỆU BAN ĐẦU
-- ==============================================================================
IF NOT EXISTS (SELECT 1 FROM SERVER_ADMIN WHERE Username = 'admin')
BEGIN
    INSERT INTO SERVER_ADMIN (Username, PasswordHash, HoTen, SoDT)
    VALUES ('admin', 'admin123', N'Chủ Quản Trị Server', '0901234567');
END

IF NOT EXISTS (SELECT 1 FROM OWNER_DOANHNGHIEP WHERE MaOwner = 'OWN01')
BEGIN
    INSERT INTO OWNER_DOANHNGHIEP (MaOwner, TenDoanhNghiep, NguoiDaiDien, SoDT, Username, PasswordHash, TrangThai)
    VALUES ('OWN01', N'Nhà Xưởng Kiểu Mẫu HSE', N'Phạm Văn Khiêm', '0987654321', 'khiem_owner', '123456', N'Hoạt động');

    INSERT INTO CA_LAM_VIEC (MaOwner, TenCa, GioBatDau, PhutAnHan, GioKetThuc)
    VALUES ('OWN01', N'Ca Sáng Hành Chính', '07:30:00', 15, '17:00:00');
END
GO
