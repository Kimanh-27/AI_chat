from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
import json
import base64
import uuid
import re
import asyncio
import os

from huggingface_hub import InferenceClient
from fastapi.responses import StreamingResponse

from dotenv import load_dotenv
from typing import List
from database import get_db_connection
from schemas import QuestionRequest, ExamRequest, PracticeQuestion, UpdateQuestionModel
from prompts import TOEIC_CONFIG

from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

load_dotenv()

HF_TOKEN = os.getenv("HUGGING_FACE_API_KEY")
client = InferenceClient(api_key=HF_TOKEN)

router = APIRouter()

uploaded_docs_cache = {} 
global_vectorstore = None
nova_tasks = {}

@router.post("/generate_toeic")
async def generate_toeic(
    part: str = Form(...), 
    num_questions: int = Form(None),
    image: UploadFile = File(None)
):
    try:
        config = TOEIC_CONFIG.get(part)
        if not config:
            return {"status": "error", "message": "Không tìm thấy cấu hình cho Part này."}

        q_count = num_questions if num_questions else config["default_questions"]
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, ten_dang FROM danh_muc_dang_bai WHERE part = %s", (part,))
        danh_sach_dang = cursor.fetchall()
        conn.close()
        menu_dang_bai = ", ".join([f"ID {r['id']}: '{r['ten_dang']}'" for r in danh_sach_dang])

        strict_rule = f"""
[QUY TRÌNH AUTO-TAGGING BẮT BUỘC NGHIÊM NGẶT]
Hệ thống hiện có các dạng bài sau: [{menu_dang_bai}].
Để đảm bảo chất lượng, bạn PHẢI làm theo đúng trình tự 3 bước sau:
1. CHỌN DẠNG BÀI TRƯỚC: Chọn ngẫu nhiên {q_count} ID từ danh sách trên (cố gắng phân bổ đều, không lặp lại 1 ID quá nhiều lần).
2. SINH CÂU HỎI: Dựa vào hình ảnh/bối cảnh và MỖI ID vừa chọn, hãy viết ra 1 câu hỏi chuẩn TOEIC bám sát đúng bản chất của dạng bài đó.
3. GẮN ID VÀO JSON: Bắt buộc mỗi object câu hỏi trả về phải có key "id_dang_bai" mang giá trị là ID bạn đã chọn ở Bước 1. Tuyệt đối không được bỏ trống (null).
"""
        final_prompt = config["system_prompt"].replace("{num_questions}", str(q_count)) + strict_rule

        messages = [{"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."}]

        if config["requires_image"]:
            if not image:
                return {"status": "error", "message": f"{part} yêu cầu phải có ảnh!"}
            
            image_bytes = await image.read()
            encoded_string = base64.b64encode(image_bytes).decode('utf-8')
            img_data_uri = f"data:{image.content_type};base64,{encoded_string}"
            
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": final_prompt},
                    {"type": "image_url", "image_url": {"url": img_data_uri}}
                ]
            })
        else:
            messages.append({"role": "user", "content": final_prompt})
        #dùng model
        response = client.chat.completions.create(
            model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
            messages=messages,
            max_tokens=8192,
            temperature=0.5
        )
        raw_text = response.choices[0].message.content.strip()
        
        match = re.search(r'(\[.*\]|\{.*\})', raw_text, re.DOTALL)
        
        if match:
            clean_json = match.group(1)
            parsed_data = json.loads(clean_json)
            return {"status": "success", "data": parsed_data}
        else:
            return {"status": "error", "message": "Nova không tạo ra được cấu trúc đề thi. Vui lòng thử lại!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


#=============================================================
#===                 SINH ĐỀ CHO PART 1              ==========
#=============================================================
@router.post("/generate-part1")
async def generate_part1_api(files: List[UploadFile] = File(...)):
    try:
        config = TOEIC_CONFIG.get("part_1")
        
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, ten_dang FROM danh_muc_dang_bai WHERE part = 'part_1'")
        danh_sach_dang = cursor.fetchall()
        db.close()
        menu_dang_bai = ", ".join([f"ID {r['id']}: '{r['ten_dang']}'" for r in danh_sach_dang])

        strict_rule = f"\nYÊU CẦU AUTO-TAGGING: Dựa vào nội dung bức ảnh, hãy tự phân tích xem nó thuộc dạng nào trong Menu dưới đây, và THÊM trường 'id_dang_bai' (chứa số ID tương ứng) vào JSON.\nMENU DẠNG BÀI HIỆN CÓ: [{menu_dang_bai}]."
        system_prompt = config["system_prompt"] + strict_rule
        
        danh_sach_cau_hoi = [] 
        
        print(f"Bắt đầu xử lý LẦN LƯỢT {len(files)} ảnh qua Hugging Face...")
        
        for index, file in enumerate(files):
            print(f"⏳ Đang xử lý ảnh {index + 1}/{len(files)}: {file.filename}")
            try:
                image_bytes = await file.read()
                encoded_string = base64.b64encode(image_bytes).decode('utf-8')
                img_data_uri = f"data:{file.content_type};base64,{encoded_string}"
                
                response = client.chat.completions.create(
                    model="lolzinventor/Qwen3.5-4B-Base-ZitGen-V1",
                    messages=[
                        {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": system_prompt},
                                {"type": "image_url", "image_url": {"url": img_data_uri}}
                            ]
                        }
                    ],
                    max_tokens=2048,
                    temperature=0.5
                )
                
                raw_text = response.choices[0].message.content.strip()
                
                bt = "`" * 3
                if raw_text.startswith(bt + "json"): raw_text = raw_text[7:]
                elif raw_text.startswith(bt): raw_text = raw_text[3:]
                if raw_text.endswith(bt): raw_text = raw_text[:-3]
                raw_text = raw_text.strip()
                
                match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
                clean_json = match.group(1) if match else raw_text
                
                parsed_data = json.loads(clean_json)
                
                if isinstance(parsed_data, list) and len(parsed_data) > 0:
                    parsed_data[0]['hinh_anh'] = img_data_uri
                    danh_sach_cau_hoi.extend(parsed_data)
                elif isinstance(parsed_data, dict):
                    parsed_data['hinh_anh'] = img_data_uri
                    danh_sach_cau_hoi.append(parsed_data)
                
                print(f"xong ảnh {index + 1}")
                
                if index < len(files) - 1:
                    await asyncio.sleep(2)
                    
            except json.JSONDecodeError as e:
                print(f"❌ Lỗi parse JSON ở file {file.filename}: {e}")
            except Exception as e:
                print(f"❌ Lỗi hệ thống ở ảnh {file.filename}: {e}")
                
        print("hoàn thành")
        return {"status": "success", "data": danh_sach_cau_hoi}
        
    except Exception as e:
        print(f"Lỗi hệ thống to đùng: {e}")
        return {"status": "error", "message": str(e)}


