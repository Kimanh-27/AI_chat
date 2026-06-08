from fastapi import APIRouter
from database import get_db_connection
from schemas import HomeworkData, RemedialGenRequest, SaveAndAssignRequest,ThongBaoLopModel
import json
import datetime
import random
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
            LIMIT 8
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

        # 2. LỤC TÌM NỘI DUNG CÂU HỎI MÀ HỌC SINH ĐÃ LÀM SAI
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
                        # Lấy các câu có is_correct = 0 hoặc False
                        if not item.get('is_correct') and item.get('id_cau_hoi'):
                            wrong_ids.append(str(item['id_cau_hoi']))
                except: pass

        wrong_questions_text = ""
        if wrong_ids:
            import random
            # Trích xuất ngẫu nhiên 10 câu sai để AI không bị quá tải
            sample_ids = random.sample(wrong_ids, min(len(wrong_ids), 10))
            format_strings = ','.join(['%s'] * len(sample_ids))
            
            cursor.execute(f"SELECT part, cau_hoi, dap_an_dung FROM ngan_hang_cau_hoi WHERE id IN ({format_strings})", tuple(sample_ids))
            wrong_qs = cursor.fetchall()
            
            for q in wrong_qs:
                wrong_questions_text += f"- [Part {q['part']}] Câu hỏi: {q['cau_hoi']} (Đáp án đúng: {q['dap_an_dung']})\n"

        db.close()

        # 3. YÊU CẦU GEMINI BẮT ĐÚNG BỆNH TỪ VỰNG / NGỮ PHÁP
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        if not weak_parts:
            # LUỒNG 1: HỌC SINH GIỎI (Không có Part nào < 50%)
            prompt = """Bạn là chuyên gia phân tích Giáo dục. Học sinh này có tất cả các kỹ năng đều đạt trên 50%, biểu hiện rất xuất sắc.
YÊU CẦU BÁO CÁO CHO GIÁO VIÊN:
Hãy viết 1 đoạn ngắn (khoảng 2 câu) báo cáo cho giáo viên, đánh giá học sinh có phong độ đồng đều và nắm vững kiến thức. 
TUYỆT ĐỐI KHÔNG phân tích lỗ hổng hay điểm yếu nào cả. KHÔNG xưng hô với học sinh."""
            
            response = await model.generate_content_async(prompt)
            nhan_xet = response.text.strip()
            focus_topic_text = "Tiếp tục phát huy phong độ"
            
        else:
            # LUỒNG 2: HỌC SINH YẾU (Có Part < 50%)
            weak_list_str = ", ".join([p['label'] for p in weak_parts])
            prompt = f"""Bạn là chuyên gia phân tích Giáo dục. Học sinh đang yếu các phần: {weak_list_str} (dưới 50%).
Dưới đây là NỘI DUNG THỰC TẾ các câu hỏi học sinh đã làm sai:
{wrong_questions_text if wrong_questions_text else "Không có dữ liệu câu hỏi chi tiết."}
    
YÊU CẦU BÁO CÁO CHO GIÁO VIÊN:
1. Đọc kỹ các câu hỏi sai, KẾT LUẬN CỤ THỂ học sinh đang hổng kiến thức ở DẠNG NÀO.
2. Trình bày dưới dạng CÁC GẠCH ĐẦU DÒNG (dùng dấu -). Mỗi gạch đầu dòng là một vấn đề cụ thể, ngắn gọn.
3. TUYỆT ĐỐI KHÔNG viết thành 1 đoạn văn dài liền mạch.
4. Hành văn KHÁCH QUAN, CHUYÊN MÔN (Báo cáo cho giáo viên, không xưng hô với học sinh). Không dùng ký tự in đậm (**)."""
            
            response = await model.generate_content_async(prompt)
            nhan_xet = response.text.strip()

            focus_prompt = f"Dựa vào báo cáo này: '{nhan_xet}', hãy tóm tắt nội dung kiến thức cần luyện tập thành 1 câu ngắn gọn (VD: 'Từ vựng công sở và thì hoàn thành'). Không giải thích thêm."
            focus_response = await model.generate_content_async(focus_prompt)
            focus_topic_text = focus_response.text.strip().replace('"', '')

        # 4. TRẢ KẾT QUẢ VỀ FRONTEND
        return {
            "status": "success",
            "data": {
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
async def generate_remedial(req: RemedialGenRequest):
    try:
        parts_to_gen = req.parts
        focus_info = req.focus_topic
        
        instruction = ""
        if len(parts_to_gen) == 1:
            p = parts_to_gen[0]
            if p in ["part_3", "part_4", "part_6", "part_7"]:
                instruction = f"Tạo 2 đoạn văn/hội thoại cho {p.upper()}, mỗi đoạn 3 câu hỏi trắc nghiệm."
            else:
                instruction = f"Tạo 10 câu hỏi trắc nghiệm lẻ cho {p.upper()}."
        else:
            instruction = f"Tạo một đề hỗn hợp gồm các phần: {', '.join(parts_to_gen).upper()}. Tổng cộng khoảng 10 câu hỏi."

        system_prompt = f"""Bạn là chuyên gia ra đề thi TOEIC. {instruction}
Chủ đề điểm yếu cần tập trung xoáy sâu vào: "{focus_info}".

BẮT BUỘC TRẢ VỀ CHUẨN JSON LÀ MỘT MẢNG (ARRAY) CÁC OBJECT CÓ CẤU TRÚC SAU:
[
  {{
    "doan_van": "Nội dung đoạn văn (nếu có, nếu không để rỗng)",
    "cau_hoi": "Nội dung câu hỏi",
    "dap_an_a": "Lựa chọn A",
    "dap_an_b": "Lựa chọn B",
    "dap_an_c": "Lựa chọn C",
    "dap_an_d": "Lựa chọn D",
    "dap_an_dung": "A, B, C hoặc D",
    "giai_thich": "Giải thích vì sao đúng"
  }}
]"""

        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash',
            generation_config={"response_mime_type": "application/json"}
        )
        response = await model.generate_content_async(system_prompt)
        return {"status": "success", "data": json.loads(response.text)}
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