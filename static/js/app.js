// ==============================================================================
// SMART HSE VISION - SINGLE PAGE APPLICATION CONTROLLER
// ==============================================================================

let isCameraRunning = false;
let camStatusInterval = null;
let chartPieInstance = null;
let chartBarInstance = null;
let allAttendanceData = [];
let allViolationsData = [];

document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initEventListeners();
    checkDbStatus();
    loadWorkers();
    loadShiftSettings();
});

function initEventListeners() {
    // 1. Camera Toggle
    const btnToggleCam = document.getElementById("btnToggleCam");
    if (btnToggleCam) btnToggleCam.addEventListener("click", toggleCamera);

    // 2. Shift Settings inputs
    const cfgShiftStart = document.getElementById("cfgShiftStart");
    const cfgGraceMins = document.getElementById("cfgGraceMins");
    const cfgShiftEnd = document.getElementById("cfgShiftEnd");
    if (cfgShiftStart) cfgShiftStart.addEventListener("input", updateShiftPreview);
    if (cfgGraceMins) cfgGraceMins.addEventListener("input", updateShiftPreview);
    if (cfgShiftEnd) cfgShiftEnd.addEventListener("input", updateShiftPreview);

    const btnSaveShift = document.getElementById("btnSaveShift");
    if (btnSaveShift) btnSaveShift.addEventListener("click", saveShiftSettings);

    // 3. Attendance filters
    const attSearch = document.getElementById("attSearchInput");
    const attDate = document.getElementById("attDateSelect");
    if (attSearch) attSearch.addEventListener("input", filterAttendanceTable);
    if (attDate) attDate.addEventListener("change", filterAttendanceTable);

    // 4. Violations filters
    const vioSearch = document.getElementById("vioSearchInput");
    const vioFilter = document.getElementById("vioFilterSelect");
    if (vioSearch) vioSearch.addEventListener("input", filterViolationsGallery);
    if (vioFilter) vioFilter.addEventListener("change", filterViolationsGallery);

    // 5. Worker Management Modal
    const btnOpenModal = document.getElementById("btnOpenWorkerModal");
    if (btnOpenModal) btnOpenModal.addEventListener("click", openWorkerModal);

    const btnCloseModal = document.getElementById("btnCloseWorkerModal");
    if (btnCloseModal) btnCloseModal.addEventListener("click", closeWorkerModal);

    const btnCancelModal = document.getElementById("btnCancelWorkerModal");
    if (btnCancelModal) btnCancelModal.addEventListener("click", closeWorkerModal);

    const modalOverlay = document.getElementById("modalAddWorker");
    if (modalOverlay) {
        modalOverlay.addEventListener("click", (e) => {
            if (e.target === modalOverlay) closeWorkerModal();
        });
    }

    const formAddWorker = document.getElementById("formAddWorker");
    if (formAddWorker) formAddWorker.addEventListener("submit", handleWorkerSubmit);

    const inpPhoto = document.getElementById("inpWorkerPhoto");
    if (inpPhoto) inpPhoto.addEventListener("change", previewUploadPhoto);
}

// ------------------------------------------------------------------------------
// 1. CHUYỂN TAB ĐIỀU HƯỚNG
// ------------------------------------------------------------------------------
function initNavigation() {
    const navItems = document.querySelectorAll(".nav-item");
    navItems.forEach(item => {
        item.addEventListener("click", () => {
            const targetTabId = item.getAttribute("data-tab");
            
            // Cập nhật class active trên menu
            navItems.forEach(n => n.classList.remove("active"));
            item.classList.add("active");

            // Hiển thị nội dung tab tương ứng
            document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));
            const targetPane = document.getElementById(targetTabId);
            if (targetPane) targetPane.classList.add("active");

            // Tự động ngắt camera nếu rời khỏi tab camera
            if (targetTabId !== "tab-camera" && isCameraRunning) {
                stopCamera();
            }

            // Tải dữ liệu tương ứng của tab đó
            if (targetTabId === "tab-attendance") loadAttendance();
            if (targetTabId === "tab-violations") loadViolations();
            if (targetTabId === "tab-analytics") loadAnalytics();
            if (targetTabId === "tab-workers") loadWorkers();
        });
    });
}

