from fastapi import APIRouter, UploadFile, File, Form
from database import get_db_connection
import uuid
import shutil
import re
import random
import os
from datetime import datetime
from schemas import UpdateAnswersModel, AccountModel, ClassModel, UpdateProfileModel, TaiLieuHocModel
import bcrypt  
from typing import Optional
import pymupdf as fitz  # PyMuPDF
import google.generativeai as genai
import datetime
router = APIRouter()

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

# ==========================================
# API ADMIN: TẢI ĐỀ IIG LÊN HỆ THỐNG
# ==========================================
@router.post("/upload-iig")
async def upload_iig(
    ten_de: str = Form(...),
    nam_phat_hanh: int = Form(...),
    dap_an_json: str = Form(...),
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
        cursor.execute("SELECT id, ten_de, nam_phat_hanh, ngay_tao FROM de_thi_iig ORDER BY ngay_tao DESC")
        tests = cursor.fetchall()
        db.close()
        return {"status": "success", "data": tests}
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
async def update_iig_answers(test_id: int, req: UpdateAnswersModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("UPDATE de_thi_iig SET dap_an_json = %s WHERE id = %s", (req.dap_an_json, test_id))
        db.commit()
        
        cursor.close()
        db.close()
        return {"status": "success", "message": "Đã cập nhật lại khóa đáp án thành công!"}
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
            SELECT l.id, l.ten_lop, l.ma_tham_gia, COUNT(h.id_tai_khoan) as so_luong_hoc_sinh 
            FROM lop_hoc l 
            LEFT JOIN hoc_sinh h ON l.id = h.id_lop 
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
            FROM hoc_sinh h
            JOIN tai_khoan t ON h.id_tai_khoan = t.id
            WHERE h.id_lop = %s
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

# ==========================================
# API XÓA ĐỀ THI IIG
# ==========================================
@router.post("/delete-iig-test/{test_id}")
async def delete_iig_test(test_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM de_thi_iig WHERE id = %s", (test_id,))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa đề thi thành công!"}
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
async def add_account(acc: AccountModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        # 1. BẮT LỖI TRÙNG EMAIL
        if acc.email and acc.email.strip() != "":
            cursor.execute("SELECT id FROM tai_khoan WHERE email = %s", (acc.email.strip(),))
            if cursor.fetchone():
                db.close()
                return {"status": "error", "message": "Email này đã được sử dụng trên hệ thống!"}
        if acc.vai_tro == 'teacher':
            # bắt lỗi trùng Tên đăng nhập
            if acc.ten_dang_nhap and acc.ten_dang_nhap.strip() != "":
                cursor.execute("SELECT id FROM tai_khoan WHERE ten_dang_nhap = %s", (acc.ten_dang_nhap.strip(),))
                if cursor.fetchone():
                    db.close()
                    return {"status": "error", "message": "Tên đăng nhập này đã tồn tại!"}
                    
            if not acc.mat_khau or acc.mat_khau.strip() == "":
                acc.mat_khau = "123456"
                
        elif acc.vai_tro == 'student':
            # bắt lỗi trùng số điện thoại
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
        
        if acc.vai_tro == 'student' and acc.id_lop is not None:
            sql_hs = "INSERT INTO hoc_sinh (ten_hoc_sinh, ngay_sinh, so_dien_thoai, email, id_tai_khoan, id_lop) VALUES (%s, %s, %s, %s, %s, %s)"
            cursor.execute(sql_hs, (acc.ten_hoc_sinh, acc.ngay_sinh, acc.so_dien_thoai, acc.email, new_acc_id, acc.id_lop))
            
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
async def update_account(acc_id: int, acc: AccountModel):
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
            
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã cập nhật tài khoản thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    
@router.post("/delete-account/{acc_id}")
async def delete_account(acc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT vai_tro FROM tai_khoan WHERE id = %s", (acc_id,))
        acc = cursor.fetchone()
        if not acc:
            return {"status": "error", "message": "Tài khoản không tồn tại."}

        role = acc['vai_tro']

        if role == 'teacher':
            cursor.execute("SELECT id FROM lop_hoc WHERE id_giao_vien = %s", (acc_id,))
            classes = cursor.fetchall()
            
            for c in classes:
                class_id = c['id']
                cursor.execute("SELECT id_tai_khoan FROM hoc_sinh WHERE id_lop = %s", (class_id,))
                student_acc_ids = [s['id_tai_khoan'] for s in cursor.fetchall()]
                
                cursor.execute("DELETE FROM hoc_sinh WHERE id_lop = %s", (class_id,))
                
                if student_acc_ids:
                    format_strings = ','.join(['%s'] * len(student_acc_ids))
                    cursor.execute(f"DELETE FROM tai_khoan WHERE id IN ({format_strings})", tuple(student_acc_ids))
            
            cursor.execute("DELETE FROM lop_hoc WHERE id_giao_vien = %s", (acc_id,))

        elif role == 'student':
            cursor.execute("DELETE FROM hoc_sinh WHERE id_tai_khoan = %s", (acc_id,))

        cursor.execute("DELETE FROM tai_khoan WHERE id = %s", (acc_id,))

        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa sạch tài khoản và dữ liệu liên quan!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/reset-password/{acc_id}")
async def reset_password(acc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # Mật khẩu mặc định
        default_pass = "123456"
        hashed_password = hash_password(default_pass)
        
        cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_password, acc_id))
        
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã reset mật khẩu thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    

# ==========================================
# API THÊM/SỬA/XÓA LỚP HỌC
# ==========================================
@router.post("/add-class")
async def add_class(cls: ClassModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # Kiểm tra lớp trùng tên của cùng một giáo viên
        cursor.execute("SELECT id FROM lop_hoc WHERE ten_lop = %s AND id_giao_vien = %s", (cls.ten_lop.strip(), cls.id_giao_vien))
        if cursor.fetchone():
            db.close()
            return {"status": "error", "message": "Giáo viên này đã có một lớp học mang tên này rồi!"}
            
        cursor.execute("INSERT INTO lop_hoc (ten_lop, ma_tham_gia, id_giao_vien) VALUES (%s, %s, %s)", 
               (cls.ten_lop, cls.ma_tham_gia, cls.id_giao_vien))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã thêm lớp học thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-class/{class_id}")
async def update_class(class_id: int, cls: ClassModel):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True) 
        
        # kiểm tra trùng trong phạm vi giáo viên
        cursor.execute("SELECT id_giao_vien FROM lop_hoc WHERE id = %s", (class_id,))
        lop_hien_tai = cursor.fetchone()
        if not lop_hien_tai:
            db.close()
            return {"status": "error", "message": "Lớp học không tồn tại!"}
            
        id_giao_vien = lop_hien_tai['id_giao_vien']
        # Kiểm tra lớp trùng tên
        cursor.execute("SELECT id FROM lop_hoc WHERE ten_lop = %s AND id_giao_vien = %s AND id != %s", (cls.ten_lop.strip(), id_giao_vien, class_id))
        if cursor.fetchone():
            db.close()
            return {"status": "error", "message": "Tên lớp này bị trùng với một lớp khác của cùng giáo viên!"}
            
        cursor.execute("UPDATE lop_hoc SET ten_lop = %s, ma_tham_gia = %s WHERE id = %s", 
               (cls.ten_lop, cls.ma_tham_gia, class_id))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã cập nhật lớp học thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-class/{class_id}")
async def delete_class(class_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT id_tai_khoan FROM hoc_sinh WHERE id_lop = %s", (class_id,))
        student_acc_ids = [s['id_tai_khoan'] for s in cursor.fetchall()]

        cursor.execute("DELETE FROM hoc_sinh WHERE id_lop = %s", (class_id,))

        if student_acc_ids:
            format_strings = ','.join(['%s'] * len(student_acc_ids))
            cursor.execute(f"DELETE FROM tai_khoan WHERE id IN ({format_strings})", tuple(student_acc_ids))

        cursor.execute("DELETE FROM lop_hoc WHERE id = %s", (class_id,))

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
        cursor = db.cursor()
        
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
        cursor = db.cursor()
        cursor.execute("DELETE FROM tin_tuc WHERE id = %s", (news_id,))
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
        # --- CẬP NHẬT TÀI KHOẢN 
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
            
        # --- CẬP NHẬT HỌC SINH 
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

# =========================================================================
# API ADMIN: BÓC TÁCH PDF/ẢNH BẰNG AI & PYMUPDF (FIX LỖI KEYERROR)
# =========================================================================
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
            model = genai.GenerativeModel('gemini-3.6-flash')
            with open(saved_filepath, "rb") as f:
                image_parts = [{"mime_type": file.content_type, "data": f.read()}]
            response = await model.generate_content_async([
                image_parts[0], 
                "OCR chính xác 100% nội dung chữ trong ảnh, định dạng HTML cơ bản. Không tự sáng tác thêm."
            ])
            raw_html = response.text.replace("```html", "").replace("```", "").strip()
        else:
            return {"status": "error", "message": "Chỉ hỗ trợ PDF hoặc Hình ảnh."}

        # 4. AI LÀM ĐẸP (POLISH) TRƯỚC KHI TRẢ VỀ
        if raw_html:
            prompt = f"""
            Dưới đây là HTML thô bóc tách từ tài liệu. Hãy định dạng lại thành một bài giảng tiếng Anh siêu đẹp, dễ đọc (Dùng <h3>, <ul>, <li>, in đậm).
            LỆNH TUYỆT ĐỐI BẮT BUỘC: 
            1. GIỮ NGUYÊN 100% các thẻ <img src="..."> và vị trí của chúng. KHÔNG tự tạo link ảnh ảo.
            2. Xóa các ký tự rác, nối các câu bị ngắt dòng sai.
            Chỉ trả về HTML sạch.
            [HTML THÔ]
            {raw_html}
            """
            model = genai.GenerativeModel('gemini-3.6-flash')
            response = await model.generate_content_async(prompt)
            clean_html = response.text.replace("```html", "").replace("```", "").strip()
            return {"status": "success", "data": clean_html, "file_goc": file_goc_url}

        return {"status": "error", "message": "Không có nội dung bóc tách."}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Lỗi Backend: {str(e)}"}

@router.get("/get-all-grammar-docs")
async def get_all_grammar_docs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, tieu_de, loai_tai_lieu, tags, noi_dung, file_goc, ngay_tao FROM tai_lieu_hoc ORDER BY id DESC")
        docs = cursor.fetchall()
        db.close()
        return {"status": "success", "data": docs}
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
        return {"status": "success", "data": doc} if doc else {"status": "error", "message": "Không tìm thấy!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/save-grammar-doc")
async def save_grammar_doc(doc: TaiLieuHocModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        if doc.id:
            sql = "UPDATE tai_lieu_hoc SET tieu_de=%s, loai_tai_lieu=%s, tags=%s, noi_dung=%s, file_goc=%s WHERE id=%s"
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung, doc.file_goc, doc.id))
        else:
            sql = "INSERT INTO tai_lieu_hoc (tieu_de, loai_tai_lieu, tags, noi_dung, file_goc) VALUES (%s, %s, %s, %s, %s)"
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung, doc.file_goc))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã lưu tài liệu thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-grammar-doc/{doc_id}")
async def delete_grammar_doc(doc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM tai_lieu_hoc WHERE id = %s", (doc_id,))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa tài liệu!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
# =========================================================================
# API ADMIN: QUẢN LÝ TÀI LIỆU HỌC (CRUD DB)
# =========================================================================

@router.get("/get-all-grammar-docs")
async def get_all_grammar_docs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # BỔ SUNG THÊM 'noi_dung' VÀO CÂU LỆNH SELECT Ở ĐÂY 👇
        cursor.execute("SELECT id, tieu_de, loai_tai_lieu, tags, noi_dung, ngay_tao FROM tai_lieu_hoc ORDER BY id DESC")
        
        docs = cursor.fetchall()
        db.close()
        return {"status": "success", "data": docs}
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
        return {"status": "error", "message": "Không tìm thấy tài liệu!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/save-grammar-doc")
async def save_grammar_doc(doc: TaiLieuHocModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        if doc.id:
            sql = """UPDATE tai_lieu_hoc 
                     SET tieu_de=%s, loai_tai_lieu=%s, tags=%s, noi_dung=%s 
                     WHERE id=%s"""
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung, doc.id))
            msg = "Đã cập nhật tài liệu thành công!"
        else:
            sql = """INSERT INTO tai_lieu_hoc (tieu_de, loai_tai_lieu, tags, noi_dung) 
                     VALUES (%s, %s, %s, %s)"""
            cursor.execute(sql, (doc.tieu_de, doc.loai_tai_lieu, doc.tags, doc.noi_dung))
            msg = "Đã tạo tài liệu mới thành công!"
        db.commit()
        db.close()
        return {"status": "success", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-grammar-doc/{doc_id}")
async def delete_grammar_doc(doc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM tai_lieu_hoc WHERE id = %s", (doc_id,))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa tài liệu khỏi hệ thống!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =========================================================================
# API ADMIN: Dashboard ADMIN   
# =========================================================================
@router.get("/dashboard-stats")
async def get_admin_dashboard_stats():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # 4 Chỉ số đếm (Scorecards)
        cursor.execute("SELECT COUNT(*) as total FROM tai_khoan WHERE vai_tro = 'student'")
        tong_hoc_sinh = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM tai_khoan WHERE vai_tro = 'teacher'")
        tong_giao_vien = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM tai_lieu_hoc")
        tong_tai_lieu = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM lich_su_lam_bai")
        tong_luot_lam_bai = cursor.fetchone()['total']

        # DỮ LIỆU BIỂU ĐỒ TRÒN (PIE CHART) - Tỉ lệ tài liệu
        cursor.execute("SELECT loai_tai_lieu, COUNT(*) as count FROM tai_lieu_hoc GROUP BY loai_tai_lieu")
        doc_types_raw = cursor.fetchall()
        doc_types = {'general': 0, 'toeic_prep': 0}
        for d in doc_types_raw:
            if d['loai_tai_lieu'] == 'general': doc_types['general'] = d['count']
            elif d['loai_tai_lieu'] == 'toeic_prep': doc_types['toeic_prep'] = d['count']

        # DỮ LIỆU BIỂU ĐỒ CỘT (BAR CHART) - Hoạt động 7 ngày qua
        today = datetime.date.today()
        # Tạo sẵn mảng 7 ngày để tránh bị khuyết ngày không có ai nộp bài
        last_7_days = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
        weekly_activity = {day: 0 for day in last_7_days}

        cursor.execute("""
            SELECT DATE(thoi_gian_nop) as date_nop, COUNT(*) as count 
            FROM lich_su_lam_bai 
            WHERE thoi_gian_nop >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
            GROUP BY DATE(thoi_gian_nop)
        """)
        activity_raw = cursor.fetchall()
        for a in activity_raw:
            day_str = a['date_nop'].strftime('%Y-%m-%d')
            if day_str in weekly_activity:
                weekly_activity[day_str] = a['count']
        
        db.close()
        return {
            "status": "success", 
            "data": {
                "tong_hoc_sinh": tong_hoc_sinh,
                "tong_giao_vien": tong_giao_vien,
                "tong_tai_lieu": tong_tai_lieu,
                "tong_luot_lam_bai": tong_luot_lam_bai,
                "doc_types": doc_types,
                "weekly_activity": weekly_activity
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

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

# =============================================================
# API ADMIN: LẤY NHẬT KÝ HOẠT ĐỘNG TOÀN HỆ THỐNG (TIMELINE)
# =============================================================
@router.get("/system-activity-logs")
async def get_system_activity_logs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        sql = """
            -- 1. Học sinh nộp bài
            SELECT 'hoc_sinh' as role, h.ten_hoc_sinh as name, 
                   CONCAT('Đã nộp bài tập ', UPPER(l.part), ' (Đạt ', ROUND((l.diem_so/l.tong_so_cau)*100), '%)') as action, 
                   l.thoi_gian_nop as time
            FROM lich_su_lam_bai l
            JOIN hoc_sinh h ON l.id_hoc_sinh = h.id_tai_khoan
            
            UNION ALL
            
            -- 2. Học sinh xem học liệu
            SELECT 'hoc_sinh' as role, h.ten_hoc_sinh as name, 
                   CONCAT('Vừa xem học liệu: ', t.tieu_de) as action, 
                   x.thoi_gian_xem as time
            FROM lich_su_xem_tai_lieu x
            JOIN hoc_sinh h ON x.id_hoc_sinh = h.id_tai_khoan
            JOIN tai_lieu_hoc t ON x.id_tai_lieu = t.id
            
            UNION ALL
            
            -- 3. Giáo viên/Admin thêm/sửa tài liệu (Từ bảng chung)
            SELECT 'giao_vien' as role, 'Giáo viên / Admin' as name, 
                   CONCAT('Đã tác động tài liệu: ', tieu_de) as action, 
                   ngay_tao as time
            FROM tai_lieu_hoc

            UNION ALL

            -- 4. ĐÃ FIX LỖI: HIỂN THỊ ID GIÁO VIÊN THAY VÌ JOIN BẢNG LỖI
            SELECT 'giao_vien' as role, CONCAT('Giáo viên (ID: ', gv_log.id_giao_vien, ')') as name,
                   CONCAT(gv_log.hanh_dong, ' - ', IFNULL(gv_log.chi_tiet, '')) as action,
                   gv_log.thoi_gian as time
            FROM lich_su_hoat_dong_gv gv_log
            
            ORDER BY time DESC LIMIT 80
        """
        cursor.execute(sql)
        logs = cursor.fetchall()
        db.close()
        
        return {"status": "success", "data": logs}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

# =============================================================
# API ADMIN: LAY DỮ LIỆU HOẠT ĐỘNG CHO BIỂU ĐỒ CỘT
# =============================================================
@router.get("/activity-chart")
async def get_activity_chart(time_filter: str = '7_days'):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        labels = []
        data_points = []

        if time_filter == '7_days':
            today = datetime.date.today()
            dates = [(today - datetime.timedelta(days=i)) for i in range(6, -1, -1)]
            for d in dates:
                labels.append(d.strftime('%d/%m'))
                cursor.execute("SELECT COUNT(*) as cnt FROM lich_su_lam_bai WHERE DATE(thoi_gian_nop) = %s", (d,))
                data_points.append(cursor.fetchone()['cnt'])
                
        elif time_filter == 'this_month':
            today = datetime.date.today()
            start_date = today.replace(day=1)
            num_days = (today - start_date).days + 1
            dates = [(start_date + datetime.timedelta(days=i)) for i in range(num_days)]
            for d in dates:
                labels.append(d.strftime('%d/%m'))
                cursor.execute("SELECT COUNT(*) as cnt FROM lich_su_lam_bai WHERE DATE(thoi_gian_nop) = %s", (d,))
                data_points.append(cursor.fetchone()['cnt'])

        elif time_filter == 'all_time':
            # Gom nhóm theo từng tháng
            cursor.execute("""
                SELECT DATE_FORMAT(thoi_gian_nop, '%m/%Y') as month_str, COUNT(*) as cnt 
                FROM lich_su_lam_bai 
                GROUP BY DATE_FORMAT(thoi_gian_nop, '%m/%Y'), YEAR(thoi_gian_nop), MONTH(thoi_gian_nop)
                ORDER BY YEAR(thoi_gian_nop) ASC, MONTH(thoi_gian_nop) ASC
            """)
            rows = cursor.fetchall()
            for r in rows:
                labels.append(r['month_str'])
                data_points.append(r['cnt'])

        db.close()
        return {"status": "success", "labels": labels, "data": data_points}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =========================================================================
# API ADMIN: LẤY TOÀN BỘ LỊCH SỬ HỌC SINH XEM TÀI LIỆU
# =========================================================================
@router.get("/all-doc-view-history")
async def get_all_doc_view_history():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT h.ten_hoc_sinh, t.tieu_de, x.thoi_gian_xem
            FROM lich_su_xem_tai_lieu x
            JOIN hoc_sinh h ON x.id_hoc_sinh = h.id_tai_khoan
            JOIN tai_lieu_hoc t ON x.id_tai_lieu = t.id
            ORDER BY x.thoi_gian_xem DESC LIMIT 200
        """
        cursor.execute(sql)
        history = cursor.fetchall()
        db.close()
        return {"status": "success", "data": history}
    except Exception as e:
        return {"status": "error", "message": str(e)}