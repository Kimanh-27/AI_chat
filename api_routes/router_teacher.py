from fastapi import APIRouter, UploadFile, File, Form, Query
from database import get_db_connection
from fastapi import APIRouter,Form
from schemas import HomeworkData,SaveAndAssignRequest,ThongBaoLopModel,DeleteHomeworkRequest,ChuDeModel,TuVungModel,BulkTuVungModel,TaiLieuHocModel, GenerateQuizRequest
import json
import io
import datetime
import random
import re
import os
import uuid 
from prompts import TOEIC_CONFIG
import google.generativeai as genai
import pymupdf as fitz
from dotenv import load_dotenv
from huggingface_hub import AsyncInferenceClient

router = APIRouter()

load_dotenv()
HF_TOKEN = os.getenv("HUGGING_FACE_API_KEY")
hf_client = AsyncInferenceClient(api_key=HF_TOKEN)

def log_teacher_activity(cursor, id_gv, action, detail=""):
    sql = "INSERT INTO lich_su_hoat_dong_gv (id_giao_vien, hanh_dong, chi_tiet) VALUES (%s, %s, %s)"
    cursor.execute(sql, (id_gv, action, detail))


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

        # 1. Overview
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT h.id) as total_students,
                COUNT(l.id) as total_tests,
                IFNULL(SUM(l.diem_so), 0) as total_correct,
                IFNULL(SUM(l.tong_so_cau), 0) as total_questions
            FROM chi_tiet_lop_hoc c
            LEFT JOIN hoc_sinh h ON c.id_hoc_sinh = h.id_tai_khoan
            LEFT JOIN lich_su_lam_bai l ON h.id_tai_khoan = l.id_hoc_sinh
            WHERE c.id_lop = %s
        """, (id_lop,))
        overview = cursor.fetchone()
        
        if not overview:
            overview = {"total_students": 0, "total_tests": 0, "total_correct": 0, "total_questions": 0}

        # 2. Thống kê theo Part
        cursor.execute("""
            SELECT l.part, IFNULL(SUM(l.diem_so), 0) as total_correct, IFNULL(SUM(l.tong_so_cau), 0) as total_questions
            FROM lich_su_lam_bai l
            JOIN chi_tiet_lop_hoc c ON l.id_hoc_sinh = c.id_hoc_sinh
            WHERE c.id_lop = %s
            GROUP BY l.part
            ORDER BY l.part ASC
        """, (id_lop,))
        part_stats = cursor.fetchall()

        # 3. Hoạt động gần đây
        cursor.execute("""
            SELECT h.ten_hoc_sinh, l.part, l.diem_so, l.tong_so_cau, l.thoi_gian_nop 
            FROM lich_su_lam_bai l
            JOIN hoc_sinh h ON l.id_hoc_sinh = h.id_tai_khoan
            JOIN chi_tiet_lop_hoc c ON h.id_tai_khoan = c.id_hoc_sinh
            WHERE c.id_lop = %s
            ORDER BY l.thoi_gian_nop DESC 
            LIMIT 50
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
        
        chi_tiet_log = f"Đã tạo và giao bài tập: '{data.ten_bai_tap}' (Part {data.part.replace('part_', '')})"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'GIAO_BAI_TAP', %s)", (data.id_giao_vien, chi_tiet_log))

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
        cursor.execute("SELECT id_hoc_sinh, id_giao_vien, ten_bai_tap FROM bai_tap_giao WHERE id = %s", (id_bai_tap,))
        hw_info = cursor.fetchone()
        
        if not hw_info:
            return {"status": "error", "message": "Không tìm thấy bài tập."}
            
        new_deadline = data.get('han_nop')
        new_lop = data.get('id_lop')
        
        if hw_info.get('id_hoc_sinh') is None:
            sql = "UPDATE bai_tap_giao SET han_nop = %s, id_lop = %s WHERE id = %s"
            cursor.execute(sql, (new_deadline, new_lop, id_bai_tap))
        else:
            sql = "UPDATE bai_tap_giao SET han_nop = %s WHERE id = %s"
            cursor.execute(sql, (new_deadline, id_bai_tap))

        chi_tiet_log = f"Đã cập nhật hạn nộp/lớp cho bài tập: '{hw_info['ten_bai_tap']}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'GIAO_BAI_TAP', %s)", (hw_info['id_giao_vien'], chi_tiet_log))

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
        
        cursor.execute("SELECT id_giao_vien FROM bai_tap_giao WHERE id = %s", (req.ids[0],))
        gv_info = cursor.fetchone()
        
        format_strings = ','.join(['%s'] * len(req.ids))
        sql = f"DELETE FROM bai_tap_giao WHERE id IN ({format_strings})"
        cursor.execute(sql, tuple(req.ids))
        
        if gv_info:
            chi_tiet_log = f"Đã xóa {len(req.ids)} bài tập đã giao"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'GIAO_BAI_TAP', %s)", (gv_info[0], chi_tiet_log))

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

        model_name = "Qwen/Qwen3-32B"
        
        if not weak_parts:
            prompt = """Bạn là chuyên gia phân tích Giáo dục. Học sinh này có tất cả các kỹ năng đều đạt trên 50%, biểu hiện rất xuất sắc.
YÊU CẦU BÁO CÁO CHO GIÁO VIÊN:
Hãy viết 1 đoạn ngắn (khoảng 2 câu) báo cáo cho giáo viên, đánh giá học sinh có phong độ đồng đều. 
TUYỆT ĐỐI KHÔNG dùng các từ ngữ chào hỏi/thư từ như "Kính gửi", "Thưa", "Chào", "Báo cáo phân tích...". Đi thẳng trực tiếp vào câu đánh giá. KHÔNG xưng hô với học sinh."""
            
            messages = [{"role": "user", "content": prompt}]
            response = await hf_client.chat_completion(messages=messages, model=model_name, max_tokens=1000)
            nhan_xet = response.choices[0].message.content.strip()
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
            
            messages = [{"role": "user", "content": prompt}]
            response = await hf_client.chat_completion(messages=messages, model=model_name, max_tokens=1500)
            nhan_xet = response.choices[0].message.content.strip()

            focus_prompt = f"Dựa vào báo cáo này: '{nhan_xet}', hãy tóm tắt nội dung kiến thức cần luyện tập thành 1 câu ngắn gọn (VD: 'Từ vựng công sở và thì hoàn thành'). Không giải thích thêm."
            focus_messages = [{"role": "user", "content": focus_prompt}]
            focus_response = await hf_client.chat_completion(messages=focus_messages, model=model_name, max_tokens=200)
            focus_topic_text = focus_response.choices[0].message.content.strip().replace('"', '')

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

        cursor.execute("""
            SELECT h.id_tai_khoan as id, h.ten_hoc_sinh as ten
            FROM chi_tiet_lop_hoc c
            JOIN hoc_sinh h ON c.id_hoc_sinh = h.id_tai_khoan
            WHERE c.id_lop = %s
        """, (id_lop,))
        students = cursor.fetchall()

        result_data = []
        for st in students:
            id_hs = st["id"]
            cursor.execute("""
                SELECT part, SUM(diem_so) as diem, SUM(tong_so_cau) as tong 
                FROM lich_su_lam_bai 
                WHERE id_hoc_sinh = %s 
                GROUP BY part
            """, (id_hs,))
            history = cursor.fetchall()

            parts_progress = {f"part_{i}": 0 for i in range(1, 8)}
            for h in history:
                p = h["part"].lower()
                if p in parts_progress and h["tong"] > 0:
                    pct = round((h["diem"] / h["tong"]) * 100)
                    parts_progress[p] = pct

            result_data.append({
                "id": id_hs,
                "ten": st["ten"],
                "parts": parts_progress
            })

        conn.close()
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

        model_name = "Qwen/Qwen3-32B"
        messages = [{"role": "user", "content": final_prompt}]
        
        response = await hf_client.chat_completion(
            messages=messages, 
            model=model_name, 
            max_tokens=2500
        )
        raw_text = response.choices[0].message.content.strip()
        
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

        han_nop = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%dT23:59:59")
        ids_json = json.dumps(inserted_ids)
        
        sql_hw = """INSERT INTO bai_tap_giao 
                    (ten_bai_tap, id_giao_vien, id_lop, id_hoc_sinh, part, so_luong_cau, han_nop, thoi_gian_lam_bai, danh_sach_cau_hoi) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        val_hw = (req.ten_bai_tap, req.id_giao_vien, req.id_lop, req.id_hoc_sinh, req.part, len(inserted_ids), han_nop, 15, ids_json)
        cursor.execute(sql_hw, val_hw)

        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã sinh đề khắc phục ({len(inserted_ids)} câu) và giao cá nhân: '{req.ten_bai_tap}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'GIAO_BAI_TAP', %s)", (req.id_giao_vien, chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        cursor.execute("SELECT id_giao_vien FROM lop_hoc WHERE id = %s", (req.id_lop,))
        gv = cursor.fetchone()
        if gv:
            chi_tiet_log = f"Đã thêm thông báo mới: '{req.tieu_de}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_LOP_HOC', %s)", (gv[0], chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        cursor.execute("SELECT id_giao_vien FROM lop_hoc WHERE id = %s", (req.id_lop,))
        gv = cursor.fetchone()
        if gv:
            chi_tiet_log = f"Đã cập nhật thông báo: '{req.tieu_de}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_LOP_HOC', %s)", (gv[0], chi_tiet_log))

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
        cursor.execute("SELECT id_lop, tieu_de FROM thong_bao_lop WHERE id = %s", (notice_id,))
        notice = cursor.fetchone()
        
        cursor.execute("DELETE FROM thong_bao_lop WHERE id = %s", (notice_id,))
        
        # --- LƯU NHẬT KÝ ---
        if notice:
            cursor.execute("SELECT id_giao_vien FROM lop_hoc WHERE id = %s", (notice[0],))
            gv = cursor.fetchone()
            if gv:
                chi_tiet_log = f"Đã xóa thông báo: '{notice[1]}'"
                cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_LOP_HOC', %s)", (gv[0], chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã tạo chủ đề từ vựng mới: '{req.chu_de}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (req.id_giao_vien, chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã đổi tên chủ đề thành: '{req.chu_de}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (req.id_giao_vien, chi_tiet_log))

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
        # Lấy tên chủ đề trước khi xóa
        cursor.execute("SELECT chu_de FROM chu_de_tu_vung WHERE id = %s", (id_chu_de,))
        chu_de_info = cursor.fetchone()
        
        cursor.execute("DELETE FROM tu_vung WHERE id_chu_de = %s", (id_chu_de,))
        cursor.execute("DELETE FROM chu_de_tu_vung WHERE id = %s AND id_giao_vien = %s", (id_chu_de, id_giao_vien))
        
        # --- LƯU NHẬT KÝ ---
        if chu_de_info:
            chi_tiet_log = f"Đã xóa chủ đề: '{chu_de_info[0]}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (id_giao_vien, chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã thêm từ vựng: '{req.tu}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (req.id_giao_vien, chi_tiet_log))

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
        
        # --- LƯU NHẬT KÝ ---
        chi_tiet_log = f"Đã sửa từ vựng thành: '{req.tu}'"
        cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (req.id_giao_vien, chi_tiet_log))

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
        cursor.execute("SELECT tu FROM tu_vung WHERE id = %s", (id_tu,))
        tu_info = cursor.fetchone()
        
        cursor.execute("DELETE FROM tu_vung WHERE id = %s AND id_giao_vien = %s", (id_tu, id_giao_vien))
        
        # --- LƯU NHẬT KÝ ---
        if tu_info:
            chi_tiet_log = f"Đã xóa từ vựng: '{tu_info[0]}'"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (id_giao_vien, chi_tiet_log))

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
                
        # --- LƯU NHẬT KÝ ---
        if success_count > 0:
            chi_tiet_log = f"Đã quét AI và lưu thành công {success_count} từ vựng"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_TU_VUNG', %s)", (req.id_giao_vien, chi_tiet_log))

        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Đã nhập thành công {success_count} từ vựng từ Nova!"}
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
                    "content": "Bạn là chuyên gia thiết kế web. Nhiệm vụ của bạn là cấu trúc lại HTML thô thành bài giảng đẹp mắt (Dùng <h3>, <ul>, <li>, in đậm). LỆNH BẮT BUỘC: Chỉ trả về mã HTML sạch. TUYỆT ĐỐI GIỮ NGUYÊN 100% các thẻ <img src=\"...\"> và vị trí của chúng. Không tự sáng tác thêm."
                },
                {
                    "role": "user",
                    "content": f"[HTML THÔ CẦN XỬ LÝ]\n{raw_html}"
                }
            ]
            
            response = await hf_client.chat_completion(
                model="Qwen/Qwen3-14B",
                messages=messages,
                max_tokens=2500,
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

@router.get("/doc-view-history/{id_lop}") #router_teacher.py
async def get_doc_view_history(id_lop: int):
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = """
            SELECT x.ten_tai_khoan as ten_hoc_sinh, x.hanh_dong as tieu_de, 'Tài liệu' as loai_tai_lieu, x.thoi_gian as thoi_gian_xem
            FROM lich_su_xem_tai_lieu x
            JOIN chi_tiet_lop_hoc c ON x.id_tai_khoan = c.id_hoc_sinh
            WHERE c.id_lop = %s
            ORDER BY x.thoi_gian DESC LIMIT 50
        """
        cursor.execute(sql, (id_lop,))
        history = cursor.fetchall()
        db.close()
        return {"status": "success", "data": history}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# API TẠO CÂU HỎI TRẮC NGHIỆM MỚI DÙNG CHUNG AI (HUGGINGFACE)
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