// ------------------------------------------------------------------------------
// 2. KIỂM TRA TRẠNG THÁI CSDL SQL SERVER
// ------------------------------------------------------------------------------
async function checkDbStatus() {
    try {
        const res = await fetch("/api/db/status");
        const data = await res.json();
        const pill = document.getElementById("dbStatusPill");
        if (data.connected) {
            pill.innerHTML = "SQL Server: Đã kết nối";
            pill.style.color = "#00FFA3";
        } else {
            pill.innerHTML = "SQL Server: Chưa kết nối";
            pill.style.color = "#FFB300";
        }
    } catch (e) {
        console.error("DB Status error:", e);
    }
}

// ------------------------------------------------------------------------------
// 3. ĐIỀU KHIỂN CAMERA AI & STREAMING
// ------------------------------------------------------------------------------
async function toggleCamera() {
    if (!isCameraRunning) {
        await startCamera();
    } else {
        await stopCamera();
    }
}

async function startCamera() {
    const btn = document.getElementById("btnToggleCam");
    const text = document.getElementById("btnCamText");
    const streamImg = document.getElementById("videoStreamImg");
    const offlineBox = document.getElementById("camOfflineBox");

    if (text) text.innerText = "Đang kết nối Camera...";
    try {
        const res = await fetch("/api/camera/start", { method: "POST" });
        const data = await res.json();
        if (data.running) {
            isCameraRunning = true;
            if (btn) btn.className = "btn-cam-stop";
            if (text) text.innerText = "TẮT CAMERA";

            if (offlineBox) offlineBox.style.display = "none";
            if (streamImg) {
                streamImg.style.display = "block";
                streamImg.src = "/video_feed?t=" + new Date().getTime();
            }

            startCameraStatusPolling();
        } else {
            alert("Không thể mở Webcam! Vui lòng kiểm tra cổng cắm hoặc quyền truy cập camera.");
            if (text) text.innerText = "BẬT CAMERA";
        }
    } catch (e) {
        alert("Lỗi kết nối server: " + e);
        if (text) text.innerText = "BẬT CAMERA";
    }
}

async function stopCamera() {
    const btn = document.getElementById("btnToggleCam");
    const text = document.getElementById("btnCamText");
    const streamImg = document.getElementById("videoStreamImg");
    const offlineBox = document.getElementById("camOfflineBox");

    try {
        await fetch("/api/camera/stop", { method: "POST" });
    } catch (e) {}

    isCameraRunning = false;
    if (btn) btn.className = "btn-cam-start";
    if (text) text.innerText = "BẬT CAMERA";

    if (streamImg) {
        streamImg.src = "";
        streamImg.style.display = "none";
    }
    if (offlineBox) offlineBox.style.display = "block";

    document.getElementById("valPersonCount").innerText = "0";
    document.getElementById("valSafeCount").innerText = "0";
    document.getElementById("valVioCount").innerText = "0";

    stopCameraStatusPolling();
}

function startCameraStatusPolling() {
    stopCameraStatusPolling();
    camStatusInterval = setInterval(async () => {
        if (!isCameraRunning) return;
        try {
            const res = await fetch("/api/camera/status");
            const data = await res.json();
            
            document.getElementById("valPersonCount").innerText = data.person_count || 0;
            document.getElementById("valSafeCount").innerText = data.safe_count || 0;
            document.getElementById("valVioCount").innerText = data.violation_count || 0;
            document.getElementById("todayCheckinBadge").innerText = `Đã Check-in: ${data.today_checkin_count || 0} người`;

            // Cập nhật danh sách sự kiện
            const streamList = document.getElementById("eventStreamList");
            if (data.recent_events && data.recent_events.length > 0) {
                streamList.innerHTML = data.recent_events.map(ev => {
                    if (ev.type === "checkin") {
                        return `<div class="event-item checkin">
                            <span style="color: #00FFA3; font-weight: 700;">Check-in:</span> ${ev.name} <span style="color: #81E6D9; font-size: 11px;">[${ev.time}]</span>
                        </div>`;
                    } else {
                        return `<div class="event-item violation">
                            <span style="color: #FF4D4D; font-weight: 700;">Vi phạm:</span> ${ev.name}<br>
                            <span style="font-size: 11.5px; color: #FFA8A8;">${ev.text}</span>
                        </div>`;
                    }
                }).join("");
            }
        } catch (e) {}
    }, 600);
}

