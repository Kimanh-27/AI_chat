from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# 1. Khởi tạo ứng dụng FastAPI với thông tin cơ bản
app = FastAPI(
    title="Nova TOEIC API System",
    description="Hệ thống API quản lý và lõi AI cho Nova",
    version="1.0.0"
)

# 2. Cấu hình CORS (Cực kỳ quan trọng)
# Cho phép Frontend từ localhost (XAMPP) gọi API mà không bị lỗi Cross-Origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Chấp nhận mọi nguồn gốc (Dùng cho môi trường Dev)
    allow_credentials=True,
    allow_methods=["*"],  # Chấp nhận mọi phương thức GET, POST, PUT, DELETE...
    allow_headers=["*"],  # Chấp nhận mọi Headers
)

# 3. Import các Router từ thư mục api_routes
# Đảm bảo các file trong api_routes đều đã khai báo biến: router = APIRouter()
try:
    from api_routes.router_admin import router as admin_router
    from api_routes.router_ai import router as ai_router
    from api_routes.router_student import router as student_router
    from api_routes.router_teacher import router as teacher_router

    # 4. Nhúng (Include) các Router vào App chính kèm theo Tiền tố (Prefix)
    app.include_router(admin_router, prefix="/api/admin", tags=["Quản trị viên (Admin)"])
    app.include_router(teacher_router, prefix="/api/teacher", tags=["Giáo viên (Teacher)"])
    app.include_router(student_router, prefix="/api/student", tags=["Học sinh (Student)"])
    app.include_router(ai_router, prefix="/api/ai", tags=["Lõi AI (AI Core)"])
    print("✅ Đã load thành công tất cả các router!")
    
except ImportError as e:
    print(f"⚠️ Cảnh báo: Lỗi import router - {e}. Hãy kiểm tra lại tên file và code bên trong api_routes.")

# 5. Endpoint mặc định (Kiểm tra sức khỏe Server)
@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "success",
        "message": "Nova API Server đang chạy mượt mà tại cổng 8000!"
    }

# Lệnh hỗ trợ chạy trực tiếp file này (Tùy chọn)
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)