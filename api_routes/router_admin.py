from fastapi import APIRouter, UploadFile, File, Form, Query
from database import get_db_connection
import uuid
import shutil
import re
import random
import json
import os
from datetime import datetime
import pymupdf as fitz 
from schemas import UpdateAnswersModel, AccountModel, ClassModel, UpdateProfileModel, TaiLieuHocModel, GenerateQuizRequest
from dotenv import load_dotenv
from huggingface_hub import AsyncInferenceClient
import bcrypt  
import google.generativeai as genai
from typing import Optional

router = APIRouter()

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

load_dotenv()
HF_TOKEN = os.getenv("HUGGING_FACE_API_KEY")
hf_client = AsyncInferenceClient(api_key=HF_TOKEN)

# ==========================================
# API ADMIN: TẢI ĐỀ IIG LÊN HỆ THỐNG
# ==========================================
@router.post("/upload-iig")
async def upload_iig(
    ten_de: str = Form(...),
    nam_phat_hanh: int = Form(...),
    dap_an_json: str = Form(...),
    id_admin: int = Form(0), # Mặc định là 0 nếu UI chưa truyền
    file_pdf: UploadFile = File(...),
    file_audio: UploadFile = File(None)
):
    try:
        pdf_filename = f"{uuid.uuid4()}_{file_pdf.filename.replace(' ', '_')}"
        pdf_path = f"uploads/pdfs/{pdf_filename}"
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file_pdf.file, buffer)
            
        audio_path = None
        if file_audio and file_audio.filename:
            audio_filename = f"{uuid.uuid4()}_{file_audio.filename.replace(' ', '_')}"
            audio_path = f"uploads/audios/{audio_filename}"
            with open(audio_path, "wb") as buffer:
                shutil.copyfileobj(file_audio.file, buffer)
                
        db = get_db_connection()
        cursor = db.cursor()
        sql = """INSERT INTO de_thi_iig (ten_de, nam_phat_hanh, file_pdf, file_audio, dap_an_json) 
                 VALUES (%s, %s, %s, %s, %s)"""
        cursor.execute(sql, (ten_de, nam_phat_hanh, f"{pdf_path}", 
                     f"{audio_path}" if audio_path else None, dap_an_json))
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã tải lên đề thi IIG: '{ten_de}' (Năm {nam_phat_hanh})"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_DE_THI', %s)", (id_admin, chi_tiet_log))

        db.commit()
        cursor.close()
        db.close()
        
        return {"status": "success", "message": "Đã tải đề lên Kho Trung Tâm thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# API ADMIN: LẤY DANH SÁCH ĐỀ IIG
# ==========================================
@router.get("/get-iig-tests")
async def get_iig_tests():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # Chỉ lấy những đề có nguồn gốc là iig_standard
        cursor.execute("""
            SELECT id, ten_de, nam_phat_hanh, ngay_tao, file_pdf, file_audio 
            FROM de_thi_iig 
            WHERE nguon_goc = 'iig_standard' OR nguon_goc IS NULL 
            ORDER BY ngay_tao DESC
        """)
        tests = cursor.fetchall()
        db.close()
        return {"status": "success", "data": tests}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# API ADMIN: LẤY DANH SÁCH ĐỀ DO AI SINH RA
# ==========================================
@router.get("/get-ai-generated-tests")
async def get_ai_generated_tests():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                d.id, 
                d.ten_de, 
                d.nam_phat_hanh, 
                d.nguon_goc,
                d.file_pdf,
                d.file_audio,
                COALESCE(t.ten_dang_nhap, 'Không rõ') AS nguoi_tao,
                COALESCE(n.thoi_gian, d.ngay_tao) AS ngay_tao
            FROM de_thi_iig d
            LEFT JOIN nhat_ky_hoat_dong n 
                ON n.loai_hanh_dong = 'TAO_DE_AI' 
                AND n.chi_tiet LIKE CONCAT('%(ID: #', d.id, ')%')
            LEFT JOIN tai_khoan t 
                ON n.id_tai_khoan = t.id
            WHERE d.nguon_goc = 'ai_generated' 
            ORDER BY d.id DESC
        """)
        tests = cursor.fetchall()
        
        for test in tests:
            if test['ngay_tao']:
                test['ngay_tao'] = test['ngay_tao'].strftime('%d/%m/%Y %H:%M')
                
        db.close()
        return {"status": "success", "data": tests}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
# =============================================================
# ===   API XEM CHI TIẾT ĐỀ THI AI                          ===
# =============================================================
@router.get("/get-ai-test-detail/{test_id}")
async def get_ai_test_detail_api(test_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, ten_de, dap_an_json, file_pdf, file_audio FROM de_thi_iig WHERE id = %s", (test_id,))
        test = cursor.fetchone()
        db.close()
        
        if test:
            return {"status": "success", "data": test}
        else:
            return {"status": "error", "message": "Không tìm thấy đề thi"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# API CẬP NHẬT ĐỀ THI AI (SỬA JSON ĐỀ CŨ)
# ==========================================
@router.post("/update-ai-test/{test_id}")
async def update_ai_test_api(test_id: int, data: dict, admin_id: int = 0):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        dap_an_json = data.get("dap_an_json")
        if not dap_an_json:
            return {"status": "error", "message": "Dữ liệu JSON không hợp lệ"}
            
        # Cập nhật lại nội dung JSON
        cursor.execute("UPDATE de_thi_iig SET dap_an_json = %s WHERE id = %s", (dap_an_json, test_id))
        
        # Ghi log
        if admin_id > 0:
            chi_tiet_log = f"Đã sửa nội dung JSON đề AI (ID: #{test_id})"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'SUA_DE_AI', %s)", (admin_id, chi_tiet_log))

        db.commit() 
        db.close()
        return {"status": "success", "message": "Cập nhật thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
# =============================================================
# ===   API XÓA ĐỀ THI AI                                   ===
# =============================================================
@router.delete("/delete-ai-test/{test_id}")
async def delete_ai_test_api(test_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM de_thi_iig WHERE id = %s", (test_id,))
        db.commit()
        db.close()
        
        return {"status": "success", "message": "Đã xóa thành công"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# API ADMIN: LẤY CHI TIẾT 1 ĐỀ IIG ĐỂ XEM TRƯỚC
# ==========================================
@router.get("/get-iig-test/{test_id}")
async def get_single_iig_test(test_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM de_thi_iig WHERE id = %s", (test_id,))
        test = cursor.fetchone()
        db.close()
        
        if test:
            return {"status": "success", "data": test}
        return {"status": "error", "message": "Không tìm thấy đề thi"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# API CẬP NHẬT LẠI ĐÁP ÁN CHO ĐỀ IIG
@router.post("/update-iig-answers/{test_id}")
async def update_iig_answers(test_id: int, req: UpdateAnswersModel, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # Lấy tên đề để ghi log cho rõ ràng
        cursor.execute("SELECT ten_de FROM de_thi_iig WHERE id = %s", (test_id,))
        de_thi = cursor.fetchone()
        
        cursor.execute("UPDATE de_thi_iig SET dap_an_json = %s WHERE id = %s", (req.dap_an_json, test_id))
        
        # --- LƯU NHẬT KÝ ---
        if de_thi:
            chi_tiet_log = f"Đã cập nhật lại khóa đáp án cho đề: '{de_thi[0]}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_DE_THI', %s)", (id_admin, chi_tiet_log))

        db.commit()
        cursor.close()
        db.close()
        return {"status": "success", "message": "Đã cập nhật lại khóa đáp án thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# API XÓA ĐỀ THI IIG
# ==========================================
@router.post("/delete-iig-test/{test_id}")
async def delete_iig_test(test_id: int, admin_id: int = 0): # Nhận thêm admin_id từ URL
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("DELETE FROM de_thi_iig WHERE id = %s", (test_id,))
        
        if admin_id > 0:
            chi_tiet_log = f"Đã xóa đề thi IIG (Hoặc đề AI) ID: #{test_id}"
            cursor.execute("""
                INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) 
                VALUES (%s, 'admin', 'XOA_DE_THI', %s)
            """, (admin_id, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================
# API ADMIN: QUẢN LÝ TÀI KHOẢN (GIÁO VIÊN -> LỚP -> HỌC SINH)
# ===========================================================
@router.get("/get-all-teachers")
async def get_all_teachers():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT t.id, t.ten_dang_nhap, t.email, COUNT(l.id) as so_luong_lop
            FROM tai_khoan t
            LEFT JOIN lop_hoc l ON t.id = l.id_giao_vien
            WHERE t.vai_tro = 'teacher'
            GROUP BY t.id
            ORDER BY t.id DESC
        """
        
        cursor.execute(sql)
        teachers = cursor.fetchall()
        db.close()
        return {"status": "success", "data": teachers}
    except Exception as e:
        return {"status": "error", "message": str(e)}