function stopCameraStatusPolling() {
    if (camStatusInterval) {
        clearInterval(camStatusInterval);
        camStatusInterval = null;
    }
}

// ------------------------------------------------------------------------------
// 4. BẢNG NHẬT KÝ ĐIỂM DANH & CÀI ĐẶT CA LÀM
// ------------------------------------------------------------------------------
function updateShiftUIBadges(startStr, graceMins, endStr) {
    if (!startStr) startStr = "07:30";
    if (!endStr) endStr = "17:00";
    graceMins = parseInt(graceMins) || 0;
    
    const startBadge = document.getElementById("ruleStartBadge");
    const graceBadge = document.getElementById("ruleGraceBadge");
    const lateBadge = document.getElementById("ruleLateBadge");
    const endBadge = document.getElementById("ruleEndBadge");
    
    if (startBadge) startBadge.innerText = startStr;
    if (graceBadge) graceBadge.innerText = `+${graceMins} phút`;
    if (endBadge) endBadge.innerText = endStr;
    
    if (lateBadge) {
        try {
            const parts = startStr.split(":");
            const h = parseInt(parts[0]);
            const m = parseInt(parts[1]);
            const totalMins = h * 60 + m + graceMins + 1;
            const newH = Math.floor(totalMins / 60) % 24;
            const newM = totalMins % 60;
            const limitStr = `${String(newH).padStart(2, '0')}:${String(newM).padStart(2, '0')}`;
            lateBadge.innerText = limitStr;
        } catch (e) {
            lateBadge.innerText = startStr;
        }
    }
}

function updateShiftPreview() {
    const startVal = document.getElementById("cfgShiftStart")?.value || "07:30";
    const graceVal = parseInt(document.getElementById("cfgGraceMins")?.value) || 0;
    const endVal = document.getElementById("cfgShiftEnd")?.value || "17:00";
    
    updateShiftUIBadges(startVal, graceVal, endVal);
}

async function loadShiftSettings() {
    try {
        const res = await fetch("/api/shift/config");
        const cfg = await res.json();
        
        const startInput = document.getElementById("cfgShiftStart");
        const graceInput = document.getElementById("cfgGraceMins");
        const endInput = document.getElementById("cfgShiftEnd");
        
        if (startInput && cfg.shift_start) startInput.value = cfg.shift_start;
        if (graceInput && cfg.grace_period_mins !== undefined) graceInput.value = cfg.grace_period_mins;
        if (endInput && cfg.shift_end) endInput.value = cfg.shift_end;
        
        updateShiftUIBadges(cfg.shift_start, cfg.grace_period_mins, cfg.shift_end);
    } catch (e) {
        console.error("Lỗi tải cài đặt ca:", e);
    }
}

async function saveShiftSettings() {
    const startVal = document.getElementById("cfgShiftStart")?.value || "07:30";
    const graceVal = parseInt(document.getElementById("cfgGraceMins")?.value) || 0;
    const endVal = document.getElementById("cfgShiftEnd")?.value || "17:00";
    const notify = document.getElementById("shiftStatusNotify");

    try {
        const res = await fetch("/api/shift/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                shift_start: startVal,
                grace_period_mins: graceVal,
                shift_end: endVal,
                shift_name: "Ca Làm Việc Tiêu Chuẩn"
            })
        });
        const json = await res.json();
        if (json.success) {
            updateShiftUIBadges(startVal, graceVal, endVal);
            if (notify) {
                notify.style.display = "inline-block";
                notify.innerHTML = `Đã lưu: Bắt đầu ${startVal} | Ân hạn +${graceVal}p!`;
                setTimeout(() => { notify.style.display = "none"; }, 4000);
            }
        }
    } catch (e) {
        alert("Lỗi lưu cài đặt: " + e);
    }
}

