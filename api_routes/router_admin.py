from fastapi import APIRouter, UploadFile, File, Form
from database import get_db_connection
import uuid
import shutil
import re
import random
import os
from datetime import datetime
from schemas import UpdateAnswersModel, AccountModel, ClassModel, UpdateProfileModel
import bcrypt  # ĐÃ THAY HASHLIB BẰNG BCRYPT
from typing import Optional

router = APIRouter()

# Hàm hỗ trợ băm mật khẩu dùng chung cho toàn file
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
        cursor.execute(sql, (ten_de, nam_phat_hanh, f"http://127.0.0.1:8000/{pdf_path}", 
                             f"http://127.0.0.1:8000/{audio_path}" if audio_path else None, dap_an_json))
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
        cursor.execute("""
            SELECT id, ten_dang_nhap, email 
            FROM tai_khoan WHERE vai_tro = 'teacher'
        """)
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
        cursor.execute("SELECT id, ten_lop, ma_tham_gia FROM lop_hoc WHERE id_giao_vien = %s", (teacher_id,))
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
        
        if acc.vai_tro == 'student':
            acc.ten_dang_nhap = tao_ten_dang_nhap(cursor, acc.ten_hoc_sinh, acc.ngay_sinh)
            if not acc.mat_khau or acc.mat_khau.strip() == "":
                acc.mat_khau = str(random.randint(100000, 999999))
        else:
            if not acc.mat_khau or acc.mat_khau.strip() == "":
                acc.mat_khau = "123456"
                
        # NÂNG CẤP BCRYPT
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
        
        cursor.execute("UPDATE tai_khoan SET email = %s WHERE id = %s", (acc.email, acc_id))
        
        if acc.mat_khau and acc.mat_khau.strip() != "":
            # NÂNG CẤP BCRYPT
            hashed_password = hash_password(acc.mat_khau)
            cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_password, acc_id))
        
        if acc.vai_tro == 'teacher':
            cursor.execute("UPDATE tai_khoan SET ten_dang_nhap = %s WHERE id = %s", (acc.ten_dang_nhap, acc_id))
        elif acc.vai_tro == 'student':
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
        # NÂNG CẤP BCRYPT
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
        cursor = db.cursor()
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
            
        db_img_path = f"http://127.0.0.1:8000/{file_path.replace(os.sep, '/')}"

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
                
            db_img_path = f"http://127.0.0.1:8000/{file_path.replace(os.sep, '/')}"
            
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

# ĐÃ XÓA HÀM BỊ TRÙNG LẶP, CHỈ GIỮ LẠI HÀM HOÀN CHỈNH NÀY
@router.post("/update-profile/{acc_id}")
async def user_update_profile(acc_id: int, req: UpdateProfileModel):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # --- CẬP NHẬT TÀI KHOẢN (Email & Pass) ---
        cursor.execute("SELECT mat_khau FROM tai_khoan WHERE id = %s", (acc_id,))
        user = cursor.fetchone()
        if not user:
            return {"status": "error", "message": "Tài khoản không tồn tại!"}

        if req.email and req.email.strip() != "":
            cursor.execute("UPDATE tai_khoan SET email = %s WHERE id = %s", (req.email, acc_id))
            
        if req.mat_khau_moi and req.mat_khau_moi.strip() != "":
            # NÂNG CẤP BCRYPT CHO ĐỔI MẬT KHẨU
            if not bcrypt.checkpw(req.mat_khau_cu.encode('utf-8'), user['mat_khau'].encode('utf-8')):
                return {"status": "error", "message": "Mật khẩu hiện tại không chính xác!"}
                
            hashed_new = hash_password(req.mat_khau_moi)
            cursor.execute("UPDATE tai_khoan SET mat_khau = %s WHERE id = %s", (hashed_new, acc_id))
            
        # --- CẬP NHẬT HỌC SINH (Giữ giá trị cũ nếu rỗng) ---
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