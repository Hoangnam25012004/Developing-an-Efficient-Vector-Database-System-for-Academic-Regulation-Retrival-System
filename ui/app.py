"""
ui/app.py — Academic Regulation Chatbot UI
Giao diện chatbot hỏi-đáp quy chế học vụ.
"""

import sys
from pathlib import Path
from datetime import datetime

import requests
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─── Page config (MUST be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title="ĐHQT | Hỏi Đáp Quy Chế",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Ẩn hoàn toàn thanh toolbar Streamlit (Deploy, menu, ...) */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
.stDeployButton { display: none !important; }
[data-testid="stHeader"] { display: none !important; }
[data-testid="stToolbar"] { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }

.stApp { background: #eef2f7 !important; }
.block-container { padding-top: 1.2rem !important; padding-bottom: 0.5rem !important; max-width: 900px; }

/* Input */
.stTextInput > div > div > input {
    border-radius: 12px !important;
    border: 1.5px solid #d1dce8 !important;
    font-size: 15px !important;
    padding: 10px 14px !important;
    transition: border-color 0.2s !important;
}
.stTextInput > div > div > input:focus {
    border-color: #1a3a5c !important;
    box-shadow: 0 0 0 3px rgba(26,58,92,0.08) !important;
}

/* Send button - target only the row containing the input */
div[data-testid="stHorizontalBlock"]:has(.stTextInput) .stButton > button {
    background: linear-gradient(135deg, #1a3a5c 0%, #2a5a9c 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    font-size: 15px !important;
    height: 46px !important;
    box-shadow: 0 3px 10px rgba(26,58,92,0.3) !important;
    transition: all 0.2s !important;
}
div[data-testid="stHorizontalBlock"]:has(.stTextInput) .stButton > button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 5px 16px rgba(26,58,92,0.4) !important;
}

/* Suggestion chips row */
div[data-testid="stHorizontalBlock"]:not(:has(.stTextInput)) .stButton > button {
    background: #ffffff !important;
    color: #1e40af !important;
    border: 1.5px solid #bfdbfe !important;
    border-radius: 20px !important;
    font-weight: 500 !important;
    font-size: 12.5px !important;
    padding: 5px 8px !important;
    height: auto !important;
    box-shadow: none !important;
    white-space: normal !important;
    text-align: center !important;
    line-height: 1.4 !important;
}
div[data-testid="stHorizontalBlock"]:not(:has(.stTextInput)) .stButton > button:hover {
    background: #1a3a5c !important;
    color: white !important;
    border-color: #1a3a5c !important;
    transform: none !important;
    box-shadow: none !important;
}

/* Sidebar */
[data-testid="stSidebar"] { background: #0d2540 !important; }
[data-testid="stSidebar"] * { color: #c8d9ea !important; }
[data-testid="stSidebar"] h3 {
    color: #fff !important; font-size: 12px !important; font-weight: 700 !important;
    text-transform: uppercase !important; letter-spacing: 1.2px !important;
    margin-top: 18px !important; margin-bottom: 6px !important;
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label { font-size: 12px !important; color: #8ba8c4 !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.1) !important; margin: 10px 0 !important; }
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
    background: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.15) !important;
    border-radius: 10px !important;
}
[data-testid="stSidebar"] .stTextInput > div > div > input {
    background: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.15) !important;
    color: #c8d9ea !important; font-size: 12px !important; border-radius: 10px !important;
}
[data-testid="stSidebar"] .stButton > button {
    background: rgba(255,255,255,0.1) !important; color: #c8d9ea !important;
    border: 1px solid rgba(255,255,255,0.2) !important; border-radius: 10px !important;
    font-size: 13px !important; height: auto !important; box-shadow: none !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(255,255,255,0.18) !important; transform: none !important;
    box-shadow: none !important;
}
</style>
""", unsafe_allow_html=True)

# ─── Session state ────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:20px 0 14px;">
        <div style="font-size:42px;line-height:1;">🎓</div>
        <div style="font-size:15px;font-weight:700;color:#fff;margin-top:8px;">ĐHQT Chatbot</div>
        <div style="font-size:12px;color:#8ba8c4;margin-top:3px;">Hỏi đáp quy chế học vụ</div>
        <div style="display:flex;align-items:center;justify-content:center;gap:6px;margin-top:8px;">
            <div style="width:7px;height:7px;background:#5dd89e;border-radius:50%;
                box-shadow:0 0 5px #5dd89e;"></div>
            <span style="font-size:11px;color:#5dd89e;font-weight:600;">Trực tuyến</span>
        </div>
    </div><hr/>
    """, unsafe_allow_html=True)

    st.markdown("### ⚙️ Phương thức")
    mode = st.selectbox(
        "mode", label_visibility="collapsed",
        options=["hybrid_rerank", "hybrid", "dense", "sparse"], index=0,
        format_func=lambda m: {
            "hybrid_rerank": "🏆 Hybrid + Rerank",
            "hybrid":        "🔀 Hybrid (Dense+BM25)",
            "dense":         "🧠 Dense (Semantic)",
            "sparse":        "🔍 Sparse (BM25)",
        }[m],
    )
    st.markdown("### 📊 Tham số")
    top_k  = st.slider("Số kết quả", 1, 20, 5)
    show_k = st.slider("Trích dẫn hiển thị", 1, min(top_k, 5), min(3, top_k))
    st.markdown("### 🌐 API")
    api_url = st.text_input("url", value="http://localhost:8000/search", label_visibility="collapsed")

    st.markdown("<hr/>", unsafe_allow_html=True)
    tips = {
        "hybrid_rerank": "Dense+BM25 → Cross-Encoder. Chính xác nhất.",
        "hybrid":        "RRF kết hợp Dense & BM25. Cân bằng tốc độ & độ chính xác.",
        "dense":         "Tìm kiếm ngữ nghĩa (vector). Tốt với câu tự nhiên.",
        "sparse":        "BM25 keyword matching. Nhanh, tốt với từ khoá.",
    }
    st.markdown(f"""<div style="background:rgba(255,255,255,0.06);border-radius:10px;padding:10px 12px;
        font-size:12px;color:#8ba8c4;line-height:1.55;">💡 {tips[mode]}</div>""",
        unsafe_allow_html=True)

    st.markdown("<hr/>", unsafe_allow_html=True)
    if st.button("🗑️  Xoá lịch sử hội thoại", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.markdown("""<div style="font-size:11px;color:#3a5570;text-align:center;margin-top:16px;line-height:1.6;">
        multilingual-e5-base · bge-reranker-v2-m3<br/>BM25 vi_basic · Qdrant</div>""",
        unsafe_allow_html=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def now_str() -> str:
    return datetime.now().strftime("%H:%M")


def fetch_hits(query: str) -> tuple[str, list]:
    try:
        res = requests.get(api_url, params={"query": query, "mode": mode, "top_k": top_k}, timeout=60)
        res.raise_for_status()
        hits = res.json()
    except Exception as exc:
        return f"❌ Không thể kết nối API: <code>{exc}</code>", []
    if not hits:
        return "Xin lỗi, tôi không tìm thấy thông tin liên quan trong cơ sở dữ liệu quy chế.", []
    top  = hits[0]
    path = " › ".join(top.get("path_hierarchy") or [])
    text = (top.get("text") or "").strip().replace("\n", "<br/>")
    answer = (f'<span style="color:#5a7fa0;font-size:12.5px;font-style:italic;">📌 {path}</span>'
              f'<br/><br/>{text}') if path else text
    return answer, hits


def source_chips_html(hits: list) -> str:
    if not hits:
        return ""
    chips = "".join(
        f'<span style="display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;'
        f'border-radius:20px;padding:3px 10px;font-size:11.5px;font-weight:500;margin:2px 3px 2px 0;">'
        f'📄 {(h.get("path_hierarchy") or [h.get("doc_id","?")])[-1][:36]} — {h.get("score",0):.3f}</span>'
        for h in hits[:show_k]
    )
    return (f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #e2e8f0;">'
            f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
            f'letter-spacing:.8px;margin-bottom:5px;">📚 Nguồn tham khảo</div>{chips}</div>')


def build_chat_html() -> str:
    msgs = st.session_state.messages

    if not msgs:
        body = """
        <div style="text-align:center;padding:52px 20px 44px;color:#94a3b8;">
            <div style="font-size:56px;margin-bottom:14px;">🏫</div>
            <div style="font-size:17px;font-weight:700;color:#3a5a80;margin-bottom:8px;">
                Xin chào! Tôi là trợ lý quy chế học vụ.</div>
            <div style="font-size:13.5px;line-height:1.75;color:#7a9ab8;">
                Bạn có thể hỏi tôi về:<br/>
                <strong style="color:#4a7aa0;">học phí · miễn giảm · điểm rèn luyện · khen thưởng</strong><br/>
                kỷ luật · ban cán sự lớp · sinh viên khuyết tật · và nhiều hơn nữa.
            </div>
        </div>"""
    else:
        rows = []
        for msg in msgs:
            role    = msg["role"]
            content = msg["content"]
            ts      = msg.get("time", "")
            sources = msg.get("sources", [])
            if role == "user":
                rows.append(f"""
                <div style="display:flex;align-items:flex-end;gap:10px;flex-direction:row-reverse;margin-bottom:16px;">
                    <div style="width:36px;height:36px;border-radius:50%;
                        background:linear-gradient(135deg,#e8a020,#f4c555);
                        display:flex;align-items:center;justify-content:center;
                        font-size:17px;flex-shrink:0;box-shadow:0 2px 8px rgba(232,160,32,.3);">👤</div>
                    <div style="max-width:68%;padding:12px 16px;
                        background:linear-gradient(135deg,#1a3a5c,#2a5a9c);
                        border-radius:18px 18px 4px 18px;font-size:14.5px;line-height:1.6;color:#fff;">
                        {content}
                        <div style="font-size:11px;color:rgba(255,255,255,.5);margin-top:5px;text-align:right;">{ts}</div>
                    </div>
                </div>""")
            else:
                rows.append(f"""
                <div style="display:flex;align-items:flex-end;gap:10px;margin-bottom:16px;">
                    <div style="width:36px;height:36px;border-radius:50%;
                        background:linear-gradient(135deg,#1a3a5c,#2a5a9c);
                        display:flex;align-items:center;justify-content:center;
                        font-size:17px;flex-shrink:0;box-shadow:0 2px 8px rgba(26,58,92,.3);">🎓</div>
                    <div style="max-width:76%;padding:13px 16px;background:#f0f4f8;
                        border-radius:18px 18px 18px 4px;font-size:14.5px;line-height:1.65;
                        color:#1e2d3d;border:1px solid #e2e8f0;">
                        {content}
                        {source_chips_html(sources)}
                        <div style="font-size:11px;color:#94a3b8;margin-top:5px;">{ts}</div>
                    </div>
                </div>""")
        body = "\n".join(rows)

    return f"""<!DOCTYPE html><html><head>
    <meta charset="utf-8"/>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>
    <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', sans-serif; background: transparent; }}
    #chat {{
        background: #ffffff; border-radius: 16px;
        padding: 20px 18px 16px;
        min-height: 420px; max-height: 520px;
        overflow-y: auto;
        box-shadow: 0 2px 12px rgba(0,0,0,.06);
        display: flex; flex-direction: column;
    }}
    #chat::-webkit-scrollbar {{ width: 5px; }}
    #chat::-webkit-scrollbar-track {{ background: transparent; }}
    #chat::-webkit-scrollbar-thumb {{ background: #cbd5e0; border-radius: 4px; }}
    </style>
    </head><body>
    <div id="chat">{body}</div>
    <script>
    const c = document.getElementById('chat');
    if (c) c.scrollTop = c.scrollHeight;
    </script>
    </body></html>"""


# ─── Main layout ──────────────────────────────────────────────────────────────

# Header
st.markdown("""
<div style="background:linear-gradient(135deg,#1a3a5c 0%,#0d2540 100%);border-radius:16px;
    padding:18px 26px;display:flex;align-items:center;gap:16px;margin-bottom:10px;
    box-shadow:0 4px 20px rgba(26,58,92,.28);">
    <div style="width:52px;height:52px;background:linear-gradient(135deg,#e8a020,#f4c555);
        border-radius:13px;display:flex;align-items:center;justify-content:center;
        font-size:26px;box-shadow:0 2px 8px rgba(232,160,32,.4);flex-shrink:0;">🎓</div>
    <div>
        <div style="font-size:19px;font-weight:700;color:#fff;letter-spacing:.3px;">
            Trợ lý Quy Chế Học Vụ</div>
        <div style="font-size:12.5px;color:#a8c4e0;margin-top:2px;">
            Đại học Quốc tế — Đại học Quốc gia TP.HCM</div>
    </div>
    <div style="margin-left:auto;display:flex;align-items:center;gap:7px;">
        <div style="width:8px;height:8px;background:#5dd89e;border-radius:50%;box-shadow:0 0 6px #5dd89e;"></div>
        <span style="font-size:12px;color:#5dd89e;font-weight:600;">Trực tuyến</span>
    </div>
</div>
""", unsafe_allow_html=True)

# Chat window via components.html (full HTML document, no Streamlit interference)
components.html(build_chat_html(), height=560, scrolling=False)

# Suggestion chips (only when empty)
SUGGESTIONS = [
    "Miễn học phần tiếng Anh?",
    "Điểm rèn luyện xuất sắc?",
    "Quy trình xét miễn giảm HP?",
    "Ban cán sự lớp gồm ai?",
    "Buộc thôi học khi nào?",
]
if not st.session_state.messages:
    cols = st.columns(len(SUGGESTIONS))
    for col, sug in zip(cols, SUGGESTIONS):
        with col:
            if st.button(sug, key=f"sug_{sug}", use_container_width=True):
                st.session_state.pending_query = sug
                st.rerun()

# Input bar
st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
c1, c2 = st.columns([6, 1])
with c1:
    user_input = st.text_input(
        "msg", placeholder="Nhập câu hỏi về quy chế học vụ…",
        label_visibility="collapsed", key="chat_input",
    )
with c2:
    send = st.button("Gửi ➤", use_container_width=True)

# Process query
query = None
if send and user_input.strip():
    query = user_input.strip()
elif st.session_state.pending_query:
    query = st.session_state.pending_query
    st.session_state.pending_query = None

if query:
    st.session_state.messages.append({"role": "user", "content": query, "sources": [], "time": now_str()})
    with st.spinner("Đang tìm kiếm…"):
        answer, hits = fetch_hits(query)
    st.session_state.messages.append({"role": "bot", "content": answer, "sources": hits, "time": now_str()})
    st.rerun()