async function loadAttendance() {
    const tbody = document.getElementById("attendanceTableBody");
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 20px;">Đang tải dữ liệu điểm danh...</td></tr>`;
    loadShiftSettings();
    try {
        const res = await fetch("/api/attendance");
        const json = await res.json();
        allAttendanceData = json.data || [];

        // Cập nhật danh sách ngày lọc
        const dateSelect = document.getElementById("attDateSelect");
        const uniqueDates = [...new Set(allAttendanceData.map(r => r["Ngày"]).filter(Boolean))];
        dateSelect.innerHTML = `<option value="">Tất cả các ngày</option>` + 
            uniqueDates.map(d => `<option value="${d}">${d}</option>`).join("");

        renderAttendanceTable(allAttendanceData);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: #FF4D4D;">Lỗi tải dữ liệu: ${e}</td></tr>`;
    }
}

function renderAttendanceTable(data) {
    const tbody = document.getElementById("attendanceTableBody");
    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 20px;">Chưa có bản ghi điểm danh nào.</td></tr>`;
        return;
    }
    tbody.innerHTML = data.map(r => {
        const st = (r["Trạng Thái"] || "Hợp lệ").toString();
        let statusBadge = `<span style="color: #00FFA3; font-weight: 700; background: rgba(0, 255, 163, 0.12); padding: 3px 10px; border-radius: 6px; border: 1px solid rgba(0, 255, 163, 0.3);">${st}</span>`;
        
        if (st.toLowerCase().includes("trễ") || st.toLowerCase().includes("tre")) {
            statusBadge = `<span style="color: #FFB300; font-weight: 700; background: rgba(255, 179, 0, 0.12); padding: 3px 10px; border-radius: 6px; border: 1px solid rgba(255, 179, 0, 0.35);">${st}</span>`;
        } else if (st.toLowerCase().includes("không") || st.toLowerCase().includes("lỗi")) {
            statusBadge = `<span style="color: #FF4D4D; font-weight: 700; background: rgba(255, 77, 77, 0.12); padding: 3px 10px; border-radius: 6px; border: 1px solid rgba(255, 77, 77, 0.3);">${st}</span>`;
        }

        return `
            <tr>
                <td>${r["STT"] || ""}</td>
                <td>${r["Ngày"] || ""}</td>
                <td><b>${r["Giờ Điểm Danh"] || ""}</b></td>
                <td><span class="worker-id-tag">${r["Mã Công Nhân"] || "N/A"}</span></td>
                <td><b style="color: #FFFFFF;">${r["Họ và Tên Công Nhân"] || ""}</b></td>
                <td>${statusBadge}</td>
                <td><span style="color: var(--text-muted); font-size: 12.5px;">${r["Phương Thức Nhận Diện"] || "Face Recognition AI"}</span></td>
            </tr>
        `;
    }).join("");
}

function filterAttendanceTable() {
    const term = document.getElementById("attSearchInput").value.toLowerCase();
    const date = document.getElementById("attDateSelect").value;
    const filtered = allAttendanceData.filter(r => {
        const nameMatch = (r["Họ và Tên Công Nhân"] || "").toLowerCase().includes(term) ||
                          (r["Mã Công Nhân"] || "").toLowerCase().includes(term);
        const dateMatch = !date || r["Ngày"] === date;
        return nameMatch && dateMatch;
    });
    renderAttendanceTable(filtered);
}

