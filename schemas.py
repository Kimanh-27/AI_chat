from pydantic import BaseModel
from typing import List, Optional, Any


class PracticeQuestion(BaseModel):
    part: str
    id_giao_vien: int
    id_dang_bai: Optional[int] = None
    hinh_anh: Optional[str] = None
    doan_van_text: Optional[str] = None
    cau_hoi: str
    dap_an_a: str   
    dap_an_b: str   
    dap_an_c: str  
    dap_an_d: Optional[str] = "" 
    dap_an_dung: str
    giai_thich: Optional[str] = ""

class UpdateQuestionModel(BaseModel):
    part: Optional[str] = None
    hinh_anh: Optional[str] = None
    doan_van: Optional[str] = None
    cau_hoi: str
    dap_an_a: str   
    dap_an_b: str   
    dap_an_c: str  
    dap_an_d: Optional[str] = "" 
    dap_an_dung: str
    giai_thich: Optional[str] = ""

class QuestionRequest(BaseModel):
    part: str
    num_questions: int

class HistoryData(BaseModel):
    id_hoc_sinh: int
    part: str
    diem_so: int
    tong_so_cau: int
    chi_tiet_bai_lam: str
    id_bai_tap: Optional[int] = None

class HomeworkData(BaseModel):
    ten_bai_tap: str
    id_giao_vien: int
    id_lop: int
    part: str
    so_luong_cau: int
    han_nop: str
    thoi_gian_lam_bai: int

# =============================================================
# MODEL DỮ LIỆU CHO API SINH ĐỀ & GIAO BÀI CÁ NHÂN CHO HỌC SINH
# =============================================================
class RemedialGenRequest(BaseModel):
    parts: list 
    focus_topic: str

class SaveAndAssignRequest(BaseModel):
    id_giao_vien: int
    id_lop: int
    id_hoc_sinh: Optional[int] = None
    part: str
    ten_bai_tap: str
    questions: list

class DeleteHomeworkRequest(BaseModel):
    ids: List[int]

class SaveExamModel(BaseModel):
    tieu_de: str
    noi_dung: str
    user_id: int = 1

class ChatMessage(BaseModel):
    role: str
    content: str

class ExamRequest(BaseModel):
    yeu_cau: str
    history: List[ChatMessage] = []
    user_id: int = 1 
    session_id: Optional[int] = None 

class IIGSubmitData(BaseModel):
    id_hoc_sinh: int
    id_de_thi: int
    dap_an_hoc_sinh: list

class UpdateAnswersModel(BaseModel):
    dap_an_json: str

class AccountModel(BaseModel):
    vai_tro: str
    email: Optional[str] = ""
    id_lop: Optional[int] = None
    ten_dang_nhap: Optional[str] = ""
    mat_khau: Optional[str] = ""
    ten_hoc_sinh: Optional[str] = ""
    ngay_sinh: Optional[str] = ""
    so_dien_thoai: Optional[str] = ""

class ClassModel(BaseModel):
    ten_lop: str
    id_giao_vien: int
    ma_tham_gia: str

class UpdateProfileModel(BaseModel):
    vai_tro: str 
    email: Optional[str] = ""
    mat_khau_cu: Optional[str] = ""
    mat_khau_moi: Optional[str] = ""
    ten_hoc_sinh: Optional[str] = ""
    ngay_sinh: Optional[str] = ""
    so_dien_thoai: Optional[str] = ""

class ThongBaoLopModel(BaseModel):
    id_lop: int
    tieu_de: str
    noi_dung: str


class ChuDeModel(BaseModel):
    chu_de: str
    id_giao_vien: int

class TuVungModel(BaseModel):
    id_chu_de: int
    id_giao_vien: int
    tu: str
    phien_am: Optional[str] = ""
    nghia: str
    vi_du: Optional[str] = ""
    tu_dong_nghia: Optional[str] = ""

class BulkTuVungModel(BaseModel):
    id_chu_de: int
    id_giao_vien: int
    danh_sach_tu: List[TuVungModel]


class LogViewRequest(BaseModel):
    id_hoc_sinh: int
    id_tai_lieu: int


class SaveAITestRequest(BaseModel):
    test_name: str
    dap_an_json: str

class PracticeAudioRequest(BaseModel):
    part: str
    questions: List[Any]

# =============================================================
# MODEL DỮ LIỆU CHO API QUẢN LÝ TÀI LIỆU HỌC (NGỮ PHÁP)
# =============================================================
class GenerateQuizRequest(BaseModel):
    noi_dung: str
    so_luong: str = "2-3"

class StudentLogRequest(BaseModel):
    id_hoc_sinh: int
    id_tai_lieu: int
    trang_thai: str # Nhận 2 giá trị: "start" hoặc "completed"

class TaiLieuHocModel(BaseModel):
    id: Optional[int] = None
    tieu_de: str
    loai_tai_lieu: str 
    tags: str          
    noi_dung: str
    file_goc: Optional[str] = None
    id_nguoi_tao: int = None
    bai_tap_json: Optional[str] = None
