from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os

from api_routes import router_admin, router_teacher, router_student, router_ai

app = FastAPI(title="Nova Backend API", version="2.0")

os.makedirs("uploads/pdfs", exist_ok=True)
os.makedirs("uploads/audios", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================================================
# ĐỊNH TUYẾN THÔNG MINH (ROUTING)
# ========================================================
app.include_router(router_admin.router, prefix="/api/admin", tags=["Admin"])

app.include_router(router_teacher.router, prefix="/api/teacher", tags=["Teacher"])

app.include_router(router_student.router, prefix="/api/student", tags=["Student"])

app.include_router(router_ai.router, prefix="/api", tags=["AI Engine & Core"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)