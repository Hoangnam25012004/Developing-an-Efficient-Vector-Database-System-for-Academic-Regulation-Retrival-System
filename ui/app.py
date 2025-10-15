import requests
import streamlit as st

st.set_page_config(page_title="Reg Retrieval", layout="wide")

st.title("📚 Academic Regulation Retrieval")

query = st.text_input("Câu hỏi / từ khóa")
mode = st.selectbox("Chế độ", ["hybrid_rerank", "hybrid", "dense", "sparse"], index=0)
api_url = st.text_input("API URL", value="http://localhost:8000/search")

col1, col2 = st.columns([1, 1])
with col1:
    top_k = st.slider("top_k", 1, 50, 10)

if st.button("Tìm kiếm", use_container_width=True):
    if not query:
        st.warning("Nhập truy vấn đã!")
    else:
        with st.spinner("Đang tìm…"):
            try:
                res = requests.get(
                    api_url,
                    params={"query": query, "mode": mode, "top_k": top_k},
                    timeout=60,
                )
                res.raise_for_status()
                hits = res.json()
            except Exception as e:
                st.error(f"API lỗi: {e}")
                hits = []
        for i, h in enumerate(hits, start=1):
            with st.expander(f"#{i} • {h.get('doc_id')} • score={h.get('score'):.4f}"):
                ph = " › ".join(h.get("path_hierarchy", []) or ["(no path)"])
                st.caption(ph)
                st.write(h.get("text"))
                meta_cols = st.columns(5)
                meta_cols[0].markdown(f"**Điều:** {h.get('article_no')}")
                meta_cols[1].markdown(f"**Khoản:** {h.get('clause_no')}")
                meta_cols[2].markdown(f"**Điểm:** {h.get('point')}")
                meta_cols[3].markdown(f"**Phiên bản:** {h.get('version')}")
                meta_cols[4].markdown(f"**Hiệu lực:** {h.get('effective_date')}")
