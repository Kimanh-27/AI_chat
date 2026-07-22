from fastapi import APIRouter, UploadFile, File, Form
from PIL import Image
from database import get_db_connection
from fastapi import APIRouter,Form
from schemas import HomeworkData,SaveAndAssignRequest,ThongBaoLopModel,DeleteHomeworkRequest,ChuDeModel,TuVungModel,BulkTuVungModel
import json
import io
import datetime
import random
import re
from prompts import TOEIC_CONFIG
import google.generativeai as genai


router = APIRouter()

#=============================================================
#===      GIÁO VIÊN THỐNG KÊ PHÂN TÍCH THEO LỚP     ==========
#=============================================================
# 1. API lấy danh sách lớp học của một giáo viên cụ thể
@router.get("/get-classes/{id_giao_vien}")
async def get_teacher_classes(id_giao_vien: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Lấy lớp từ bảng lop_hoc theo id_giao_vien
        cursor.execute("SELECT id, ten_lop FROM lop_hoc WHERE id_giao_vien = %s", (id_giao_vien,))
        classes = cursor.fetchall()
        
        conn.close()
        return {"status": "success", "data": classes}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 2. API lấy thống kê THEO TỪNG LỚP
@router.get("/get-statistics/{id_lop}")
async def get_class_statistics(id_lop: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                COUNT(DISTINCT h.id) as total_students,
                COUNT(l.id) as total_tests,
                SUM(l.diem_so) as total_correct,
                SUM(l.tong_so_cau) as total_questions
            FROM hoc_sinh h
            LEFT JOIN lich_su_lam_bai l ON h.id_tai_khoan = l.id_hoc_sinh
            WHERE h.id_lop = %s
        """, (id_lop,))
        overview = cursor.fetchone()

        cursor.execute("""
            SELECT l.part, SUM(l.diem_so) as total_correct, SUM(l.tong_so_cau) as total_questions
            FROM lich_su_lam_bai l
            JOIN hoc_sinh h ON l.id_hoc_sinh = h.id_tai_khoan
            WHERE h.id_lop = %s
            GROUP BY l.part
            ORDER BY l.part ASC
        """, (id_lop,))
        part_stats = cursor.fetchall()

        cursor.execute("""
            SELECT h.ten_hoc_sinh, l.part, l.diem_so, l.tong_so_cau, l.thoi_gian_nop 
            FROM lich_su_lam_bai l
            JOIN hoc_sinh h ON l.id_hoc_sinh = h.id_tai_khoan
            WHERE h.id_lop = %s
            ORDER BY l.thoi_gian_nop DESC 
        """, (id_lop,))
        recent_activities = cursor.fetchall()

        conn.close()
        return {
            "status": "success", 
            "overview": overview, 
            "part_stats": part_stats,
            "recent_activities": recent_activities
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

#=============================================================
#===            GIÁO VIÊN GIAO BÀI TẬP              ==========
#=============================================================
# 1. API Giao bài tập mới
@router.post("/assign-homework")
async def assign_homework(data: HomeworkData):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Lấy toàn bộ câu hỏi của Part đó để chọn
        cursor.execute("SELECT id, doan_van FROM ngan_hang_cau_hoi WHERE part = %s", (data.part,))
        all_questions = cursor.fetchall()
        
        if not all_questions:
            return {"status": "error", "message": "Kho câu hỏi Part này đang trống!"}

        selected_ids = []
        
        if data.part in ["part_3", "part_4", "part_6", "part_7"]:
            groups = {}
            for q in all_questions:
                key = q['doan_van'] if q['doan_van'] else str(q['id'])
                if key not in groups: groups[key] = []
                groups[key].append(q['id'])
            
            group_list = list(groups.values())
            random.shuffle(group_list)
            
            count = 0
            for g in group_list:
                if count < data.so_luong_cau:
                    selected_ids.extend(g)
                    count += 1 
                else: break
        else:
            ids = [q['id'] for q in all_questions]
            random.shuffle(ids)
            selected_ids = ids[:data.so_luong_cau]

        ids_json = json.dumps(selected_ids)

        sql = """INSERT INTO bai_tap_giao 
                 (ten_bai_tap, id_giao_vien, id_lop, part, so_luong_cau, han_nop, thoi_gian_lam_bai, danh_sach_cau_hoi) 
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
        val = (data.ten_bai_tap, data.id_giao_vien, data.id_lop, data.part, data.so_luong_cau, data.han_nop, data.thoi_gian_lam_bai, ids_json)
        cursor.execute(sql, val)
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã tạo đề chung và giao bài thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 2. API Lấy danh sách bài tập đã giao của giáo viên
@router.get("/get-homeworks/{id_giao_vien}")
async def get_teacher_homeworks(id_giao_vien: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        sql = """
            SELECT b.*, l.ten_lop 
            FROM bai_tap_giao b
            JOIN lop_hoc l ON b.id_lop = l.id
            WHERE b.id_giao_vien = %s
            ORDER BY b.ngay_tao DESC
        """
        cursor.execute(sql, (id_giao_vien,))
        homeworks = cursor.fetchall()
        conn.close()
        return {"status": "success", "data": homeworks}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# API Cập nhật Bài tập giao
@router.post("/update-homework/{id_bai_tap}")
async def update_homework(id_bai_tap: int, data: dict):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        #kiểm tra giao riêng hay giao chung
        cursor.execute("SELECT id_hoc_sinh FROM bai_tap_giao WHERE id = %s", (id_bai_tap,))
        hw_info = cursor.fetchone()
        
        if not hw_info:
            return {"status": "error", "message": "Không tìm thấy bài tập."}
            
        new_deadline = data.get('han_nop')
        new_lop = data.get('id_lop')
        
        # nếu là bài tập giao theo lớp (id_hoc_sinh bị trống/null) -> Cập nhật cả Lớp và Hạn nộp
        if hw_info.get('id_hoc_sinh') is None:
            sql = "UPDATE bai_tap_giao SET han_nop = %s, id_lop = %s WHERE id = %s"
            cursor.execute(sql, (new_deadline, new_lop, id_bai_tap))
        # Nếu là bài tập cá nhân -> KHÔNG ĐƯỢC ĐỔI LỚP, chỉ cho phép đổi Hạn nộp
        else:
            sql = "UPDATE bai_tap_giao SET han_nop = %s WHERE id = %s"
            cursor.execute(sql, (new_deadline, id_bai_tap))

        conn.commit()
        conn.close()
        return {"status": "success", "message": "Cập nhật thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-homeworks")
async def delete_homeworks(req: DeleteHomeworkRequest):
    try:
        if not req.ids:
            return {"status": "error", "message": "Không có bài tập nào được chọn."}
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # chuỗi định dạng %s,%s,%s tương ứng với số lượng ID cần xóa
        format_strings = ','.join(['%s'] * len(req.ids))
        sql = f"DELETE FROM bai_tap_giao WHERE id IN ({format_strings})"
        cursor.execute(sql, tuple(req.ids))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã xóa thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    

#======================================================
# Giáo viên xem chi tiết kết quả của 1 bài tập
#======================================================
@router.get("/get-homework-results/{id_bai_tap}")
async def get_homework_results(id_bai_tap: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM bai_tap_giao WHERE id = %s", (id_bai_tap,))
        hw = cursor.fetchone()

        if not hw:
            return {"status": "error", "message": "Không tìm thấy bài tập"}

        if hw.get('id_hoc_sinh'):
            sql = """
                SELECT h.ten_hoc_sinh, h.id_tai_khoan,
                       l.diem_so, l.tong_so_cau, l.thoi_gian_nop
                FROM hoc_sinh h
                LEFT JOIN lich_su_lam_bai l 
                       ON h.id_tai_khoan = l.id_hoc_sinh AND l.id_bai_tap = %s
                WHERE h.id_lop = %s AND h.id_tai_khoan = %s
                ORDER BY l.diem_so DESC, h.ten_hoc_sinh ASC
            """
            cursor.execute(sql, (id_bai_tap, hw['id_lop'], hw['id_hoc_sinh']))
        else:
            # LUỒNG 2: BÀI TẬP CỦA LỚP -> Lôi toàn bộ danh sách lớp
            sql = """
                SELECT h.ten_hoc_sinh, h.id_tai_khoan,
                       l.diem_so, l.tong_so_cau, l.thoi_gian_nop
                FROM hoc_sinh h
                LEFT JOIN lich_su_lam_bai l 
                       ON h.id_tai_khoan = l.id_hoc_sinh AND l.id_bai_tap = %s
                WHERE h.id_lop = %s
                ORDER BY l.diem_so DESC, h.ten_hoc_sinh ASC
            """
            cursor.execute(sql, (id_bai_tap, hw['id_lop']))
            
        results = cursor.fetchall()

        conn.close()
        return {"status": "success", "homework": hw, "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# GIÁO VIÊN YÊU CẦU AI ĐÁNH GIÁ CHI TIẾT 1 HỌC SINH
# ==========================================
@router.get("/evaluate-student/{id_hoc_sinh}")
async def evaluate_student_performance(id_hoc_sinh: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
    
        cursor.execute("""
            SELECT part, SUM(diem_so) as total_correct, SUM(tong_so_cau) as total_q
            FROM lich_su_lam_bai
            WHERE id_hoc_sinh = %s
            GROUP BY part
        """, (id_hoc_sinh,))
        stats = cursor.fetchall()

        if not stats:
            db.close()
            return {"status": "error", "message": "Học sinh này chưa có dữ liệu làm bài."}

        weak_parts = []
        for s in stats:
            accuracy = (s['total_correct'] / s['total_q']) * 100
            if accuracy < 50:
                weak_parts.append({
                    "part": s['part'],
                    "accuracy": round(accuracy, 1),
                    "label": f"{s['part'].replace('_', ' ').upper()} ({round(accuracy, 1)}%)"
                })
        weak_parts.sort(key=lambda x: x['accuracy'])

        cursor.execute("""
            SELECT chi_tiet_bai_lam
            FROM lich_su_lam_bai
            WHERE id_hoc_sinh = %s
            ORDER BY thoi_gian_nop DESC LIMIT 10
        """, (id_hoc_sinh,))
        recent_tests = cursor.fetchall()

        wrong_ids = []
        for t in recent_tests:
            if t['chi_tiet_bai_lam']:
                try:
                    chi_tiet = json.loads(t['chi_tiet_bai_lam'])
                    for item in chi_tiet:
                        if not item.get('is_correct') and item.get('id_cau_hoi'):
                            wrong_ids.append(str(item['id_cau_hoi']))
                except: pass

        wrong_questions_text = ""
        if wrong_ids:
            import random
            sample_ids = random.sample(wrong_ids, min(len(wrong_ids), 10))
            format_strings = ','.join(['%s'] * len(sample_ids))
            
            cursor.execute(f"SELECT part, cau_hoi, dap_an_dung FROM ngan_hang_cau_hoi WHERE id IN ({format_strings})", tuple(sample_ids))
            wrong_qs = cursor.fetchall()
            
            for q in wrong_qs:
                wrong_questions_text += f"- [Part {q['part']}] Câu hỏi: {q['cau_hoi']} (Đáp án đúng: {q['dap_an_dung']})\n"

        db.close()

        model = genai.GenerativeModel('gemini-2.5-flash')
        
        if not weak_parts:
            prompt = """Bạn là chuyên gia phân tích Giáo dục. Học sinh này có tất cả các kỹ năng đều đạt trên 50%, biểu hiện rất xuất sắc.
YÊU CẦU BÁO CÁO CHO GIÁO VIÊN:
Hãy viết 1 đoạn ngắn (khoảng 2 câu) báo cáo cho giáo viên, đánh giá học sinh có phong độ đồng đều. 
TUYỆT ĐỐI KHÔNG dùng các từ ngữ chào hỏi/thư từ như "Kính gửi", "Thưa", "Chào", "Báo cáo phân tích...". Đi thẳng trực tiếp vào câu đánh giá. KHÔNG xưng hô với học sinh."""
            
            response = await model.generate_content_async(prompt)
            nhan_xet = response.text.strip()
            focus_topic_text = "Tiếp tục phát huy phong độ"
            
        else:
            weak_list_str = ", ".join([p['label'] for p in weak_parts])
            prompt = f"""Bạn là chuyên gia phân tích Giáo dục. Học sinh đang yếu các phần: {weak_list_str} (dưới 50%).
Dưới đây là NỘI DUNG THỰC TẾ các câu hỏi học sinh đã làm sai:
{wrong_questions_text if wrong_questions_text else "Không có dữ liệu câu hỏi chi tiết."}
    
YÊU CẦU BÁO CÁO CHO GIÁO VIÊN:
1. Đọc kỹ các câu hỏi sai, KẾT LUẬN CỤ THỂ học sinh đang hổng kiến thức ở DẠNG NÀO.
2. Trình bày dưới dạng CÁC GẠCH ĐẦU DÒNG (dùng dấu -). Mỗi gạch đầu dòng là một vấn đề cụ thể, ngắn gọn.
3. TUYỆT ĐỐI KHÔNG viết thành 1 đoạn văn dài liền mạch.
4. Hành văn KHÁCH QUAN.
5. LỆNH CẤM BẮT BUỘC: TUYỆT ĐỐI KHÔNG dùng các từ ngữ chào hỏi/thư từ như "Kính gửi", "Thưa", "Chào", "Báo cáo phân tích...". Bắt đầu ngay lập tức bằng gạch đầu dòng đầu tiên."""
            
            response = await model.generate_content_async(prompt)
            nhan_xet = response.text.strip()

            focus_prompt = f"Dựa vào báo cáo này: '{nhan_xet}', hãy tóm tắt nội dung kiến thức cần luyện tập thành 1 câu ngắn gọn (VD: 'Từ vựng công sở và thì hoàn thành'). Không giải thích thêm."
            focus_response = await model.generate_content_async(focus_prompt)
            focus_topic_text = focus_response.text.strip().replace('"', '')

        return {
            "status": "success",
            "data": {
                "id_hoc_sinh": id_hoc_sinh,
                "nhan_xet": nhan_xet,
                "focus_topic": focus_topic_text,
                "weak_parts": weak_parts
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# LẤY DANH SÁCH HỌC SINH & TIẾN ĐỘ TỪNG PART
# ==========================================
@router.get("/class-progress/{id_lop}")
async def get_class_progress(id_lop: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT id_tai_khoan, ten_hoc_sinh FROM hoc_sinh WHERE id_lop = %s", (id_lop,))
        students = cursor.fetchall()
        
        sql = """
            SELECT h.id_tai_khoan, l.part, SUM(l.diem_so) as total_correct, SUM(l.tong_so_cau) as total_questions
            FROM hoc_sinh h
            LEFT JOIN lich_su_lam_bai l ON h.id_tai_khoan = l.id_hoc_sinh
            WHERE h.id_lop = %s AND l.part IS NOT NULL
            GROUP BY h.id_tai_khoan, l.part
        """
        cursor.execute(sql, (id_lop,))
        stats = cursor.fetchall()
        conn.close()
        
        result_data = []
        for st in students:
            st_data = {
                "id": st['id_tai_khoan'],
                "ten": st['ten_hoc_sinh'],
                "parts": {f"part_{i}": 0 for i in range(1, 8)} 
            }
            for stat in stats:
                if stat['id_tai_khoan'] == st['id_tai_khoan']:
                    part_key = stat['part']
                    if stat['total_questions'] > 0:
                        pct = round((stat['total_correct'] / stat['total_questions']) * 100)
                        st_data['parts'][part_key] = pct
            result_data.append(st_data)
            
        return {"status": "success", "data": result_data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# SINH ĐỀ KHẮC PHỤC TRỰC TIẾP TRONG MODAL
# ==========================================
@router.post("/generate-remedial")
async def generate_remedial(part: str = Form(...), num_items: int = Form(...), focus_topic: str = Form(...)):
    try:
        config = TOEIC_CONFIG.get(part)
        if not config:
            return {"status": "error", "message": "Không tìm thấy cấu hình cho Part này."}

        system_prompt = config["system_prompt"].replace("{num_questions}", str(num_items))
        remedial_rule = f"\n\n[MỤC TIÊU KHẮC PHỤC LỖ HỔNG KIẾN THỨC BẮT BUỘC]: Hãy sinh đề tập trung hoàn toàn vào mảng bối cảnh/ngữ pháp/từ vựng sau đây: '{focus_topic}'."
        final_prompt = system_prompt + remedial_rule

        model = genai.GenerativeModel(
                'gemini-2.5-flash',
                generation_config={"response_mime_type": "application/json"}
            )
        response = await model.generate_content_async(final_prompt)
        raw_text = response.text.strip()
        
        match = re.search(r'(\[.*\]|\{.*\})', raw_text, re.DOTALL)
        if match:
            clean_json = match.group(1)
            parsed_data = json.loads(clean_json)
            return {"status": "success", "data": parsed_data}
        else:
            return {"status": "error", "message": "Nova không tạo ra được cấu trúc đề thi khắc phục lỗi."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# LƯU ĐỀ VÀ TẠO BÀI TẬP GIAO CHO CÁ NHÂN HỌC SINH
# ==========================================
@router.post("/save-and-assign")
async def save_and_assign(req: SaveAndAssignRequest):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        inserted_ids = []

        sql_insert_q = """INSERT INTO ngan_hang_cau_hoi 
                         (id_giao_vien, id_dang_bai, part, hinh_anh, doan_van, cau_hoi, dap_an_a, dap_an_b, dap_an_c, dap_an_d, dap_an_dung, giai_thich) 
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        
        for q in req.questions:
            val = (req.id_giao_vien, None, req.part, None, None, q['cau_hoi'], q['dap_an_a'], q['dap_an_b'], q['dap_an_c'], q.get('dap_an_d', ''), q['dap_an_dung'], q['giai_thich'])
            cursor.execute(sql_insert_q, val)
            inserted_ids.append(cursor.lastrowid)

        # Tạo Bài Tập Giao CÁ NHÂN
        han_nop = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%dT23:59:59")
        ids_json = json.dumps(inserted_ids)
        
        sql_hw = """INSERT INTO bai_tap_giao 
                    (ten_bai_tap, id_giao_vien, id_lop, id_hoc_sinh, part, so_luong_cau, han_nop, thoi_gian_lam_bai, danh_sach_cau_hoi) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        val_hw = (req.ten_bai_tap, req.id_giao_vien, req.id_lop, req.id_hoc_sinh, req.part, len(inserted_ids), han_nop, 15, ids_json)
        cursor.execute(sql_hw, val_hw)

        db.commit()
        cursor.close()
        db.close()
        return {"status": "success", "message": "Đã tạo và giao bài thành công cho cá nhân học sinh!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==========================================
# API: LẤY SỐ LƯỢNG CÂU HỎI THEO PART
# ==========================================
@router.get("/get-part-counts/{id_giao_vien}")
async def get_part_counts(id_giao_vien: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = "SELECT part, COUNT(id) as count FROM ngan_hang_cau_hoi WHERE id_giao_vien = %s GROUP BY part"
        cursor.execute(sql, (id_giao_vien,))
        counts = cursor.fetchall()
        db.close()
        return {"status": "success", "data": counts}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# API: LẤY DANH SÁCH CÂU HỎI ÔN LUYỆN
# ==========================================
@router.get("/get-practice-questions/{part_name}/{id_giao_vien}")
async def get_practice_questions(part_name: str, id_giao_vien: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = "SELECT * FROM ngan_hang_cau_hoi WHERE part = %s AND id_giao_vien = %s ORDER BY id DESC"
        cursor.execute(sql, (part_name, id_giao_vien))
        questions = cursor.fetchall()
        cursor.close()
        db.close()
        return {"status": "success", "data": questions}
    except Exception as e:
        print(f"Lỗi lấy dữ liệu ôn luyện: {e}")
        return {"status": "error", "message": "Không thể kết nối Database."}
    
#-----------Thông báo lớp -------------
@router.get("/get-announcements/{class_id}")
async def get_class_announcements(class_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM thong_bao_lop WHERE id_lop = %s ORDER BY ngay_tao DESC", (class_id,))
        notices = cursor.fetchall()
        db.close()
        return {"status": "success", "data": notices}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/add-announcement")
async def add_announcement(req: ThongBaoLopModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        sql = "INSERT INTO thong_bao_lop (id_lop, tieu_de, noi_dung) VALUES (%s, %s, %s)"
        cursor.execute(sql, (req.id_lop, req.tieu_de, req.noi_dung))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã gửi thông báo cho lớp thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-announcement/{notice_id}")
async def update_announcement(notice_id: int, req: ThongBaoLopModel):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        sql = "UPDATE thong_bao_lop SET tieu_de = %s, noi_dung = %s WHERE id = %s"
        cursor.execute(sql, (req.tieu_de, req.noi_dung, notice_id))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã cập nhật thông báo thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-announcement/{notice_id}")
async def delete_announcement(notice_id: int):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM thong_bao_lop WHERE id = %s", (notice_id,))
        db.commit()
        db.close()
        return {"status": "success", "message": "Đã xóa thông báo thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    


@router.get("/get-topics/{id_giao_vien}")
async def get_topics(id_giao_vien: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM chu_de_tu_vung WHERE id_giao_vien = %s ORDER BY id DESC", (id_giao_vien,))
        topics = cursor.fetchall()
        
        for t in topics:
            cursor.execute("SELECT COUNT(id) as total FROM tu_vung WHERE id_chu_de = %s", (t['id'],))
            t['so_tu'] = cursor.fetchone()['total']
            
        conn.close()
        return {"status": "success", "data": topics}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/add-topic")
async def add_topic(req: ChuDeModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chu_de_tu_vung WHERE chu_de = %s AND id_giao_vien = %s", (req.chu_de, req.id_giao_vien))
        if cursor.fetchone():
            return {"status": "error", "message": "Chủ đề này đã tồn tại!"}
            
        cursor.execute("INSERT INTO chu_de_tu_vung (chu_de, id_giao_vien) VALUES (%s, %s)", (req.chu_de, req.id_giao_vien))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã tạo chủ đề mới!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
@router.post("/update-topic/{id_chu_de}")
async def update_topic(id_chu_de: int, req: ChuDeModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chu_de_tu_vung WHERE chu_de = %s AND id_giao_vien = %s AND id != %s", (req.chu_de, req.id_giao_vien, id_chu_de))
        if cursor.fetchone():
            return {"status": "error", "message": "Tên chủ đề này đã tồn tại!"}
            
        cursor.execute("UPDATE chu_de_tu_vung SET chu_de = %s WHERE id = %s AND id_giao_vien = %s", (req.chu_de, id_chu_de, req.id_giao_vien))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã cập nhật tên chủ đề!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-topic/{id_chu_de}")
async def delete_topic(id_chu_de: int, id_giao_vien: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tu_vung WHERE id_chu_de = %s", (id_chu_de,))
        cursor.execute("DELETE FROM chu_de_tu_vung WHERE id = %s AND id_giao_vien = %s", (id_chu_de, id_giao_vien))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã xóa chủ đề!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/get-vocab/{id_chu_de}")
async def get_vocab(id_chu_de: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tu_vung WHERE id_chu_de = %s ORDER BY id DESC", (id_chu_de,))
        vocab = cursor.fetchall()
        conn.close()
        return {"status": "success", "data": vocab}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/add-vocab")
async def add_vocab(req: TuVungModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tu_vung WHERE tu = %s AND id_chu_de = %s AND id_giao_vien = %s", (req.tu, req.id_chu_de, req.id_giao_vien))
        if cursor.fetchone():
            return {"status": "error", "message": "Từ vựng đã có sẵn trong chủ đề này!"}
            
        cursor.execute("INSERT INTO tu_vung (id_giao_vien, id_chu_de, tu, phien_am, nghia, vi_du) VALUES (%s, %s, %s, %s, %s, %s)", 
                      (req.id_giao_vien, req.id_chu_de, req.tu, req.phien_am, req.nghia, req.vi_du))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã thêm từ vựng mới!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/update-vocab/{id_tu}")
async def update_vocab(id_tu: int, req: TuVungModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tu_vung WHERE tu = %s AND id_chu_de = %s AND id != %s", (req.tu, req.id_chu_de, id_tu))
        if cursor.fetchone():
            return {"status": "error", "message": "Từ vựng này đã tồn tại!"}
            
        cursor.execute("UPDATE tu_vung SET tu=%s, phien_am=%s, nghia=%s, vi_du=%s WHERE id=%s AND id_giao_vien=%s", 
                      (req.tu, req.phien_am, req.nghia, req.vi_du, id_tu, req.id_giao_vien))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã cập nhật từ vựng!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/delete-vocab/{id_tu}")
async def delete_vocab(id_tu: int, id_giao_vien: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tu_vung WHERE id = %s AND id_giao_vien = %s", (id_tu, id_giao_vien))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã xóa từ vựng!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/bulk-add-vocab")
async def bulk_add_vocab(req: BulkTuVungModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        success_count = 0
        
        for item in req.danh_sach_tu:
            cursor.execute("SELECT id FROM tu_vung WHERE tu = %s AND id_chu_de = %s AND id_giao_vien = %s", (item.tu, req.id_chu_de, req.id_giao_vien))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO tu_vung (id_giao_vien, id_chu_de, tu, phien_am, nghia, vi_du) VALUES (%s, %s, %s, %s, %s, %s)", 
                              (req.id_giao_vien, req.id_chu_de, item.tu, item.phien_am, item.nghia, item.vi_du))
                success_count += 1
                
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Đã nhập thành công {success_count} từ vựng từ Nova!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/extract-vocab-media")
async def extract_vocabulary_media(file: UploadFile = File(None), text: str = Form(None)):
    try:
        if not file and not text:
            return {"status": "error", "message": "Vui lòng tải lên ảnh hoặc nhập văn bản."}

        contents = []
        system_prompt = """Bạn là chuyên gia trích xuất dữ liệu. Hãy đọc hình ảnh hoặc dữ liệu bảng được cung cấp và trích xuất TOÀN BỘ danh sách từ vựng có trong đó.
TRẢ VỀ ĐÚNG ĐỊNH DẠNG MẢNG JSON, bao gồm các key: "tu", "phien_am", "nghia", "vi_du".
Bám sát chính xác dữ liệu gốc, không tự bịa thêm từ, không bỏ sót từ nào. Giữ nguyên định dạng gốc của từ (bao gồm cả loại từ như (n), (v) nếu có). Nếu trong ảnh/dữ liệu không có phiên âm hoặc ví dụ cụ thể, hãy để chuỗi rỗng "".
Ví dụ: [{"tu": "Family (n)", "phien_am": "/ˈfæm.əl.i/", "nghia": "Gia đình", "vi_du": ""}]"""
        
        contents.append(system_prompt)

        if file:
            image_data = await file.read()
            image = Image.open(io.BytesIO(image_data))
            contents.append(image)
        
        if text:
            contents.append(text)

        model = genai.GenerativeModel(
            'gemini-2.5-flash',
            generation_config={"response_mime_type": "application/json"}
        )
        
        response = await model.generate_content_async(contents)
        raw_output = response.text.strip()

        match = re.search(r'(\[.*\])', raw_output, re.DOTALL)
        if match:
            clean_json = match.group(1)
            parsed_data = json.loads(clean_json)
            return {"status": "success", "data": parsed_data}
        else:
            return {"status": "error", "message": "Nova không thể tạo cấu trúc JSON từ dữ liệu cung cấp."}

    except Exception as e:
        return {"status": "error", "message": str(e)}