// ------------------------------------------------------------------------------
// 5. THƯ VIỆN ẢNH BẰNG CHỨNG VI PHẠM (COMPACT & BỘ LỌC THÔNG MINH)
// ------------------------------------------------------------------------------
async function loadViolations() {
    const grid = document.getElementById("violationsGalleryGrid");
    const badge = document.getElementById("vioCountBadge");
    grid.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">Đang tải bằng chứng vi phạm...</p>`;
    try {
        const res = await fetch("/api/violations");
        const json = await res.json();
        allViolationsData = json.data || [];

        if (allViolationsData.length === 0) {
            grid.innerHTML = `<p style="color: #00FFA3; font-weight: 700; font-size: 15px; padding: 20px;">Chưa có trường hợp vi phạm nào được ghi nhận.</p>`;
            if (badge) badge.innerText = "0 Bằng chứng";
            return;
        }

        filterViolationsGallery();
    } catch (e) {
        grid.innerHTML = `<p style="color: #FF4D4D; padding: 20px;">Lỗi tải bằng chứng: ${e}</p>`;
    }
}

function filterViolationsGallery() {
    const term = (document.getElementById("vioSearchInput")?.value || "").toLowerCase().trim();
    const filterMode = document.getElementById("vioFilterSelect")?.value || "with_photo";
    const badge = document.getElementById("vioCountBadge");

    let filtered = allViolationsData.filter(r => {
        const name = (r["Họ và Tên Công Nhân"] || "").toLowerCase();
        const id = (r["Mã Công Nhân"] || "").toLowerCase();
        const reason = (r["Lỗi Vi Phạm Cụ Thể"] || "").toLowerCase();
        const matchesTerm = !term || name.includes(term) || id.includes(term) || reason.includes(term);

        if (!matchesTerm) return false;
        if (filterMode === "photo_only") return r.has_image === true;
        return true;
    });

    // Sắp xếp: Nếu chế độ "with_photo" thì ưu tiên đưa các bản ghi có ảnh chụp lên trước
    if (filterMode === "with_photo") {
        filtered.sort((a, b) => {
            if (a.has_image === b.has_image) return 0;
            return a.has_image ? -1 : 1;
        });
    }

    if (badge) {
        const photoCount = filtered.filter(f => f.has_image).length;
        badge.innerText = `Hiển thị ${filtered.length} bản ghi (${photoCount} có ảnh)`;
    }

    renderViolationsGallery(filtered);
}

