from fastapi import APIRouter, Query, HTTPException
from database import get_db_connection
from schemas import HistoryData, IIGSubmitData, StudentLogRequest
from pydantic import BaseModel
import json
import re
import os
import random
import google.generativeai as genai

class LogViewRequest(BaseModel):
    id_hoc_sinh: int
    id_tai_lieu: int

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
router = APIRouter()

def write_system_log(cursor, id_tai_khoan, action_desc):
    """
    Hàm tiện ích tự động tra cứu thông tin tài khoản và ghi log
    Lưu ý: Bạn phải gọi db.commit() sau khi dùng hàm này ở route chính.
    """
    try:
        # 1. Tìm thông tin người dùng từ DB
        cursor.execute("SELECT ten_dang_nhap, vai_tro FROM tai_khoan WHERE id = %s", (id_tai_khoan,))
        user = cursor.fetchone()
        
        if user:
            # Tương thích với cả cursor tuple và dictionary
            ten = user['ten_dang_nhap'] if isinstance(user, dict) else user[0]
            vai_tro = user['vai_tro'] if isinstance(user, dict) else user[1]
            
            # 2. Ghi vào bảng nhật ký
            sql_log = "INSERT INTO lich_su_xem_tai_lieu (id_tai_khoan, ten_tai_khoan, vai_tro, hanh_dong) VALUES (%s, %s, %s, %s)"
            cursor.execute(sql_log, (id_tai_khoan, ten, vai_tro, action_desc))
    except Exception as e:
        print("LỖI GHI LOG:", e)