@router.get("/get-classes-by-teacher/{teacher_id}")
async def get_classes_by_teacher(teacher_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT l.id, l.ten_lop, l.ma_tham_gia, COUNT(c.id_hoc_sinh) as so_luong_hoc_sinh 
            FROM lop_hoc l 
            LEFT JOIN chi_tiet_lop_hoc c ON l.id = c.id_lop 
            WHERE l.id_giao_vien = %s 
            GROUP BY l.id
        """
        cursor.execute(sql, (teacher_id,))
        classes = cursor.fetchall()
        db.close()
        return {"status": "success", "data": classes}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/get-students-by-class/{class_id}")
async def get_students_by_class(class_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT h.id as id_hoc_sinh, h.ten_hoc_sinh, h.ngay_sinh, h.so_dien_thoai, 
                t.id as id_tai_khoan, t.ten_dang_nhap, t.email
            FROM chi_tiet_lop_hoc c
            JOIN hoc_sinh h ON c.id_hoc_sinh = h.id_tai_khoan
            JOIN tai_khoan t ON h.id_tai_khoan = t.id
            WHERE c.id_lop = %s
        """
        cursor.execute(sql, (class_id,))
        students = cursor.fetchall()
        db.close()
        return {"status": "success", "data": students}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =============================
# THỐNG KÊ TỔNG QUAN HỆ THỐNG
# =============================
@router.get("/system-overview")
async def get_system_overview():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as total FROM tai_khoan WHERE vai_tro = 'teacher'")
        total_teachers = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM tai_khoan WHERE vai_tro = 'student'")
        total_students = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM lop_hoc")
        total_classes = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM de_thi_iig")
        total_iig_tests = cursor.fetchone()['total']
        
        cursor.execute("""
            SELECT t.ten_dang_nhap, t.email, COUNT(n.id) as so_cau_hoi_da_tao
            FROM tai_khoan t
            LEFT JOIN ngan_hang_cau_hoi n ON t.id = n.id_giao_vien
            WHERE t.vai_tro = 'teacher'
            GROUP BY t.id
            ORDER BY so_cau_hoi_da_tao DESC
            LIMIT 5
        """)
        top_teachers = cursor.fetchall()
        
        db.close()
        
        return {
            "status": "success", 
            "data": {
                "tong_giao_vien": total_teachers,
                "tong_hoc_sinh": total_students,
                "tong_lop_hoc": total_classes,
                "tong_de_iig": total_iig_tests,
                "top_giao_vien": top_teachers
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===============================================
# API THÊM/SỬA/XÓA TÀI KHOẢN (GIÁO VIÊN/HỌC SINH)
# ===============================================
def tao_ten_dang_nhap(db_cursor, ten_hoc_sinh, ngay_sinh):
    ten = ten_hoc_sinh.lower().strip()
    ten = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', ten)
    ten = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', ten)
    ten = re.sub(r'[ìíịỉĩ]', 'i', ten)
    ten = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', ten)
    ten = re.sub(r'[ùúụủũưừứựửữ]', 'u', ten)
    ten = re.sub(r'[ỳýỵỷỹ]', 'y', ten)
    ten = re.sub(r'[đ]', 'd', ten)
    
    mang = ten.split()
    ten_cuoi = mang[-1] if mang else ""
    
    try:
        ngay_obj = datetime.strptime(ngay_sinh, "%Y-%m-%d")
        ngay_str = ngay_obj.strftime("%d%m%Y")
    except:
        ngay_str = "01012000"
        
    username_goc = f"{ten_cuoi}{ngay_str}"
    username = username_goc
    
    dem = 1
    while True:
        db_cursor.execute("SELECT id FROM tai_khoan WHERE ten_dang_nhap = %s", (username,))
        if not db_cursor.fetchone():
            break
        username = f"{username_goc}{dem}"
        dem += 1
        
    return username

@router.post("/add-account")
async def add_account(acc: AccountModel, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        if acc.email and acc.email.strip() != "":
            cursor.execute("SELECT id FROM tai_khoan WHERE email = %s", (acc.email.strip(),))
            if cursor.fetchone():
                db.close()
                return {"status": "error", "message": "Email này đã được sử dụng trên hệ thống!"}
        if acc.vai_tro == 'teacher':
            if acc.ten_dang_nhap and acc.ten_dang_nhap.strip() != "":
                cursor.execute("SELECT id FROM tai_khoan WHERE ten_dang_nhap = %s", (acc.ten_dang_nhap.strip(),))
                if cursor.fetchone():
                    db.close()
                    return {"status": "error", "message": "Tên đăng nhập này đã tồn tại!"}
            if not acc.mat_khau or acc.mat_khau.strip() == "":
                acc.mat_khau = "123456"
        elif acc.vai_tro == 'student':
            if acc.so_dien_thoai and acc.so_dien_thoai.strip() != "":
                cursor.execute("SELECT id_tai_khoan FROM hoc_sinh WHERE so_dien_thoai = %s", (acc.so_dien_thoai.strip(),))
                if cursor.fetchone():
                    db.close()
                    return {"status": "error", "message": "Số điện thoại này đã được đăng ký bởi học sinh khác!"}
            acc.ten_dang_nhap = tao_ten_dang_nhap(cursor, acc.ten_hoc_sinh, acc.ngay_sinh)
            if not acc.mat_khau or acc.mat_khau.strip() == "":
                acc.mat_khau = str(random.randint(100000, 999999))
                
        hashed_password = hash_password(acc.mat_khau)
        
        sql = "INSERT INTO tai_khoan (ten_dang_nhap, email, mat_khau, vai_tro) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql, (acc.ten_dang_nhap, acc.email, hashed_password, acc.vai_tro))
        new_acc_id = cursor.lastrowid
        
        if acc.vai_tro == 'student':
            sql_hs = "INSERT INTO hoc_sinh (ten_hoc_sinh, ngay_sinh, so_dien_thoai, email, id_tai_khoan) VALUES (%s, %s, %s, %s, %s)"
            cursor.execute(sql_hs, (acc.ten_hoc_sinh, acc.ngay_sinh, acc.so_dien_thoai, acc.email, new_acc_id))
            if getattr(acc, 'id_lop', None) is not None:
                cursor.execute("INSERT INTO chi_tiet_lop_hoc (id_hoc_sinh, id_lop, trang_thai_hoc) VALUES (%s, %s, 'dang_hoc')", (new_acc_id, acc.id_lop))
                
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã tạo tài khoản {acc.vai_tro} mới: '{acc.ten_dang_nhap}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TAI_KHOAN', %s)", (id_admin, chi_tiet_log))

        db.commit()
        db.close()
        
        return {
            "status": "success", 
            "message": "Đã tạo tài khoản thành công!",
            "credentials": {
                "username": acc.ten_dang_nhap,
                "password": acc.mat_khau
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/update-account/{acc_id}")
async def update_account(acc_id: int, acc: AccountModel, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # BẮT LỖI TRÙNG EMAIL
        if acc.email and acc.email.strip() != "":
            cursor.execute("SELECT id FROM tai_khoan WHERE email = %s AND id != %s", (acc.email.strip(), acc_id))
            if cursor.fetchone():
                db.close()
                return {"status": "error", "message": "Email này đã được sử dụng bởi một tài khoản khác!"}

        cursor.execute("UPDATE tai_khoan SET email = %s WHERE id = %s", (acc.email, acc_id))
        
        if acc.mat_khau and acc.mat_khau.strip() != "":
            hashed_password = hash_password(acc.mat_khau)
            cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_password, acc_id))
        
        if acc.vai_tro == 'teacher':
            # Bắt lỗi trùng Tên đăng nhập
            if acc.ten_dang_nhap and acc.ten_dang_nhap.strip() != "":
                cursor.execute("SELECT id FROM tai_khoan WHERE ten_dang_nhap = %s AND id != %s", (acc.ten_dang_nhap.strip(), acc_id))
                if cursor.fetchone():
                    db.close()
                    return {"status": "error", "message": "Tên đăng nhập này đã có người sử dụng!"}
            
            cursor.execute("UPDATE tai_khoan SET ten_dang_nhap = %s WHERE id = %s", (acc.ten_dang_nhap, acc_id))
            
        elif acc.vai_tro == 'student':
            # Bắt lỗi trùng Số điện thoại 
            if acc.so_dien_thoai and acc.so_dien_thoai.strip() != "":
                cursor.execute("SELECT id_tai_khoan FROM hoc_sinh WHERE so_dien_thoai = %s AND id_tai_khoan != %s", (acc.so_dien_thoai.strip(), acc_id))
                if cursor.fetchone():
                    db.close()
                    return {"status": "error", "message": "Số điện thoại này thuộc về học sinh khác!"}
                    
            cursor.execute("UPDATE hoc_sinh SET ten_hoc_sinh = %s, ngay_sinh = %s, so_dien_thoai = %s WHERE id_tai_khoan = %s", 
               (acc.ten_hoc_sinh, acc.ngay_sinh, acc.so_dien_thoai, acc_id))
            
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã cập nhật thông tin tài khoản ID: #{acc_id}"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TAI_KHOAN', %s)", (id_admin, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã cập nhật tài khoản thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    
@router.post("/delete-account/{acc_id}")
async def delete_account(acc_id: int, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT vai_tro, ten_dang_nhap FROM tai_khoan WHERE id = %s", (acc_id,))
        acc = cursor.fetchone()
        if not acc:
            return {"status": "error", "message": "Tài khoản không tồn tại."}

        role = acc['vai_tro']

        if role == 'teacher':
            cursor.execute("SELECT id FROM lop_hoc WHERE id_giao_vien = %s", (acc_id,))
            classes = cursor.fetchall()
            for c in classes:
                class_id = c['id']
                cursor.execute("SELECT id_hoc_sinh FROM chi_tiet_lop_hoc WHERE id_lop = %s", (class_id,))
                student_acc_ids = [s['id_hoc_sinh'] for s in cursor.fetchall()]
                
                cursor.execute("DELETE FROM chi_tiet_lop_hoc WHERE id_lop = %s", (class_id,))
                
                if student_acc_ids:
                    format_strings = ','.join(['%s'] * len(student_acc_ids))
                    cursor.execute(f"DELETE FROM hoc_sinh WHERE id_tai_khoan IN ({format_strings})", tuple(student_acc_ids))
                    cursor.execute(f"DELETE FROM tai_khoan WHERE id IN ({format_strings})", tuple(student_acc_ids))
            cursor.execute("DELETE FROM lop_hoc WHERE id_giao_vien = %s", (acc_id,))

        elif role == 'student':
            cursor.execute("DELETE FROM chi_tiet_lop_hoc WHERE id_hoc_sinh = %s", (acc_id,))
            cursor.execute("DELETE FROM hoc_sinh WHERE id_tai_khoan = %s", (acc_id,))

        cursor.execute("DELETE FROM tai_khoan WHERE id = %s", (acc_id,))

        # --- LƯU NHẬT KÝ ---
        if acc:
            chi_tiet_log = f"Đã xóa vĩnh viễn tài khoản {role}: '{acc['ten_dang_nhap']}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TAI_KHOAN', %s)", (id_admin, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa sạch tài khoản và dữ liệu liên quan!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/reset-password/{acc_id}")
async def reset_password(
    acc_id: int, 
    id_nguoi_dung: int = Query(0), 
    vai_tro: str = Query("admin")
):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # Mật khẩu mặc định
        default_pass = "123456"
        hashed_password = hash_password(default_pass)
        
        cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_password, acc_id))
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã khôi phục mật khẩu mặc định (123456) cho tài khoản ID: #{acc_id}"
        
        if id_nguoi_dung > 0:
            cursor.execute(
                "INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, %s, 'QUAN_LY_TAI_KHOAN', %s)", 
                (id_nguoi_dung, vai_tro, chi_tiet_log)
            )

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã reset mật khẩu thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    

# ==========================================
# API THÊM/SỬA/XÓA LỚP HỌC
# ==========================================
@router.post("/add-class")
async def add_class(cls: ClassModel, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("SELECT id FROM lop_hoc WHERE ten_lop = %s AND id_giao_vien = %s", (cls.ten_lop.strip(), cls.id_giao_vien))
        if cursor.fetchone():
            db.close()
            return {"status": "error", "message": "Giáo viên này đã có một lớp học mang tên này rồi!"}
            
        cursor.execute("INSERT INTO lop_hoc (ten_lop, ma_tham_gia, id_giao_vien) VALUES (%s, %s, %s)", 
               (cls.ten_lop, cls.ma_tham_gia, cls.id_giao_vien))
               
        # --- LƯU NHẬT KÝ ADMIN ---
        actor_id = id_admin if id_admin > 0 else cls.id_giao_vien
        role_type = 'admin' if id_admin > 0 else 'teacher'
        chi_tiet_log = f"Đã thêm lớp học mới: '{cls.ten_lop}' cho giáo viên ID #{cls.id_giao_vien}"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, %s, 'QUAN_LY_LOP_HOC', %s)", (actor_id, role_type, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã thêm lớp học thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-class/{class_id}")
async def update_class(class_id: int, cls: ClassModel, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True) 
        
        cursor.execute("SELECT id_giao_vien, ten_lop FROM lop_hoc WHERE id = %s", (class_id,))
        lop_hien_tai = cursor.fetchone()
        if not lop_hien_tai:
            db.close()
            return {"status": "error", "message": "Lớp học không tồn tại!"}
            
        id_giao_vien = lop_hien_tai['id_giao_vien']
        cursor.execute("SELECT id FROM lop_hoc WHERE ten_lop = %s AND id_giao_vien = %s AND id != %s", (cls.ten_lop.strip(), id_giao_vien, class_id))
        if cursor.fetchone():
            db.close()
            return {"status": "error", "message": "Tên lớp này bị trùng với một lớp khác của cùng giáo viên!"}
            
        cursor.execute("UPDATE lop_hoc SET ten_lop = %s, ma_tham_gia = %s WHERE id = %s", 
               (cls.ten_lop, cls.ma_tham_gia, class_id))
               
        # --- LƯU NHẬT KÝ ADMIN ---
        actor_id = id_admin if id_admin > 0 else id_giao_vien
        role_type = 'admin' if id_admin > 0 else 'teacher'
        chi_tiet_log = f"Đã sửa thông tin lớp: '{lop_hien_tai['ten_lop']}' thành '{cls.ten_lop}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, %s, 'QUAN_LY_LOP_HOC', %s)", (actor_id, role_type, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã cập nhật lớp học thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-class/{class_id}")
async def delete_class(class_id: int, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT id_giao_vien, ten_lop FROM lop_hoc WHERE id = %s", (class_id,))
        lop_hien_tai = cursor.fetchone()

        cursor.execute("SELECT id_hoc_sinh FROM chi_tiet_lop_hoc WHERE id_lop = %s", (class_id,))
        student_acc_ids = [s['id_hoc_sinh'] for s in cursor.fetchall()]

        cursor.execute("DELETE FROM chi_tiet_lop_hoc WHERE id_lop = %s", (class_id,))

        if student_acc_ids:
            format_strings = ','.join(['%s'] * len(student_acc_ids))
            cursor.execute(f"DELETE FROM hoc_sinh WHERE id_tai_khoan IN ({format_strings})", tuple(student_acc_ids))
            cursor.execute(f"DELETE FROM tai_khoan WHERE id IN ({format_strings})", tuple(student_acc_ids))

        cursor.execute("DELETE FROM lop_hoc WHERE id = %s", (class_id,))

        # --- LƯU NHẬT KÝ ADMIN ---
        if lop_hien_tai:
            actor_id = id_admin if id_admin > 0 else lop_hien_tai['id_giao_vien']
            role_type = 'admin' if id_admin > 0 else 'teacher'
            chi_tiet_log = f"Đã xóa lớp: '{lop_hien_tai['ten_lop']}' và giải tán {len(student_acc_ids)} học sinh"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, %s, 'QUAN_LY_LOP_HOC', %s)", (actor_id, role_type, chi_tiet_log))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa lớp và giải tán toàn bộ học sinh liên quan!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
# ==========================================
# QUẢN LÝ TIN TỨC (BLOG)
# ==========================================

@router.post("/tao-tin-tuc")
async def tao_tin_tuc(
    tieu_de: str = Form(...),
    tom_tat: str = Form(...),
    noi_dung: str = Form(...),
    id_admin: int = Form(...),
    anh_dai_dien: UploadFile = File(...)
):
    try:
        upload_dir = "uploads/tin_tuc"
        os.makedirs(upload_dir, exist_ok=True)
        
        file_ext = os.path.splitext(anh_dai_dien.filename)[1]
        new_filename = f"news_{uuid.uuid4().hex}{file_ext}"
        file_path = os.path.join(upload_dir, new_filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(anh_dai_dien.file, buffer)
            
        db_img_path = file_path.replace(os.sep, '/')

        db = get_db_connection()
        cursor = db.cursor()
        sql = """INSERT INTO tin_tuc (tieu_de, tom_tat, noi_dung, anh_dai_dien, id_admin) 
                 VALUES (%s, %s, %s, %s, %s)"""
        cursor.execute(sql, (tieu_de, tom_tat, noi_dung, db_img_path, id_admin))
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã đăng tin tức mới: '{tieu_de}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TIN_TUC', %s)", (id_admin, chi_tiet_log))
        
        db.commit()
        new_id = cursor.lastrowid
        cursor.close()
        db.close()
        
        return {"status": "success", "message": "Đã tạo tin tức thành công!", "id": new_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/get-all-news")
async def get_all_news():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, tieu_de, tom_tat, anh_dai_dien, ngay_dang, id_admin FROM tin_tuc ORDER BY ngay_dang DESC")
        news_list = cursor.fetchall()
        db.close()
        return {"status": "success", "data": news_list}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/get-news/{news_id}")
async def get_news_detail(news_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tin_tuc WHERE id = %s", (news_id,))
        news_detail = cursor.fetchone()
        db.close()
        
        if news_detail:
            return {"status": "success", "data": news_detail}
        return {"status": "error", "message": "Không tìm thấy bài viết."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-news/{news_id}")
async def update_news(
    news_id: int,
    tieu_de: str = Form(...),
    tom_tat: str = Form(...),
    noi_dung: str = Form(...),
    anh_dai_dien: Optional[UploadFile] = File(None) 
):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id_admin FROM tin_tuc WHERE id = %s", (news_id,))
        news_info = cursor.fetchone()
        id_admin = news_info['id_admin'] if news_info else 0
        
        if anh_dai_dien and anh_dai_dien.filename:
            upload_dir = "uploads/tin_tuc"
            os.makedirs(upload_dir, exist_ok=True)
            
            file_ext = os.path.splitext(anh_dai_dien.filename)[1]
            new_filename = f"news_{uuid.uuid4().hex}{file_ext}"
            file_path = os.path.join(upload_dir, new_filename)
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(anh_dai_dien.file, buffer)
                
            db_img_path = file_path.replace(os.sep, '/')
            
            sql = """UPDATE tin_tuc SET tieu_de = %s, tom_tat = %s, noi_dung = %s, anh_dai_dien = %s WHERE id = %s"""
            cursor.execute(sql, (tieu_de, tom_tat, noi_dung, db_img_path, news_id))
        else:
            sql = """UPDATE tin_tuc SET tieu_de = %s, tom_tat = %s, noi_dung = %s WHERE id = %s"""
            cursor.execute(sql, (tieu_de, tom_tat, noi_dung, news_id))
            
        # --- LƯU NHẬT KÝ ---
        if id_admin:
            chi_tiet_log = f"Đã cập nhật tin tức: '{tieu_de}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TIN_TUC', %s)", (id_admin, chi_tiet_log))
            
        db.commit()
        cursor.close()
        db.close()
        return {"status": "success", "message": "Đã cập nhật tin tức thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-news/{news_id}")
async def delete_news(news_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id_admin, tieu_de FROM tin_tuc WHERE id = %s", (news_id,))
        news_info = cursor.fetchone()
        
        cursor.execute("DELETE FROM tin_tuc WHERE id = %s", (news_id,))
        
        # --- LƯU NHẬT KÝ ---
        if news_info:
            chi_tiet_log = f"Đã xóa tin tức: '{news_info['tieu_de']}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'admin', 'QUAN_LY_TIN_TUC', %s)", (news_info['id_admin'], chi_tiet_log))
            
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa bài viết thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# QUẢN LÝ THÔNG TIN CÁ NHÂN (PROFILE)
# ==========================================

@router.get("/get-profile/{acc_id}")
async def get_user_profile(acc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT email, vai_tro FROM tai_khoan WHERE id = %s", (acc_id,))
        user = cursor.fetchone()
        
        if not user:
            return {"status": "error", "message": "Không tìm thấy tài khoản"}
            
        data = {"email": user['email'] if user['email'] else ""}
        
        if user['vai_tro'] == 'student':
            cursor.execute("SELECT ten_hoc_sinh, ngay_sinh, so_dien_thoai FROM hoc_sinh WHERE id_tai_khoan = %s", (acc_id,))
            hs = cursor.fetchone()
            if hs:
                data['ten_hoc_sinh'] = hs['ten_hoc_sinh'] if hs['ten_hoc_sinh'] else ""
                data['ngay_sinh'] = str(hs['ngay_sinh']) if hs['ngay_sinh'] else ""
                data['so_dien_thoai'] = hs['so_dien_thoai'] if hs['so_dien_thoai'] else ""
                
        db.close()
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-profile/{acc_id}")
async def user_update_profile(acc_id: int, req: UpdateProfileModel):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT mat_khau FROM tai_khoan WHERE id = %s", (acc_id,))
        user = cursor.fetchone()
        if not user:
            return {"status": "error", "message": "Tài khoản không tồn tại!"}

        if req.email and req.email.strip() != "":
            # Bắt lỗi trùng Email
            cursor.execute("SELECT id FROM tai_khoan WHERE email = %s AND id != %s", (req.email.strip(), acc_id))
            if cursor.fetchone():
                db.close()
                return {"status": "error", "message": "Email này đã được tài khoản khác sử dụng!"}
                
            cursor.execute("UPDATE tai_khoan SET email = %s WHERE id = %s", (req.email.strip(), acc_id))
            
        if req.mat_khau_moi and req.mat_khau_moi.strip() != "":
            if not bcrypt.checkpw(req.mat_khau_cu.encode('utf-8'), user['mat_khau'].encode('utf-8')):
                return {"status": "error", "message": "Mật khẩu hiện tại không chính xác!"}
                
            hashed_new = hash_password(req.mat_khau_moi)
            cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_new, acc_id))
            
        if req.vai_tro == 'student':
            cursor.execute("SELECT ten_hoc_sinh, ngay_sinh, so_dien_thoai FROM hoc_sinh WHERE id_tai_khoan = %s", (acc_id,))
            hs_old = cursor.fetchone()
            
            if hs_old:
                new_ten = req.ten_hoc_sinh.strip() if req.ten_hoc_sinh and req.ten_hoc_sinh.strip() != "" else hs_old['ten_hoc_sinh']
                new_ns = req.ngay_sinh.strip() if req.ngay_sinh and req.ngay_sinh.strip() != "" else hs_old['ngay_sinh']
                new_sdt = req.so_dien_thoai.strip() if req.so_dien_thoai and req.so_dien_thoai.strip() != "" else hs_old['so_dien_thoai']
                
                if req.so_dien_thoai and req.so_dien_thoai.strip() != "":
                    if not re.match(r"^(0[35789])[0-9]{8}$", req.so_dien_thoai.strip()):
                        return {"status": "error", "message": "Số điện thoại không hợp lệ!"}
                    # Bắt lỗi trùng Số điện thoại
                    cursor.execute("SELECT id_tai_khoan FROM hoc_sinh WHERE so_dien_thoai = %s AND id_tai_khoan != %s", (req.so_dien_thoai.strip(), acc_id))
                    if cursor.fetchone():
                        db.close()
                        return {"status": "error", "message": "Số điện thoại này đã được học sinh khác sử dụng!"}
                        
                cursor.execute("""
                    UPDATE hoc_sinh 
                    SET ten_hoc_sinh = %s, ngay_sinh = %s, so_dien_thoai = %s 
                    WHERE id_tai_khoan = %s
                """, (new_ten, new_ns, new_sdt, acc_id))

        db.commit()
        db.close()
        return {"status": "success", "message": "Cập nhật thông tin cá nhân thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# 1. API ADMIN: XEM NHẬT KÝ THAO TÁC CỦA TẤT CẢ ADMIN
# ==========================================
@router.get("/get-admin-logs")
async def get_all_admin_logs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT n.*, t.ten_dang_nhap, t.email 
            FROM nhat_ky_hoat_dong n
            LEFT JOIN tai_khoan t ON n.id_tai_khoan = t.id
            WHERE n.vai_tro = 'admin'
            ORDER BY n.thoi_gian DESC LIMIT 500
        """
        cursor.execute(sql)
        logs = cursor.fetchall()
        db.close()
        return {"status": "success", "data": logs}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# 2. API ADMIN: XEM NHẬT KÝ HỆ THỐNG (CHỈ GIÁO VIÊN & HỌC SINH)
# ==========================================
@router.get("/get-system-logs")
async def get_system_logs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT n.*, t.ten_dang_nhap, t.email 
            FROM nhat_ky_hoat_dong n
            LEFT JOIN tai_khoan t ON n.id_tai_khoan = t.id
            WHERE n.vai_tro IN ('teacher', 'student')
            ORDER BY n.thoi_gian DESC LIMIT 500
        """
        cursor.execute(sql)
        logs = cursor.fetchall()
        db.close()
        return {"status": "success", "data": logs}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ===================================================================
# HÀM BỔ TRỢ GHI LOG HỆ THỐNG
# ===================================================================
def write_system_log(cursor, id_tai_khoan, action_desc): #cả 3 router (ở đầu file router trước khi viết api)
    try:
        cursor.execute("SELECT ten_dang_nhap FROM tai_khoan WHERE id = %s", (id_tai_khoan,))
        user = cursor.fetchone()
        ten_tk = user['ten_dang_nhap'] if isinstance(user, dict) and user else (user[0] if user else "Unknown")
        
        sql_log = "INSERT INTO lich_su_xem_tai_lieu (id_tai_khoan, ten_tai_khoan, hanh_dong) VALUES (%s, %s, %s)"
        cursor.execute(sql_log, (id_tai_khoan, ten_tk, action_desc))
    except Exception as e:
        print("LỖI GHI LOG:", e)

# ===================================================================
# NHÓM 1: UPLOAD VÀ XỬ LÝ TÀI LIỆU BẰNG AI
# ===================================================================
@router.post("/upload-document-only")
async def upload_document_only(file: UploadFile = File(...)):
    try:
        goc_dir = "uploads/tai_lieu_goc"
        os.makedirs(goc_dir, exist_ok=True)
        
        file_ext = os.path.splitext(file.filename)[1].lower()
        file_id = uuid.uuid4().hex
        
        saved_filename = f"{file_id}{file_ext}"
        saved_filepath = os.path.join(goc_dir, saved_filename)
        with open(saved_filepath, "wb") as buffer:
            buffer.write(await file.read())
            
        file_goc_url = f"http://127.0.0.1:80/ai_local/AI_chat/uploads/tai_lieu_goc/{saved_filename}"
        
        return {"status": "success", "file_goc": file_goc_url}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/extract-document-ai")
async def extract_document_ai(file: UploadFile = File(...)):
    try:
        goc_dir = "uploads/tai_lieu_goc"
        ext_dir = "uploads/extracted_docs"
        os.makedirs(goc_dir, exist_ok=True)
        os.makedirs(ext_dir, exist_ok=True)
        
        file_ext = os.path.splitext(file.filename)[1].lower()
        file_id = uuid.uuid4().hex
        
        # 1. LƯU FILE GỐC
        saved_filename = f"{file_id}{file_ext}"
        saved_filepath = os.path.join(goc_dir, saved_filename)
        with open(saved_filepath, "wb") as buffer:
            buffer.write(await file.read())
            
        file_goc_url = f"http://127.0.0.1:80/ai_local/AI_chat/uploads/tai_lieu_goc/{saved_filename}"
        raw_html = ""

        # 2. RÃ PDF LẤY CHỮ VÀ ẢNH
        if file_ext == '.pdf':
            doc = fitz.open(saved_filepath)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                blocks = page.get_text("dict").get("blocks", [])
                blocks.sort(key=lambda b: (round(b.get("bbox", [0,0,0,0])[1] / 10), b.get("bbox", [0,0,0,0])[0]))
                
                for b in blocks:
                    if b.get('type') == 0:
                        text = " ".join([span.get("text", "") for line in b.get("lines", []) for span in line.get("spans", [])])
                        if text.strip(): raw_html += f"<p>{text.strip()}</p>\n"
                    elif b.get('type') == 1:
                        img_bytes = b.get("image")
                        if img_bytes and len(img_bytes) > 2048:
                            img_name = f"ext_{uuid.uuid4().hex}.png"
                            with open(os.path.join(ext_dir, img_name), "wb") as img_file:
                                img_file.write(img_bytes)
                            img_url = f"http://127.0.0.1:80/ai_local/AI_chat/uploads/extracted_docs/{img_name}"
                            raw_html += f'<div style="text-align:center; margin:15px 0;"><img src="{img_url}" style="max-width:100%; border-radius:8px;"></div>\n'
            doc.close()

        # 3. RÃ ẢNH LẤY CHỮ
        elif file_ext in ['.png', '.jpg', '.jpeg']:
            model = genai.GenerativeModel('gemini-3.8-flash')
            with open(saved_filepath, "rb") as f:
                image_parts = [{"mime_type": file.content_type, "data": f.read()}]
            response = await model.generate_content_async([
                image_parts[0], 
                "OCR chính xác 100% nội dung chữ trong ảnh, định dạng HTML cơ bản. Không tự sáng tác thêm."
            ])
            raw_html = response.text.replace("```html", "").replace("```", "").strip()
        else:
            return {"status": "error", "message": "Chỉ hỗ trợ PDF hoặc Hình ảnh."}

# 4. AI LÀM ĐẸP (POLISH) TRƯỚC KHI TRẢ VỀ DÙNG QWEN3-14B
        if raw_html:
            messages = [
                {
                    "role": "system",
                    "content": """Bạn là chuyên gia thiết kế bài giảng web. Hãy cấu trúc lại văn bản thô.
LỆNH TỐI THƯỢNG ĐỂ CHỐNG LỖI CẮT CỤT (CUT-OFF):
1. GIẢM THIỂU THẺ HTML. CẤM vẽ bảng (<table>) cho các nội dung dài. Hãy chuyển đổi các đoạn so sánh thành mục gạch đầu dòng (<ul> <li>) hoặc in đậm tiêu đề (<b>) để tiết kiệm bộ nhớ.
2. TUYỆT ĐỐI KHÔNG TÓM TẮT. Phải xử lý và xuất ra toàn bộ nội dung đến chữ cuối cùng.
3. KHÔNG dùng CSS nội tuyến (style="..."). Chỉ trả về mã HTML sạch."""
                },
                {
                    "role": "user",
                    "content": f"[NỘI DUNG CẦN CẤU TRÚC LẠI]\n{raw_html}"
                }
            ]
            
            response = await hf_client.chat_completion(
                model="Qwen/Qwen3-14B",
                messages=messages,
                max_tokens=5000,
                temperature=0.1 # Để nhiệt độ thấp giúp định dạng HTML chuẩn xác hơn
            )
            
            clean_html = response.choices[0].message.content.replace("```html", "").replace("```", "").strip()
            return {"status": "success", "data": clean_html, "file_goc": file_goc_url}

        return {"status": "error", "message": "Không có nội dung bóc tách."}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Lỗi Backend: {str(e)}"}


# ===================================================================
# NHÓM 2: QUẢN LÝ DỮ LIỆU TÀI LIỆU (CRUD)
# ===================================================================
@router.get("/get-all-grammar-docs")
async def get_all_grammar_docs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # ĐÃ SỬA: Nối qua tk.id thay vì tk.id_tai_khoan
        sql = """
            SELECT t.*, tk.vai_tro as vai_tro_tao, tk.ten_dang_nhap as ten_nguoi_tao 
            FROM tai_lieu_hoc t 
            LEFT JOIN tai_khoan tk ON t.id_nguoi_tao = tk.id 
            ORDER BY t.id DESC
        """
        cursor.execute(sql)
        docs = cursor.fetchall()
        db.close()
        return {"status": "success", "data": docs}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/save-grammar-doc")
async def save_grammar_doc(doc: TaiLieuHocModel): 
    try:
        db = get_db_connection()
        cursor = db.cursor()

        # 1. KIỂM TRA TRÙNG TÊN TÀI LIỆU
        if doc.id:
            cursor.execute("SELECT id FROM tai_lieu_hoc WHERE tieu_de = %s AND id != %s", (doc.tieu_de, doc.id))
        else:
            cursor.execute("SELECT id FROM tai_lieu_hoc WHERE tieu_de = %s", (doc.tieu_de,))
            
        if cursor.fetchone():
            db.close()
            return {"status": "error", "message": "Tên tài liệu này đã tồn tại trong kho. Vui lòng chọn tên khác!"}

        # 2. TIẾN HÀNH LƯU
        if doc.id:
            sql = "UPDATE tai_lieu_hoc SET tieu_de=%s, loai_tai_lieu=%s, tags=%s, noi_dung=%s, file_goc=%s, bai_tap_json=%s WHERE id=%s"
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung, doc.file_goc, doc.bai_tap_json, doc.id))
        else:
            sql = "INSERT INTO tai_lieu_hoc (tieu_de, loai_tai_lieu, tags, noi_dung, file_goc, id_nguoi_tao, bai_tap_json) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung, doc.file_goc, doc.id_nguoi_tao, doc.bai_tap_json))
            
        # --- BỔ SUNG GHI LOG ---
        hieu_lenh = "đã sửa" if doc.id else "đã thêm"
        hanh_dong = f"{hieu_lenh} tài liệu: {doc.tieu_de}"
        write_system_log(cursor, doc.id_nguoi_tao, hanh_dong)
        # -----------------------

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã lưu tài liệu thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-grammar-doc/{doc_id}")
async def delete_grammar_doc(doc_id: int, id_admin: int = Query(0)):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # 1. Lấy thông tin tài liệu trước khi xóa (Bắt buộc phải có id_nguoi_tao)
        cursor.execute("SELECT tieu_de, id_nguoi_tao FROM tai_lieu_hoc WHERE id = %s", (doc_id,))
        doc = cursor.fetchone()
        
        if doc:
            # 2. Xóa tài liệu
            cursor.execute("DELETE FROM tai_lieu_hoc WHERE id = %s", (doc_id,))
            
            # --- 3. BỔ SUNG GHI LOG ---
            # Nếu Frontend không gửi id_admin, lấy tạm id_nguoi_tao làm người thực hiện hành động
            nguoi_thuc_hien = id_admin if id_admin > 0 else doc['id_nguoi_tao']
            hanh_dong = f"Đã xóa tài liệu: '{doc['tieu_de']}'"
            write_system_log(cursor, nguoi_thuc_hien, hanh_dong)
            # -----------------------
            
            db.commit()
            
        db.close()
        return {"status": "success", "message": "Đã xóa tài liệu khỏi hệ thống!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
# ===================================================================
# NHÓM 3: GHI NHẬN VÀ TRUY XUẤT LỊCH SỬ XEM TÀI LIỆU
# ===================================================================
@router.get("/document-view-logs") #router_admin and router_teacher
async def get_document_view_logs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        sql = """
            SELECT l.id, l.ten_tai_khoan, l.hanh_dong, l.thoi_gian, t.vai_tro
            FROM lich_su_xem_tai_lieu l
            LEFT JOIN tai_khoan t ON l.id_tai_khoan = t.id
            ORDER BY l.thoi_gian DESC LIMIT 200
        """
        cursor.execute(sql)
        logs = cursor.fetchall()
        db.close()
        
        for log in logs:
            if log.get('thoi_gian'):
                log['thoi_gian'] = log['thoi_gian'].strftime("%d/%m/%Y %H:%M:%S")
                
        return {"status": "success", "data": logs}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/all-doc-view-history") #router_admin.py
async def get_all_doc_view_history():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT ten_tai_khoan as ten_hoc_sinh, hanh_dong as tieu_de, thoi_gian as thoi_gian_xem
            FROM lich_su_xem_tai_lieu
            ORDER BY thoi_gian DESC LIMIT 200
        """
        cursor.execute(sql)
        history = cursor.fetchall()
        db.close()
        return {"status": "success", "data": history}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/get-grammar-doc/{doc_id}")
async def get_grammar_doc(doc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tai_lieu_hoc WHERE id = %s", (doc_id,))
        doc = cursor.fetchone()
        db.close()
        
        if doc:
            return {"status": "success", "data": doc}
        else:
            return {"status": "error", "message": "Không tìm thấy tài liệu!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# API TẠO CÂU HỎI TRẮC NGHIỆM MỚI DÙNG CHUNG AI (HUGGINGFACE)
@router.post("/generate-doc-quiz")
async def generate_doc_quiz(req: GenerateQuizRequest):
    try:
        clean_text = re.sub(r'<[^>]+>', ' ', req.noi_dung).strip()
        clean_text = clean_text[:4000] 
        
        if not clean_text:
            return {"status": "error", "message": "Nội dung bài giảng đang trống."}

        messages = [
            {
                "role": "system",
                "content": "Bạn là chuyên gia ra đề thi trắc nghiệm Tiếng Anh. YÊU CẦU TỐI THƯỢNG: Chỉ trả về một mảng JSON duy nhất. Không sinh ra bất kỳ văn bản, thẻ markdown (```json), lời giải thích bên ngoài hay lời chào nào khác."
            },
            {
                "role": "user",
                "content": f"""Nhiệm vụ: Phân tích đoạn văn bản bài học dưới đây. Tự động sinh ra từ {req.so_luong} câu trắc nghiệm tùy thuộc vào độ dài kiến thức.

TIÊU CHÍ CHẤT LƯỢNG:
1. Nội dung câu hỏi BẮT BUỘC phải nằm trong văn bản cung cấp.
2. Có 4 đáp án (1 đúng, 3 sai).
3. LUẬT THÉP CHỐNG LỖI JSON: TUYỆT ĐỐI KHÔNG sử dụng dấu ngoặc kép (") bên trong nội dung câu hỏi, đáp án và giải thích. Nếu cần trích dẫn từ vựng, BẮT BUỘC dùng dấu nháy đơn (').
- Ví dụ SAI: "explanation": "Từ "go" là động từ"
- Ví dụ ĐÚNG: "explanation": "Từ 'go' là động từ"

Cấu trúc JSON BẮT BUỘC (Không thừa không thiếu):
[
  {{
    "question": "Nội dung câu hỏi?",
    "options": ["A. Đáp án 1", "B. Đáp án 2", "C. Đáp án 3", "D. Đáp án 4"],
    "correctIndex": 0,
    "explanation": "Vì trong bài ghi rõ rằng..."
  }}
]

VĂN BẢN BÀI HỌC CẦN RA ĐỀ:
{clean_text}"""
            }
        ]        
        response = await hf_client.chat_completion(
            model="Qwen/Qwen3-32B",
            messages=messages,
            max_tokens=1000, # Tăng max_tokens một chút để đề phòng sinh 3 câu dài
            temperature=0.15 # Tăng nhiệt độ lên 1 chút để AI có độ mượt khi tự quyết định số câu
        )
        
        raw_text = response.choices[0].message.content.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        
        start_idx = raw_text.find('[')
        end_idx = raw_text.rfind(']')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            clean_json_string = raw_text[start_idx:end_idx+1]
            clean_json_string = clean_json_string.replace('\n', ' ').replace('\r', '')
            
            # --- BỘ LỌC TỰ CHỮA LÀNH JSON (AUTO-HEALER) ---
            # Quét và biến các dấu ngoặc kép "lưu manh" thành dấu nháy đơn '
            chars = list(clean_json_string)
            for i, c in enumerate(chars):
                if c == '"':
                    # Tìm ký tự có nghĩa liền trước
                    prev_char = ''
                    for j in range(i-1, -1, -1):
                        if chars[j].strip():
                            prev_char = chars[j]
                            break
                            
                    # Tìm ký tự có nghĩa liền sau
                    next_char = ''
                    for j in range(i+1, len(chars)):
                        if chars[j].strip():
                            next_char = chars[j]
                            break
                    
                    # Nếu ngoặc kép KHÔNG ĐỨNG CẠNH các dấu phẩy, hai chấm hay ngoặc nhọn của JSON
                    # => Chắc chắn là ngoặc kép rác do AI tự chế -> Đổi thành nháy đơn
                    if prev_char not in '{[:,[' and next_char not in '}]:,]':
                        chars[i] = "'"
            
            clean_json_string = "".join(chars)
            
            try:
                quiz_data = json.loads(clean_json_string)
                return {"status": "success", "data": quiz_data}
            except Exception as parse_err:
                print("LỖI PARSE JSON:", str(parse_err))
                print("CHUỖI BỊ LỖI:", clean_json_string)
                return {"status": "error", "message": "AI sinh ra dữ liệu có chứa ký tự lạ làm hỏng JSON."}
        else:
            print("LỖI KHÔNG TÌM THẤY DẤU NGOẶC MẢNG:", raw_text)
            return {"status": "error", "message": "Model không thể định dạng JSON hợp lệ."}
    except Exception as e:
        print("LỖI HỆ THỐNG KHI GENERATE QUIZ:", str(e))
        return {"status": "error", "message": str(e)}