function renderViolationsGallery(data) {
    const grid = document.getElementById("violationsGalleryGrid");
    if (data.length === 0) {
        grid.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">Không tìm thấy bản ghi vi phạm phù hợp với bộ lọc.</p>`;
        return;
    }

    grid.innerHTML = data.map(r => {
        const hasPhoto = r.has_image && r.image_url;
        const imgBlock = hasPhoto ? `
            <div class="evidence-img-wrap">
                <img src="${r.image_url}" class="evidence-img" loading="lazy" 
                     onerror="this.onerror=null; this.parentElement.innerHTML='<div class=\\'evidence-no-photo\\'><span>Ảnh đã lưu trữ</span></div>';" 
                     alt="Evidence">
            </div>
        ` : `
            <div class="evidence-img-wrap">
                <div class="evidence-no-photo">
                    <span>Không có file ảnh</span>
                </div>
            </div>
        `;

        return `
            <div class="evidence-card">
                ${imgBlock}
                <div class="evidence-body">
                    <div class="evidence-worker-title" title="${r["Họ và Tên Công Nhân"]}">
                        ${r["Họ và Tên Công Nhân"]}
                    </div>
                    <div class="evidence-meta">
                        ID: <span style="color: var(--teal-glow); font-weight: 600;">${r["Mã Công Nhân"] || "UNKNOWN"}</span> • Giờ: ${r["Giờ Vi Phạm"]} (${r["Ngày"]})
                    </div>
                    <div class="evidence-violation-tag">
                        ${r["Lỗi Vi Phạm Cụ Thể"]}
                    </div>
                    <div class="evidence-status-bar">
                        <span>Cảnh báo: <b>${r["Mức Độ Cảnh Báo"]}</b></span>
                        <span style="color: #00FFA3;">${r["Trạng Thái Xử Lý"]}</span>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

// ------------------------------------------------------------------------------
// 6. THỐNG KÊ & BIỂU ĐỒ ANALYTICS (CHART.JS THIẾT KẾ TINH TẾ)
// ------------------------------------------------------------------------------
async function loadAnalytics() {
    try {
        const res = await fetch("/api/analytics");
        const data = await res.json();

        document.getElementById("statTotalAtt").innerText = data.total_att;
        document.getElementById("statSafeEst").innerText = data.safe_est;
        document.getElementById("statTotalVio").innerText = data.total_vio;
        document.getElementById("statRate").innerText = data.compliance_rate + "%";

        // 1. Biểu đồ tròn Donut Chart
        const ctxPie = document.getElementById("chartPie").getContext("2d");
        if (chartPieInstance) chartPieInstance.destroy();
        chartPieInstance = new Chart(ctxPie, {
            type: "doughnut",
            data: {
                labels: ["An toàn đạt chuẩn", "Vi phạm trang bị"],
                datasets: [{
                    data: [data.pie_data.safe, data.pie_data.violation],
                    backgroundColor: ["#00FFA3", "#FF4D4D"],
                    borderColor: "#041615",
                    borderWidth: 3,
                    hoverOffset: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "68%",
                plugins: {
                    legend: { 
                        position: "bottom",
                        labels: { color: "#F1FDFB", font: { size: 12.5, family: "Inter" }, padding: 16 } 
                    }
                }
            }
        });

        // 2. Biểu đồ cột Bar Chart tinh tế, thanh mảnh
        const ctxBar = document.getElementById("chartBar").getContext("2d");
        if (chartBarInstance) chartBarInstance.destroy();
        
        const maxVal = Math.max(data.bar_data.helmet, data.bar_data.vest);
        const suggestedMax = Math.max(maxVal + Math.ceil(maxVal * 0.25) + 2, 10);

        chartBarInstance = new Chart(ctxBar, {
            type: "bar",
            data: {
                labels: ["Không đội mũ", "Không mặc áo"],
                datasets: [{
                    label: "Số ca vi phạm",
                    data: [data.bar_data.helmet, data.bar_data.vest],
                    backgroundColor: ["#FFB300", "#00F2FE"],
                    borderColor: ["#FFA000", "#00D2E0"],
                    borderWidth: 1,
                    borderRadius: 8,
                    maxBarThickness: 38,
                    categoryPercentage: 0.55,
                    barPercentage: 0.65
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: {
                    padding: { top: 12, bottom: 5 }
                },
                scales: {
                    x: { 
                        ticks: { color: "#81E6D9", font: { size: 12, weight: 600 } },
                        grid: { display: false }
                    },
                    y: { 
                        suggestedMax: suggestedMax,
                        ticks: { color: "#81E6D9", stepSize: 5, font: { size: 11 } },
                        grid: { color: "rgba(0, 255, 163, 0.08)", drawBorder: false }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#051F1C",
                        titleColor: "#00FFA3",
                        bodyColor: "#FFFFFF",
                        borderColor: "rgba(0, 255, 163, 0.3)",
                        borderWidth: 1,
                        padding: 10
                    }
                }
            }
        });
    } catch (e) {
        console.error("Analytics error:", e);
    }
}

// ------------------------------------------------------------------------------
// 7. QUẢN LÝ CÔNG NHÂN
// ------------------------------------------------------------------------------
async function loadWorkers() {
    const container = document.getElementById("workerListContainer");
    const badge = document.getElementById("workerCountBadge");
    try {
        const res = await fetch("/api/workers");
        const json = await res.json();
        const workers = json.workers || [];

        badge.innerText = `${workers.length} Hồ sơ`;
        document.getElementById("inpWorkerId").value = `CN-${String(workers.length + 1).padStart(3, "0")}`;

        if (workers.length === 0) {
            container.innerHTML = `<p style="color: var(--text-dim); padding: 15px;">Chưa có hồ sơ công nhân nào trong thư mục.</p>`;
            return;
        }

        container.innerHTML = workers.map(w => `
            <div class="worker-item">
                <img src="${w.image_url}" class="worker-avatar" onerror="this.src='/static/img/default_avatar.png'" alt="Avatar">
                <div class="worker-info">
                    <span class="worker-id-tag">Mã: ${w.id}</span>
                    <div class="worker-name-title">${w.name}</div>
                </div>
                <button class="btn-del-worker" onclick="deleteWorker('${w.filename}', '${w.name}')">Xóa</button>
            </div>
        `).join("");
    } catch (e) {
        container.innerHTML = `<p style="color: #FF4D4D;">Lỗi tải danh sách: ${e}</p>`;
    }
}

function previewUploadPhoto(event) {
    const file = event.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const preview = document.getElementById("photoPreviewImg");
            preview.src = e.target.result;
            document.getElementById("photoPreviewContainer").style.display = "block";
        };
        reader.readAsDataURL(file);
    }
}

function openWorkerModal() {
    const modal = document.getElementById("modalAddWorker");
    if (modal) {
        modal.classList.add("show");
        document.getElementById("formAddWorker").reset();
        document.getElementById("photoPreviewContainer").style.display = "none";
    }
}

function closeWorkerModal() {
    const modal = document.getElementById("modalAddWorker");
    if (modal) {
        modal.classList.remove("show");
    }
}

function handleModalOutsideClick(event) {
    if (event.target && event.target.id === "modalAddWorker") {
        closeWorkerModal();
    }
}

async function handleWorkerSubmit(event) {
    event.preventDefault();
    const workerId = document.getElementById("inpWorkerId").value;
    const workerName = document.getElementById("inpWorkerName").value;
    const fileInput = document.getElementById("inpWorkerPhoto");

    if (!fileInput.files || fileInput.files.length === 0) {
        alert("Vui lòng chọn file ảnh chân dung!");
        return;
    }

    const formData = new FormData();
    formData.append("worker_id", workerId);
    formData.append("worker_name", workerName);
    formData.append("photo", fileInput.files[0]);

    try {
        const res = await fetch("/api/workers/add", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (res.ok && data.success) {
            alert(data.message || "Đã đăng ký thành công!");
            closeWorkerModal();
            loadWorkers();
        } else {
            alert("Lỗi: " + (data.detail || "Không thể thêm công nhân"));
        }
    } catch (e) {
        alert("Lỗi kết nối: " + e);
    }
}

async function deleteWorker(filename, name) {
    if (!confirm(`Bạn có chắc muốn xóa hồ sơ công nhân: ${name}?`)) return;
    try {
        const res = await fetch(`/api/workers/${filename}`, { method: "DELETE" });
        const data = await res.json();
        if (data.success) {
            loadWorkers();
        } else {
            alert("Không thể xóa: " + data.detail);
        }
    } catch (e) {
        alert("Lỗi khi xóa: " + e);
    }
}

// Global window exposure for dynamic templates & debugging
window.toggleCamera = toggleCamera;
window.startCamera = startCamera;
window.stopCamera = stopCamera;
window.loadAttendance = loadAttendance;
window.filterAttendanceTable = filterAttendanceTable;
window.loadViolations = loadViolations;
window.filterViolationsGallery = filterViolationsGallery;
window.loadAnalytics = loadAnalytics;
window.loadWorkers = loadWorkers;
window.openWorkerModal = openWorkerModal;
window.closeWorkerModal = closeWorkerModal;
window.handleModalOutsideClick = handleModalOutsideClick;
window.handleWorkerSubmit = handleWorkerSubmit;
window.previewUploadPhoto = previewUploadPhoto;
window.deleteWorker = deleteWorker;
window.saveShiftSettings = saveShiftSettings;
window.updateShiftPreview = updateShiftPreview;