#=============================================================
#===      HỌC SINH NHẬN BÀI TẬP ĐƯỢC GIAO     ================
#=============================================================
# 1. API Lấy danh sách bài tập dựa theo ID Lớp của Học sinh
@router.get("/get-homeworks/{id_hoc_sinh}/{id_lop}")
async def get_student_homeworks(id_hoc_sinh: int, id_lop: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # giao bài chung hoặc giao riêng cho học sinh
        sql = """
            SELECT b.*, 
                   (SELECT COUNT(*) FROM lich_su_lam_bai l 
                    WHERE l.id_bai_tap = b.id AND l.id_hoc_sinh = %s) as da_lam
            FROM bai_tap_giao b
            WHERE b.id_lop = %s AND (b.id_hoc_sinh IS NULL OR b.id_hoc_sinh = 0 OR b.id_hoc_sinh = %s)
            ORDER BY b.han_nop ASC
        """
        
        cursor.execute(sql, (id_hoc_sinh, id_lop, id_hoc_sinh))
        homeworks = cursor.fetchall()
        
        conn.close()
        return {"status": "success", "data": homeworks}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/get-homework-detail/{id_bai_tap}")
async def get_homework_detail(id_bai_tap: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM bai_tap_giao WHERE id = %s", (id_bai_tap,))
        hw = cursor.fetchone()
        
        if not hw:
            return {"status": "error", "message": "Không tìm thấy bài tập!"}
            
        ids = json.loads(hw['danh_sach_cau_hoi'])
        
        if not ids:
            return {"status": "success", "homework": hw, "questions": []}
            
        format_strings = ','.join(['%s'] * len(ids))
        
        cursor.execute(f"SELECT * FROM ngan_hang_cau_hoi WHERE id IN ({format_strings})", tuple(ids))
        questions = cursor.fetchall()
        
        conn.close()
        return {"status": "success", "homework": hw, "questions": questions}
    except Exception as e:
        return {"status": "error", "message": str(e)}


#====LƯU LỊCH SỬ LÀM BAI CỦA HỌC SINH====
@router.post("/save-history")
async def save_history(data: HistoryData):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        chi_tiet = json.loads(data.chi_tiet_bai_lam)
        
        part_stats = {}

        for item in chi_tiet:
            cursor.execute("SELECT part FROM ngan_hang_cau_hoi WHERE id = %s", (item['id_cau_hoi'],))
            q_info = cursor.fetchone()
            
            raw_part = q_info['part'] if q_info else data.part
            actual_part = str(raw_part).strip().lower()

            if actual_part not in part_stats:
                part_stats[actual_part] = {'diem': 0, 'tong': 0, 'chi_tiet': []}
            
            part_stats[actual_part]['tong'] += 1
            if item.get('is_correct'):
                part_stats[actual_part]['diem'] += 1
            part_stats[actual_part]['chi_tiet'].append(item)

        sql_insert = "INSERT INTO lich_su_lam_bai (id_hoc_sinh, part, diem_so, tong_so_cau, chi_tiet_bai_lam, id_bai_tap) VALUES (%s, %s, %s, %s, %s, %s)"
        parts_logged = []
        for p, stats in part_stats.items():
            if stats['tong'] > 0:
                chi_tiet_json = json.dumps(stats['chi_tiet'])
                cursor.execute(sql_insert, (data.id_hoc_sinh, p, stats['diem'], stats['tong'], chi_tiet_json, data.id_bai_tap))

        # --- LƯU NHẬT KÝ
        if parts_logged:
            chuoi_parts = ", ".join(parts_logged)

            if data.id_bai_tap: 
                loai_hd = 'LAM_BAI_TAP'
                chi_tiet_log = f"Đã hoàn thành bài tập được giao (Part {chuoi_parts})"
            else: 
                loai_hd = 'ON_LUYEN_TU_DO'
                chi_tiet_log = f"Đã hoàn thành lượt tự ôn luyện (Part {chuoi_parts})"

            cursor.execute(
                "INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'student', %s, %s)", 
                (data.id_hoc_sinh, loai_hd, chi_tiet_log)
            )        
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
        
    except Exception as e:
        print("Lỗi lưu lịch sử:", e)
        return {"status": "error", "message": str(e)}


#====  LẤY RA LỊCH SỬ LÀM BÀI  ====
@router.get("/get-history/{part}/{id_hoc_sinh}")
async def get_history(part: str, id_hoc_sinh: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True) 
        
        sql = """
            SELECT id, diem_so, tong_so_cau, thoi_gian_nop, chi_tiet_bai_lam 
            FROM lich_su_lam_bai 
            WHERE part = %s AND id_hoc_sinh = %s 
            ORDER BY thoi_gian_nop DESC
        """
        cursor.execute(sql, (part, id_hoc_sinh))
        records = cursor.fetchall()
        
        # Chuyển đổi chuỗi JSON trong CSDL thành mảng Python trước khi trả về
        for row in records:
            if row['chi_tiet_bai_lam']:
                row['chi_tiet_bai_lam'] = json.loads(row['chi_tiet_bai_lam'])
                
        conn.close()
        return {"status": "success", "data": records}
    except Exception as e:
        return {"status": "error", "message": str(e)}


#=============================================================
#===           HỌC SINH THỐNG KÊ PHÂN TÍCH          ==========
#=============================================================
@router.get("/get-statistics/{id_hoc_sinh}")
async def get_statistics(id_hoc_sinh: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Thống kê theo từng Part (Gom nhóm để xem tỷ lệ đúng/sai)
        sql_part = """
            SELECT part, 
                   SUM(diem_so) as total_correct, 
                   SUM(tong_so_cau) as total_questions 
            FROM lich_su_lam_bai 
            WHERE id_hoc_sinh = %s 
            GROUP BY part
            ORDER BY part ASC
        """
        cursor.execute(sql_part, (id_hoc_sinh,))
        part_stats = cursor.fetchall()

        # 2. Thống kê tổng quan toàn bộ quá trình học
        sql_total = """
            SELECT COUNT(*) as total_tests, 
                   SUM(diem_so) as total_correct, 
                   SUM(tong_so_cau) as total_questions
            FROM lich_su_lam_bai
            WHERE id_hoc_sinh = %s
        """
        cursor.execute(sql_total, (id_hoc_sinh,))
        total_stats = cursor.fetchone()

        conn.close()
        return {
            "status": "success", 
            "part_stats": part_stats, 
            "total_stats": total_stats
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==============================
# HỌC SINH LẤY CÂU HỎI ÔN LUYỆN
# ==============================
@router.get("/get-practice-questions/{part_name}/{id_hoc_sinh}")
async def get_student_practice_questions(part_name: str, id_hoc_sinh: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # ĐÃ SỬA: Lấy id_giao_vien thông qua bảng chi_tiet_lop_hoc        
        cursor.execute("""
            SELECT l.id_giao_vien 
            FROM chi_tiet_lop_hoc c 
            JOIN lop_hoc l ON c.id_lop = l.id 
            WHERE c.id_hoc_sinh = %s        
        """, (id_hoc_sinh,))
        gv_info = cursor.fetchone()
        
        if not gv_info:
            db.close()
            return {"status": "success", "data": []} 
            
        id_giao_vien = gv_info['id_giao_vien']
        
        sql = "SELECT * FROM ngan_hang_cau_hoi WHERE part = %s AND id_giao_vien = %s ORDER BY id DESC"
        cursor.execute(sql, (part_name, id_giao_vien))
        questions = cursor.fetchall()
        
        cursor.close()
        db.close()
        
        return {"status": "success", "data": questions}
    except Exception as e:
        print(f"Lỗi lấy dữ liệu ôn luyện của học sinh: {e}")
        return {"status": "error", "message": "Không thể kết nối Database."}

# ==========================================
# HỌC SINH LẤY ĐỀ THI IIG (BẢO MẬT: KHÔNG TRẢ VỀ ĐÁP ÁN)
# ==========================================
@router.get("/get-iig-test/{id_de_thi}")
async def get_student_iig_test(id_de_thi: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        sql = "SELECT id, ten_de, file_pdf, file_audio FROM de_thi_iig WHERE id = %s"
        cursor.execute(sql, (id_de_thi,))
        test = cursor.fetchone()
        
        cursor.close()
        db.close()
        
        if not test:
            return {"status": "error", "message": "Không tìm thấy đề thi."}
            
        return {"status": "success", "data": test}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/submit-iig-test")
async def submit_iig_test(data: IIGSubmitData):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT ten_de, dap_an_json FROM de_thi_iig WHERE id = %s", (data.id_de_thi,))
        de_thi = cursor.fetchone()
        
        if not de_thi:
            return {"status": "error", "message": "Không tìm thấy đề thi gốc để chấm điểm."}
            
        dap_an_chuan = json.loads(de_thi['dap_an_json'])
        dap_an_hoc_sinh = data.dap_an_hoc_sinh
        
        so_cau_nghe = 0
        so_cau_doc = 0
        chi_tiet_bai_lam = []
        
        for i in range(200):
            cau_so = i + 1
            chon = dap_an_hoc_sinh[i] if i < len(dap_an_hoc_sinh) else ""
            chuan = dap_an_chuan[i] if i < len(dap_an_chuan) else ""
            is_correct = (chon == chuan and chon != "")
            
            if is_correct:
                if i < 100: so_cau_nghe += 1
                else: so_cau_doc += 1
                
            chi_tiet_bai_lam.append({
                "cau_so": cau_so,
                "da_chon": chon,
                "dap_an_dung": chuan,
                "is_correct": is_correct
            })
            
        if so_cau_nghe == 0: diem_nghe = 5
        else:
            diem_nghe = min(so_cau_nghe * 5, 495)
            if diem_nghe < 5: diem_nghe = 5 
            
        if so_cau_doc == 0: diem_doc = 5
        else:
            diem_doc = min(so_cau_doc * 5, 495)
            if diem_doc < 5: diem_doc = 5 
            
        tong_diem = diem_nghe + diem_doc
        
        sql = """INSERT INTO ket_qua_thi_iig 
                 (id_hoc_sinh, id_de_thi, so_cau_nghe_dung, so_cau_doc_dung, diem_nghe, diem_doc, tong_diem, chi_tiet_json)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
        
        cursor.execute(sql, (data.id_hoc_sinh, data.id_de_thi, so_cau_nghe, so_cau_doc, diem_nghe, diem_doc, tong_diem, json.dumps(chi_tiet_bai_lam)))
        id_ket_qua = cursor.lastrowid
        # --- LƯU NHẬT KÝ HỌC SINH ---
        chi_tiet_log = f"Đã hoàn thành thi thử đề '{de_thi['ten_de']}' (Đạt: {tong_diem} điểm)"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'student', 'LAM_BAI_THI', %s)", (data.id_hoc_sinh, chi_tiet_log))
        db.commit()
        cursor.close()
        db.close()
        
        return {
            "status": "success", 
            "message": "Nộp bài thành công!",
            "id_ket_qua": id_ket_qua, 
            "diem_so": {"nghe": diem_nghe, "doc": diem_doc, "tong": tong_diem}
        }
        
    except Exception as e:
        print("Lỗi chấm IIG:", e)
        return {"status": "error", "message": str(e)}
    


# ==========================================
# LẤY CHI TIẾT KẾT QUẢ THI IIG ĐỂ XEM LẠI
# ==========================================
@router.get("/get-iig-result/{id_ket_qua}")
async def get_iig_result(id_ket_qua: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        sql = """
            SELECT k.*, d.ten_de, d.file_pdf, d.file_audio 
            FROM ket_qua_thi_iig k
            JOIN de_thi_iig d ON k.id_de_thi = d.id
            WHERE k.id = %s
        """
        cursor.execute(sql, (id_ket_qua,))
        result = cursor.fetchone()
        
        db.close()
        
        if not result:
            return {"status": "error", "message": "Không tìm thấy dữ liệu bài làm."}
            
        result['chi_tiet_json'] = json.loads(result['chi_tiet_json'])
        
        return {"status": "success", "data": result}
        
    except Exception as e:
        print("Lỗi lấy kết quả IIG:", e)
        return {"status": "error", "message": str(e)}
    
    
# ==========================================
# HỌC SINH LẤY KHO ĐỀ KÈM TRẠNG THÁI ĐÃ LÀM
# ==========================================
@router.get("/get-iig-tests/{id_hoc_sinh}")
async def get_student_iig_tests(id_hoc_sinh: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        sql = """
            SELECT d.id, d.ten_de, d.nam_phat_hanh, d.ngay_tao,
                   k.id as id_ket_qua, k.tong_diem, k.ngay_thi
            FROM de_thi_iig d
            LEFT JOIN (
                SELECT id_de_thi, MAX(id) as id, tong_diem, MAX(ngay_thi) as ngay_thi 
                FROM ket_qua_thi_iig 
                WHERE id_hoc_sinh = %s 
                GROUP BY id_de_thi, tong_diem
            ) k ON d.id = k.id_de_thi
            ORDER BY d.ngay_tao DESC
        """
        cursor.execute(sql, (id_hoc_sinh,))
        tests = cursor.fetchall()
        
        cursor.close()
        db.close()
        return {"status": "success", "data": tests}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    

# ==========================================
# API HỌC SINH: LẤY THÔNG BÁO CỦA LỚP MÌNH
# ==========================================
@router.get("/get-class-notices/{acc_id}")
async def get_student_class_notices(acc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id_lop FROM chi_tiet_lop_hoc WHERE id_hoc_sinh = %s", (acc_id,))        
        hs = cursor.fetchone()
        
        if not hs or not hs['id_lop']:
            return {"status": "success", "data": []} 
            
        cursor.execute("SELECT * FROM thong_bao_lop WHERE id_lop = %s ORDER BY ngay_tao DESC", (hs['id_lop'],))
        notices = cursor.fetchall()
        
        db.close()
        return {"status": "success", "data": notices}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    


# ==========================================
# API HỌC SINH: LẤY DANH SÁCH CHỦ ĐỀ TỪ VỰNG CỦA GIÁO VIÊN MÌNH
# ==========================================
@router.get("/get-vocab-topics/{acc_id}")
async def get_student_vocab_topics(acc_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # ĐÃ SỬA: Nối qua bảng chi_tiet_lop_hoc để lấy id_giao_vien của lớp
        sql_get_gv = """
            SELECT l.id_giao_vien 
            FROM chi_tiet_lop_hoc c 
            JOIN lop_hoc l ON c.id_lop = l.id 
            WHERE c.id_hoc_sinh = %s
        """
        cursor.execute(sql_get_gv, (acc_id,))
        gv_info = cursor.fetchone()
        
        if not gv_info:
            return {"status": "success", "data": []}
            
        id_giao_vien = gv_info['id_giao_vien']
        
        sql_topics = """
            SELECT c.id, c.chu_de, COUNT(t.id) as so_tu 
            FROM chu_de_tu_vung c
            LEFT JOIN tu_vung t ON c.id = t.id_chu_de
            WHERE c.id_giao_vien = %s
            GROUP BY c.id, c.chu_de
            ORDER BY c.id DESC
        """
        cursor.execute(sql_topics, (id_giao_vien,))
        topics = cursor.fetchall()
        
        db.close()
        return {"status": "success", "data": topics}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# API HỌC SINH: LẤY DANH SÁCH TỪ VỰNG TRONG 1 CHỦ ĐỀ
# ==========================================
@router.get("/get-vocab-list/{id_chu_de}")
async def get_student_vocab_list(id_chu_de: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT id, tu, phien_am, nghia, vi_du FROM tu_vung WHERE id_chu_de = %s ORDER BY id ASC", (id_chu_de,))
        vocab_list = cursor.fetchall()
        
        db.close()
        return {"status": "success", "data": vocab_list}
    except Exception as e:
        return {"status": "error", "message": str(e)}



@router.get("/generate-quiz")
async def generate_quiz(topic_id: int, limit: int = Query(10, le=20)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        sql_query = "SELECT tu, nghia FROM tu_vung WHERE id_chu_de = %s"
        cursor.execute(sql_query, (topic_id,))
        vocab_list = cursor.fetchall()
        
        cursor.close()
        conn.close()

        if not vocab_list or len(vocab_list) < limit:
            return {
                "status": "error", 
                "message": f"Chủ đề này có quá ít từ vựng. Cần ít nhất {limit} từ để Nova không bị lặp câu hỏi!"
            }

        vocab_context = "\n".join([f"- {v['tu']}: {v['nghia']}" for v in vocab_list])

        prompt = f"""
        Bạn là Nova, một hệ thống AI tạo đề thi trắc nghiệm TOEIC.
        Tôi có danh sách từ vựng sau:
        {vocab_context}

        Nhiệm vụ: Tạo {limit} câu hỏi trắc nghiệm tiếng Anh.
        
        Các dạng câu hỏi (sẽ đưa vào trường "type"):
        1. "Chọn nghĩa đúng của từ sau:"
        2. "Từ đồng nghĩa với từ sau là gì?"
        3. "Từ trái nghĩa với từ sau là gì?"
        4. "Đâu KHÔNG PHẢI là nghĩa của từ sau?"

        LƯU Ý SINH TỬ VỀ LOGIC & NGÔN NGỮ: 
        - KHÔNG TRÙNG LẶP: Mỗi từ vựng CHỈ ĐƯỢC HỎI TỐI ĐA 1 LẦN. Không tạo 2 câu hỏi cho cùng 1 từ.
        - TUYỆT ĐỐI KHÔNG sử dụng dạng câu "Từ trái nghĩa" cho các Danh từ chỉ người (father, mother, uncle,...), quan hệ gia đình, hoặc đồ vật cụ thể. Dạng "Từ trái nghĩa" CHỈ được dùng cho Tính từ hoặc Động từ!
        - TRƯỜNG "question": CHỈ ĐƯỢC ĐIỀN ĐÚNG TỪ VỰNG TIẾNG ANH CẦN HỎI (Ví dụ: "Daughter"). TUYỆT ĐỐI KHÔNG tự ý chế thêm câu hỏi bằng tiếng Anh hay tiếng Việt nào khác vào trường này.
        
        YÊU CẦU BẮT BUỘC:
        - 4 đáp án (1 đúng, 3 sai). Trả về JSON ARRAY bắt đầu bằng [ và kết thúc bằng ].
        - Cấu trúc ví dụ CHUẨN: {{"type": "Chọn nghĩa đúng của từ sau:", "question": "Daughter", "options": ["Con gái", "Con trai", "Bố", "Mẹ"], "answerIndex": 0}}
        """

        model = genai.GenerativeModel('gemini-2.5-flash')
        # ĐÃ SỬA: Đổi sang gọi AI bất đồng bộ (async) để server không bị nghẽn
        response = await model.generate_content_async(prompt) 
        
        raw_text = response.text.strip()
        
        raw_text = re.sub(r"^```(json)?\s*", "", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        quiz_data = json.loads(raw_text)
        for q in quiz_data:
            correct_answer_text = q["options"][q["answerIndex"]]
            
            random.shuffle(q["options"])
            
            q["answerIndex"] = q["options"].index(correct_answer_text)

        return {"status": "success", "data": quiz_data}

    except json.JSONDecodeError as json_err:
        print(f"[NOVA JSON ERROR] {json_err} \nRaw text: {raw_text}")
        return {
            "status": "error", 
            "message": "Nova gặp vấn đề khi thiết lập ma trận đáp án. Vui lòng thử lại."
        }
        
    except Exception as e:
        print(f"[SYSTEM ERROR] Lỗi tạo quiz AI: {e}")
        return {
            "status": "error", 
            "message": "Đã xảy ra lỗi nghiêm trọng trong quá trình tạo đề."
        }
    

@router.get("/generate-matching")
async def generate_matching(topic_id: int, limit: int = Query(5, le=15)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT tu, nghia FROM tu_vung WHERE id_chu_de = %s", (topic_id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            raise HTTPException(status_code=404, detail="Không tìm thấy từ vựng cho chủ đề này")

        vocab_list = [f"{row['tu']} ({row['nghia']})" for row in rows]
        vocab_context = ", ".join(vocab_list)

        prompt = f"""
        Bạn là Nova, hệ thống AI tạo bài tập TOEIC.
        Dữ liệu:
        {vocab_context}

        Tạo {limit} cặp từ vựng ngẫu nhiên từ dữ liệu trên.
        KHÔNG giải thích, KHÔNG định dạng markdown thừa.
        Định dạng JSON ARRAY BẮT BUỘC:
        [
            {{"word": "Từ tiếng Anh 1", "meaning": "Nghĩa tiếng Việt 1"}},
            {{"word": "Từ tiếng Anh 2", "meaning": "Nghĩa tiếng Việt 2"}}
        ]
        """

        model = genai.GenerativeModel('gemini-2.5-flash')
        # ĐÃ SỬA: Đổi sang gọi AI bất đồng bộ
        response = await model.generate_content_async(prompt)        
        cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned_text)

        return {"status": "success", "data": data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
# =============================================================
# API LẤY STATS CHO DASHBOARD HỌC SINH
# =============================================================
@router.get("/dashboard-stats/{id_hoc_sinh}")
async def get_student_dashboard_stats(id_hoc_sinh: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as total FROM bai_tap_giao WHERE id_lop IN (SELECT id_lop FROM hoc_sinh WHERE id = %s)", (id_hoc_sinh,))
        tong_bai_duoc_giao = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM lich_su_lam_bai WHERE id_hoc_sinh = %s", (id_hoc_sinh,))
        tong_bai_da_nop = cursor.fetchone()['total']
        
        cursor.execute("SELECT AVG(diem_so) as avg_score FROM lich_su_lam_bai WHERE id_hoc_sinh = %s", (id_hoc_sinh,))
        diem_trung_binh = cursor.fetchone()['avg_score'] or 0
        
        cursor.execute("""
            SELECT SUM(diem_so) as total_score, SUM(tong_so_cau) as total_questions
            FROM lich_su_lam_bai 
            WHERE id_hoc_sinh = %s
        """, (id_hoc_sinh,))
        diem_tong = cursor.fetchone()
        tong_diem = diem_tong['total_score'] or 0
        tong_cau_tra_loi = diem_tong['total_questions'] or 0
        
        db.close()
        return {
            "status": "success", 
            "data": {
                "tong_bai_duoc_giao": tong_bai_duoc_giao,
                "tong_bai_da_nop": tong_bai_da_nop,
                "diem_trung_binh": round(diem_trung_binh, 2),
                "tong_diem": tong_diem,
                "tong_cau_tra_loi": tong_cau_tra_loi
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# API HỌC SINH LẤY DANH SÁCH TÀI LIỆU (READ-ONLY)
@router.get("/get-all-grammar-docs")
async def get_all_grammar_docs():
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # Nối bảng để lấy thông tin người biên soạn
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
        
@router.post("/log-doc-view")
async def log_student_view(req: dict): # Hoặc req: StudentLogRequest tùy bạn đang dùng
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        id_hs = req.get('id_hoc_sinh')
        id_tl = req.get('id_tai_lieu')
        trang_thai = req.get('trang_thai')

        # Lấy tên tài liệu để ghép vào chuỗi hành động
        cursor.execute("SELECT tieu_de FROM tai_lieu_hoc WHERE id = %s", (id_tl,))
        doc = cursor.fetchone()
        tieu_de = doc['tieu_de'] if doc else f"Tài liệu #{id_tl}"

        # Lấy tên học sinh
        cursor.execute("SELECT ten_dang_nhap FROM tai_khoan WHERE id = %s", (id_hs,))
        user = cursor.fetchone()
        ten_tk = user['ten_dang_nhap'] if user else "Học sinh"

        hanh_dong = f"Đã hoàn thành bài học: {tieu_de}" if trang_thai == "completed" else f"Đang học: {tieu_de}"

        # Ghi log chuẩn 5 cột
        sql_log = "INSERT INTO lich_su_xem_tai_lieu (id_tai_khoan, ten_tai_khoan, hanh_dong) VALUES (%s, %s, %s)"
        cursor.execute(sql_log, (id_hs, ten_tk, hanh_dong))
        
        db.commit()
        db.close()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}