#=============================================================
#===     SINH ĐỀ CHO PART KHÔNG CÓ ẢNH VÀ ĐOẠN VĂN      ======
#=============================================================

@router.post("/generate-questions")
async def generate_questions_api(req: QuestionRequest):
    try:
        config = TOEIC_CONFIG.get(req.part)
        if not config:
            return {"status": "error", "message": "Part này chưa được hỗ trợ."}

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, ten_dang FROM danh_muc_dang_bai WHERE part = %s", (req.part,))
        danh_sach_dang = cursor.fetchall()
        conn.close()
        menu_dang_bai = ", ".join([f"ID {r['id']}: '{r['ten_dang']}'" for r in danh_sach_dang])

        strict_rule = f"""
[QUY TRÌNH AUTO-TAGGING BẮT BUỘC NGHIÊM NGẶT]
Hệ thống hiện có các dạng bài sau: [{menu_dang_bai}].
Để đảm bảo chất lượng, bạn PHẢI làm theo đúng trình tự 3 bước sau để tạo {req.num_questions} câu:
1. CHỌN DẠNG BÀI TRƯỚC: Chọn ngẫu nhiên {req.num_questions} ID từ danh sách trên (cố gắng phân bổ đều, không lặp lại 1 ID quá nhiều lần).
2. SINH CÂU HỎI: Với MỖI ID vừa chọn, hãy suy nghĩ và viết ra 1 câu hỏi chuẩn TOEIC bám sát đúng bản chất của dạng bài đó.
3. GẮN ID VÀO JSON: Bắt buộc mỗi object câu hỏi trả về phải có key "id_dang_bai" mang giá trị là ID bạn đã chọn ở Bước 1. Tuyệt đối không được bỏ trống (null).
"""
        system_prompt = config["system_prompt"] + strict_rule
        
        print(f"Đang sinh {req.num_questions} câu cho {req.part} qua Hugging Face...")
        response = client.chat.completions.create(
            model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
            messages=[
                {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                {"role": "user", "content": system_prompt}
            ],
            max_tokens=8192,
            temperature=0.5
        )
        
        raw_text = response.choices[0].message.content.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()
            
        parsed_data = json.loads(raw_text)
        
        if isinstance(parsed_data, dict):
            parsed_data = [parsed_data]
            
        for item in parsed_data:
            item['hinh_anh'] = None

        return {"status": "success", "data": parsed_data}
        
    except Exception as e:
        print(f"Lỗi sinh đề chữ: {e}")
        return {"status": "error", "message": str(e)}


#=============================================================
#===     SINH ĐỀ CHO PART CÓ ĐOẠN VĂN BẢN           ==========
#=============================================================
@router.post("/generate-passage-questions")
async def generate_passage_questions_api(req: QuestionRequest):
    try:
        config = TOEIC_CONFIG.get(req.part)
        if not config:
            return {"status": "error", "message": "Part này chưa được hỗ trợ."}

        system_prompt = config["system_prompt"] + f"\n\nYêu cầu bổ sung: Hãy sinh ra CHÍNH XÁC {req.num_questions} đoạn văn. Mỗi đoạn văn đi kèm 3 câu hỏi."
        
        print(f"Đang sinh Đoạn văn + {req.num_questions} câu cho {req.part}...")
        response = client.chat.completions.create(
            model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
            messages=[
                {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                {"role": "user", "content": system_prompt}
            ],
            max_tokens=8192,
            temperature=0.5
        )
        
        raw_text = response.choices[0].message.content.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()
            
        parsed_data = json.loads(raw_text)
        
        return {"status": "success", "data": parsed_data}
        
    except Exception as e:
        print(f"Lỗi sinh đề chùm: {e}")
        return {"status": "error", "message": str(e)}


# =============================================================
# ===   HÀM KIỂM ĐỊNH TỰ ĐỘNG (LỚP 1 - VALIDATOR)            ===
# =============================================================
def validate_generated_batch(parsed_data, expected_count, is_passage=False):
    if not parsed_data or not isinstance(parsed_data, list):
        return False
        
    if len(parsed_data) != expected_count:
        return False
        
    for item in parsed_data:
        if is_passage:
            if 'doan_van' not in item or 'danh_sach_cau_hoi' not in item:
                return False
            questions = item['danh_sach_cau_hoi']
        else:
            questions = [item]
            
        for q in questions:
            if not all(k in q for k in ['cau_hoi', 'dap_an_a', 'dap_an_b', 'dap_an_c', 'dap_an_d', 'dap_an_dung']):
                return False
            if q['dap_an_dung'] not in ['A', 'B', 'C', 'D']:
                return False
    return True

# =============================================================
# ===   ORCHESTRATOR: SINH ĐỀ FULL 200 CÂU (CHẠY NGẦM)       ===
# =============================================================

async def build_200_questions_task(task_id: str, test_name: str, image_data_list: list):
    try:
        full_test_data = {}
        nova_tasks[task_id]["message"] = "Đang phân tích 6 ảnh để sinh Part 1..."
        full_test_data["part_1"] = []
        config_p1 = TOEIC_CONFIG.get("part_1")
        
        try:
            db_p1 = get_db_connection()
            cursor_p1 = db_p1.cursor(dictionary=True)
            cursor_p1.execute("SELECT id, ten_dang FROM danh_muc_dang_bai WHERE part = 'part_1'")
            danh_sach_dang = cursor_p1.fetchall()
            db_p1.close()
            menu_dang_bai = ", ".join([f"ID {r['id']}: '{r['ten_dang']}'" for r in danh_sach_dang])
            strict_rule = f"\nYÊU CẦU AUTO-TAGGING: Dựa vào nội dung bức ảnh, hãy tự phân tích xem nó thuộc dạng nào trong Menu dưới đây, và THÊM trường 'id_dang_bai' (chứa số ID tương ứng) vào JSON.\nMENU DẠNG BÀI HIỆN CÓ: [{menu_dang_bai}]."
            system_prompt_p1 = config_p1["system_prompt"] + strict_rule
        except Exception:
            system_prompt_p1 = config_p1["system_prompt"]

        for idx, img_info in enumerate(image_data_list):
            nova_tasks[task_id]["message"] = f"Đang phân tích ảnh {idx + 1}/6 cho Part 1..."
            try:
                image_bytes = img_info['data']
                encoded_string = base64.b64encode(image_bytes).decode('utf-8')
                img_data_uri = f"data:{img_info['mime_type']};base64,{encoded_string}"
                
                response = client.chat.completions.create(
                    model="lolzinventor/Qwen3.5-4B-Base-ZitGen-V1",
                    messages=[
                        {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": system_prompt_p1},
                                {"type": "image_url", "image_url": {"url": img_data_uri}}
                            ]
                        }
                    ],
                    max_tokens=2048,
                    temperature=0.5
                )
                raw_text = response.choices[0].message.content.strip()
                
                bt = "`" * 3
                if raw_text.startswith(bt + "json"): raw_text = raw_text[7:]
                elif raw_text.startswith(bt): raw_text = raw_text[3:]
                if raw_text.endswith(bt): raw_text = raw_text[:-3]
                raw_text = raw_text.strip()
                
                match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
                clean_json = match.group(1) if match else raw_text
                
                parsed_data = json.loads(clean_json)
                
                if isinstance(parsed_data, list) and len(parsed_data) > 0:
                    parsed_data[0]['hinh_anh'] = img_data_uri
                    full_test_data["part_1"].extend(parsed_data)
                elif isinstance(parsed_data, dict):
                    parsed_data['hinh_anh'] = img_data_uri
                    full_test_data["part_1"].append(parsed_data)
                    
            except Exception:
                pass
            
            await asyncio.sleep(2)

        toeic_structure = [
            {"part": "part_2", "total": 25, "batch_size": 5, "is_passage": False, "desc": "Part 2"},
            {"part": "part_3", "total": 13, "batch_size": 1, "is_passage": True, "desc": "Part 3"},
            {"part": "part_4", "total": 10, "batch_size": 1, "is_passage": True, "desc": "Part 4"},
            {"part": "part_5", "total": 30, "batch_size": 5, "is_passage": False, "desc": "Part 5"},
            {"part": "part_6", "total": 4,  "batch_size": 1, "is_passage": True, "desc": "Part 6"},
            {"part": "part_7", "total": 15, "batch_size": 1, "is_passage": True, "desc": "Part 7"}
        ]
        
        total_steps = len(toeic_structure) + 1
        
        for index, step in enumerate(toeic_structure):
            part_key = step["part"]
            full_test_data[part_key] = []
            
            total_needed = step["total"]
            batch_size = step["batch_size"]
            is_passage = step["is_passage"]
            
            num_batches = (total_needed + batch_size - 1) // batch_size 
            
            for b in range(num_batches):
                current_batch_size = batch_size
                if b == num_batches - 1 and total_needed % batch_size != 0:
                    current_batch_size = total_needed % batch_size
                
                nova_tasks[task_id]["message"] = f"Đang sinh {step['desc']}... ({b+1}/{num_batches})"
                nova_tasks[task_id]["progress"] = int(((index + 1 + (b / num_batches)) / total_steps) * 100)
                
                config = TOEIC_CONFIG.get(part_key)
                system_prompt = config["system_prompt"].replace("{num_questions}", str(current_batch_size))
                
                max_retries = 3
                batch_success = False
                
                for attempt in range(max_retries):
                    try:
                        response = client.chat.completions.create(
                            model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
                            messages=[
                                {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                                {"role": "user", "content": system_prompt}
                            ],
                            max_tokens=8192,
                            temperature=0.5
                        )
                        raw_text = response.choices[0].message.content.strip()
                        
                        match = re.search(r'(\[.*\]|\{.*\})', raw_text, re.DOTALL)
                        if match:
                            parsed_data = json.loads(match.group(1))
                            if validate_generated_batch(parsed_data, current_batch_size, is_passage):
                                full_test_data[part_key].extend(parsed_data)
                                batch_success = True
                                break
                    except Exception:
                        pass
                    
                    await asyncio.sleep(2)
                
                await asyncio.sleep(3)

        nova_tasks[task_id]["progress"] = 100
        nova_tasks[task_id]["status"] = "completed"
        
        try:
            db = get_db_connection()
            cursor = db.cursor()

            dap_an_json_string = json.dumps(full_test_data, ensure_ascii=False)
            import datetime
            nam_phat_hanh = datetime.date.today().year
            
            sql = "INSERT INTO de_thi_iig (ten_de, nguon_goc, nam_phat_hanh, dap_an_json) VALUES (%s, %s, %s, %s)"
            val = (test_name, 'ai_generated', nam_phat_hanh, dap_an_json_string)
            
            cursor.execute(sql, val)
            id_de_thi_moi = cursor.lastrowid

            db.commit() 
            db.close()
            
            nova_tasks[task_id]["message"] = f"Đã tạo và LƯU thành công đề '{test_name}' (ID: #{id_de_thi_moi}) vào hệ thống!"
        
        except Exception as db_err:
            nova_tasks[task_id]["status"] = "error"
            nova_tasks[task_id]["message"] = f"Lỗi khi lưu vào Database: {str(db_err)}"
            if 'db' in locals(): db.close()

    except Exception as e:
        nova_tasks[task_id]["status"] = "error"
        nova_tasks[task_id]["message"] = str(e)


@router.post("/generate-full-test")
async def generate_full_test_api(
    background_tasks: BackgroundTasks, 
    test_name: str = Form(...),
    images: List[UploadFile] = File(...)
):
    try:
        if len(images) != 6:
            return {"status": "error", "message": "Phải có đúng 6 ảnh cho Part 1."}
            
        task_id = str(uuid.uuid4())
        nova_tasks[task_id] = {"status": "processing", "progress": 0, "message": "Đang nạp dữ liệu ảnh..."}
        
        image_data_list = []
        for img in images:
            bytes_data = await img.read()
            image_data_list.append({
                "mime_type": img.content_type,
                "data": bytes_data
            })
            
        background_tasks.add_task(build_200_questions_task, task_id, test_name, image_data_list)
        
        return {
            "status": "success", 
            "task_id": task_id
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/task-status/{task_id}")
async def get_task_status_api(task_id: str):
    task = nova_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": "Không tìm thấy tiến trình sinh đề này."}
    
    if task["status"] in ["completed", "error"]:
        response_data = task.copy()
        del nova_tasks[task_id] 
        return {"status": "success", "data": response_data}
        
    return {"status": "success", "data": task}

#=============================================================
#===       LƯU BỘ CÂU HỎI AI SINH          ==========
#=============================================================
@router.post("/save-practice-questions")
async def save_practice_questions(questions: List[PracticeQuestion]):
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        sql = """INSERT INTO ngan_hang_cau_hoi 
                 (id_giao_vien, id_dang_bai, part, hinh_anh, doan_van, cau_hoi, dap_an_a, dap_an_b, dap_an_c, dap_an_d, dap_an_dung, giai_thich) 
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        
        for q in questions:
            val = (q.id_giao_vien, q.id_dang_bai, q.part, q.hinh_anh, q.doan_van_text, q.cau_hoi, 
                   q.dap_an_a, q.dap_an_b, q.dap_an_c, q.dap_an_d, q.dap_an_dung, q.giai_thich)
            cursor.execute(sql, val)

        if questions:
            id_gv = questions[0].id_giao_vien
            chi_tiet_log = f"Đã duyệt và lưu thành công {len(questions)} câu hỏi ôn luyện mới"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_CAU_HOI', %s)", (id_gv, chi_tiet_log))

        db.commit()
        cursor.close()
        db.close()
        
        return {"status": "success", "message": f"Đã lưu thành công {len(questions)} câu vào Ngân hàng Ôน luyện!"}
        
    except Exception as e:
        print(f"Lỗi lưu câu hỏi: {e}")
        return {"status": "error", "message": "Lỗi Database: " + str(e)}


@router.post("/delete-question/{q_id}")
async def delete_question(q_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id_giao_vien FROM ngan_hang_cau_hoi WHERE id = %s", (q_id,))
        gv_info = cursor.fetchone()
        
        cursor.execute("DELETE FROM ngan_hang_cau_hoi WHERE id = %s", (q_id,))
        
        if gv_info:
            chi_tiet_log = f"Đã xóa câu hỏi ID: #{q_id} khỏi Ngân hàng"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_CAU_HOI', %s)", (gv_info[0], chi_tiet_log))

        conn.commit()
        conn.close()
        return {"status": "success", "message": "Đã xóa câu hỏi thành công!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/update-question/{q_id}")
async def update_question(q_id: int, q: UpdateQuestionModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id_giao_vien FROM ngan_hang_cau_hoi WHERE id = %s", (q_id,))
        gv_info = cursor.fetchone()
        
        sql = """UPDATE ngan_hang_cau_hoi SET 
                 doan_van=%s, cau_hoi=%s, dap_an_a=%s, dap_an_b=%s, 
                 dap_an_c=%s, dap_an_d=%s, dap_an_dung=%s, giai_thich=%s 
                 WHERE id=%s"""
        val = (q.doan_van, q.cau_hoi, q.dap_an_a, q.dap_an_b, q.dap_an_c, 
               q.dap_an_d, q.dap_an_dung, q.giai_thich, q_id)
        
        cursor.execute(sql, val)
        
        if gv_info:
            chi_tiet_log = f"Đã cập nhật nội dung câu hỏi ID: #{q_id}"
            cursor.execute("INSERT INTO nhat_ky_hoat_dong (id_tai_khoan, vai_tro, loai_hanh_dong, chi_tiet) VALUES (%s, 'teacher', 'QUAN_LY_CAU_HOI', %s)", (gv_info[0], chi_tiet_log))

        conn.commit()
        conn.close()
        return {"status": "success", "message": "Cập nhật thành công!"}
    except Exception as e:
        print("LỖI SỬA CÂU HỎI:", e)
        return {"status": "error", "message": str(e)}


def run_llm_background(task_id: str, request: ExamRequest, chat_memory: list):
    try:
        diem_so_context = ""
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute("""
                SELECT part, diem_so, tong_so_cau, thoi_gian_nop 
                FROM lich_su_lam_bai 
                WHERE id_hoc_sinh = %s 
                ORDER BY thoi_gian_nop DESC LIMIT 5
            """, (request.user_id,))
            lich_su = cursor.fetchall()
            db.close()

            if lich_su:
                diem_so_context = "\n\n[THÔNG TIN BẢO MẬT: TIẾN ĐỘ HỌC TẬP CỦA HỌC SINH]\nĐây là kết quả 5 bài tập gần nhất học sinh này vừa làm:\n"
                for row in lich_su:
                    diem_so_context += f"- Dạng bài: {row['part'].upper()} | Điểm: {row['diem_so']}/{row['tong_so_cau']}.\n"
                diem_so_context += "-> Nếu học sinh hỏi về tình hình học tập, hãy DỰA VÀO NHỮNG ĐIỂM SỐ NÀY để khen ngợi, phân tích điểm yếu và đưa ra lộ trình ôn tập cá nhân hóa."
        except Exception as e:
            print("Lỗi lấy điểm:", e)

        base_prompt = f"""Bạn là Nova, Trợ giảng Tiếng Anh AI cá nhân cực kỳ tận tâm, thân thiện và kiên nhẫn. Bạn xưng "mình" và gọi học sinh là "bạn".
NHIỆM VỤ CỦA BẠN:
1. Giải đáp mọi thắc mắc về Từ vựng, Ngữ pháp, cấu trúc câu tiếng Anh một cách dễ hiểu nhất, luôn kèm theo ví dụ minh họa.
2. Theo dõi tiến độ và đưa ra lời khuyên học tập dựa trên điểm số thực tế.
3. TUYỆT ĐỐI KHÔNG tự ý sinh ra đề thi trắc nghiệm (Nếu học sinh yêu cầu làm bài tập, hãy nhẹ nhàng khuyên họ tắt khung chat và chọn các Part trên màn hình để làm bài).

QUY TẮC GIAO TIẾP TỐI THƯỢNG (BẮT BUỘC TUÂN THỦ): 
TUYỆT ĐỐI KHÔNG lặp lại lời chào (như "Chào bạn!", "Mình là Nova đây") ở đầu mỗi câu trả lời. Hãy đi thẳng trực tiếp vào vấn đề luôn để cuộc trò chuyện tự nhiên như người thật.
{diem_so_context}
"""

        messages = [{"role": "system", "content": base_prompt}]
        for m in request.history[:-1]:
            role = "user" if m.role == 'user' else "assistant"
            messages.append({"role": role, "content": m.content})
        messages.append({"role": "user", "content": request.yeu_cau})

        response = client.chat.completions.create(
            model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
            messages=messages,
            max_tokens=4096,
            temperature=0.7
        )
        result = response.choices[0].message.content

        current_session_id = request.session_id
        try:
            db = get_db_connection()
            cursor = db.cursor()
            if not current_session_id:
                title = request.yeu_cau[:40] + "..." if len(request.yeu_cau) > 40 else request.yeu_cau
                cursor.execute("INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s)", (request.user_id, title))
                current_session_id = cursor.lastrowid
            cursor.execute("INSERT INTO chat_messages (session_id, sender_type, content) VALUES (%s, %s, %s)", (current_session_id, 'user', request.yeu_cau))
            cursor.execute("INSERT INTO chat_messages (session_id, sender_type, content) VALUES (%s, %s, %s)", (current_session_id, 'nova', result))
            db.commit()
            cursor.close()
            db.close()
        except Exception:
            pass

        nova_tasks[task_id] = {"status": "done", "ket_qua": result, "session_id": current_session_id}
        
    except Exception as e:
        import traceback
        traceback.print_exc() 
        nova_tasks[task_id] = {"status": "error", "message": "Mình đang bị nghẽn mạng một chút, bạn đợi mình vài giây rồi hỏi lại nhé!"}


@router.post("/chat")
async def chat_with_nova(request: ExamRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    nova_tasks[task_id] = {"status": "processing"}

    chat_memory = []
    for msg in request.history[:-1]:
        if msg.role == 'user':
            chat_memory.append(HumanMessage(content=msg.content))
        else:
            chat_memory.append(AIMessage(content=msg.content))

    background_tasks.add_task(run_llm_background, task_id, request, chat_memory)

    return {"status": "processing", "task_id": task_id}


@router.get("/check-task/{task_id}")
async def check_task(task_id: str):
    if task_id in nova_tasks:
        return nova_tasks[task_id]
    return {"status": "not_found"}


@router.post("/reset")
async def reset_nova():
    global global_vectorstore, uploaded_docs_cache
    uploaded_docs_cache.clear() 
    global_vectorstore = None   
    return {"status": "success"}


@router.post("/generate-full-test-stream")
async def generate_full_test_stream_api(
    test_name: str = Form(...),
    images: List[UploadFile] = File(...)
):
    async def event_generator():
        yield " " * 1024 + "\n"
        
        yield f"[*] Đã nhận lệnh tạo đề: {test_name}\n"
        try:
            if len(images) != 6:
                yield "❌ LỖI: Phải có đúng 6 ảnh cho Part 1!\n"
                return
                
            yield "[*] Đang đọc dữ liệu 6 bức ảnh...\n"
            image_data_list = []
            for img in images:
                bytes_data = await img.read()
                image_data_list.append({"mime_type": img.content_type, "data": bytes_data})
            
            full_test_data = {}
            
            # --- XỬ LÝ PART 1 ---
            yield "\n[========== ĐANG SINH PART 1 ==========]\n"
            full_test_data["part_1"] = []
            config_p1 = TOEIC_CONFIG.get("part_1")
            system_prompt_p1 = config_p1["system_prompt"]
            
            for idx, img_info in enumerate(image_data_list):
                yield f" -> Đang xử lý ảnh {idx + 1}/6... "
                try:
                    image_bytes = img_info['data']
                    encoded_string = base64.b64encode(image_bytes).decode('utf-8')
                    img_data_uri = f"data:{img_info['mime_type']};base64,{encoded_string}"
                    
                    response = client.chat.completions.create(
                        model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
                        messages=[
                            {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": system_prompt_p1},
                                    {"type": "image_url", "image_url": {"url": img_data_uri}}
                                ]
                            }
                        ],
                        max_tokens=2048,
                        temperature=0.5
                    )
                    raw_text = response.choices[0].message.content.strip()
                    match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
                    clean_json = match.group(1) if match else raw_text
                    parsed_data = json.loads(clean_json)
                    
                    if isinstance(parsed_data, list) and len(parsed_data) > 0:
                        parsed_data[0]['hinh_anh'] = img_data_uri
                        full_test_data["part_1"].extend(parsed_data)
                    yield "[OK]\n"
                except Exception as e:
                    yield f"[LỖI: {str(e)}]\n"
                await asyncio.sleep(2)
                
            # --- XỬ LÝ PART 2 ĐẾN PART 7 ---
            toeic_structure = [
                {"part": "part_2", "total": 25, "batch_size": 5, "is_passage": False, "desc": "Part 2"},
                {"part": "part_3", "total": 13, "batch_size": 1, "is_passage": True, "desc": "Part 3"},
                {"part": "part_4", "total": 10, "batch_size": 1, "is_passage": True, "desc": "Part 4"},
                {"part": "part_5", "total": 30, "batch_size": 5, "is_passage": False, "desc": "Part 5"},
                {"part": "part_6", "total": 4,  "batch_size": 1, "is_passage": True, "desc": "Part 6"},
                {"part": "part_7", "total": 15, "batch_size": 1, "is_passage": True, "desc": "Part 7"}
            ]

            for step in toeic_structure:
                part_key = step["part"]
                yield f"\n[========== ĐANG SINH {step['desc'].upper()} ==========]\n"
                full_test_data[part_key] = []
                
                total_needed = step["total"]
                batch_size = step["batch_size"]
                num_batches = (total_needed + batch_size - 1) // batch_size 
                config = TOEIC_CONFIG.get(part_key)
                
                for b in range(num_batches):
                    current_batch_size = batch_size
                    if b == num_batches - 1 and total_needed % batch_size != 0:
                        current_batch_size = total_needed % batch_size
                        
                    yield f" -> Đang chạy mẻ {b+1}/{num_batches} (sinh {current_batch_size} câu)... "
                    system_prompt = config["system_prompt"].replace("{num_questions}", str(current_batch_size))
                    
                    batch_success = False
                    for attempt in range(3):
                        try:
                            response = client.chat.completions.create(
                                model="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF",
                                messages=[
                                    {"role": "system", "content": "You are a professional TOEIC test generator. Output valid JSON only."},
                                    {"role": "user", "content": system_prompt}
                                ],
                                max_tokens=8192,
                                temperature=0.5
                            )
                            raw_text = response.choices[0].message.content.strip()
                            match = re.search(r'(\[.*\]|\{.*\})', raw_text, re.DOTALL)
                            if match:
                                parsed_data = json.loads(match.group(1))
                                if validate_generated_batch(parsed_data, current_batch_size, step["is_passage"]):
                                    full_test_data[part_key].extend(parsed_data)
                                    batch_success = True
                                    break
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                    
                    if batch_success:
                        yield "[OK]\n"
                    else:
                        yield "[THẤT BẠI - Đã bỏ qua mẻ này]\n"
                    await asyncio.sleep(3)

            # --- LƯU DATABASE ---
            yield "\n[*] Đang tiến hành lưu toàn bộ đề vào Database...\n"
            try:
                db = get_db_connection()
                cursor = db.cursor()
                dap_an_json_string = json.dumps(full_test_data, ensure_ascii=False)
                import datetime
                nam_phat_hanh = datetime.date.today().year
                
                sql = "INSERT INTO de_thi_iig (ten_de, nguon_goc, nam_phat_hanh, dap_an_json) VALUES (%s, %s, %s, %s)"
                val = (test_name, 'ai_generated', nam_phat_hanh, dap_an_json_string)
                cursor.execute(sql, val)
                id_de_thi = cursor.lastrowid
                db.commit() 
                db.close()
                yield f"✅ [THÀNH CÔNG] Đã lưu xong đề thi! (ID mới: #{id_de_thi})\n\n[=== TIẾN TRÌNH KẾT THÚC HOÀN TOÀN ===]"
            except Exception as db_err:
                yield f"❌ [LỖI DATABASE] {str(db_err)}\n"
                
        except Exception as e:
            yield f"\n❌ [LỖI HỆ THỐNG NGHIÊM TRỌNG] {str(e)}\n"

    return StreamingResponse(event_generator(), media_type="text/plain")