# Detail Design — Sửa lỗi reranker trùng lặp, lỗi "Qdrant offline" và phát hành mã nguồn lên GitHub

| Mục | Nội dung |
|---|---|
| Trạng thái | **Đã duyệt (CP1) và đang triển khai (2026-09-26).** Kết quả kiểm thử ở Phụ lục D; các điểm điều chỉnh so với bản thiết kế ở Phụ lục E. |
| Ngày | 2026-09-26 |
| Phạm vi | **F1:** kết quả rerank chứa chunk lặp (`src/retrieval/reranker.py`). **F2:** thanh trạng thái luôn báo "Qdrant offline" (`api/main.py`, `/health`). **R:** phát hành mã nguồn công khai, bản build production (Docker) và CI. Kèm theo: đo lại Bảng II trong môi trường cô lập. |
| Không đụng tới | Parser/chunker, `_dedup`, RRF, model và mọi tham số trong `config.yaml`, logic chọn tier, `clause_assembler.py`, luồng upload/xoá/reindex, `/config`, `/evaluate`, giao diện `ui/index.html`, file paper. |
| Quy ước số | Số liệu viết theo định dạng của paper và file JSON (dấu chấm thập phân, ví dụ 0.885) để đối chiếu trực tiếp. |
| Lưu ý | Tài liệu này nằm trong `docs/`, tức sẽ có trong repo public. |

---

## 0. Tóm tắt

**F1 — reranker chèn lại chunk đã chọn.** Lượt 2 của `_diversify` so dict gốc với các bản sao đã gắn `_score_rerank`. Không dict gốc nào bằng bản sao, nên điều kiện "chưa được chọn" luôn đúng và các chunk điểm cao nhất bị thêm lại. Lỗi chỉ xảy ra khi lượt 1 (tối đa 5 chunk mỗi văn bản) chọn được ít hơn 10 chunk. Dữ liệu pool của GT v2 cho thấy điều này xảy ra ở **9/100 câu hỏi**, tổng cộng 28 vị trí lặp (đo ở E1). Với 91 câu còn lại, bản sửa cho ra **kết quả giống hệt** (chứng minh ở mục 3.2). Cách sửa: lượt 2 bỏ qua các vị trí đã chọn.

**F2 — "Qdrant offline" giả.** Giao diện đọc `d.qdrant`, nhưng `/health` không trả về field này. Sửa phía API: thêm field `qdrant`, giá trị lấy từ một lần kiểm tra thật tới server và collection. Giao diện giữ nguyên.

**R — phát hành.** Theo quyết định Q1, chỉ public mã nguồn, không host. Repo GitHub nhận một chuỗi commit mới nối tiếp `main` hiện có, **không force-push**:

1. code đúng như lúc đo paper (tag `kse2026-paper`);
2. Upload v2;
3. hai bản sửa F1, F2;
4. gói production: Docker Compose, image trên GHCR, CI, license (tag `v2.0.1`).

`Report/` không được công khai, kể cả trong lịch sử git. License: MIT cho code, CC BY 4.0 cho test collection.

**Paper giữ nguyên.** Sau khi sửa F1, những số sau **chắc chắn không đổi** (chứng minh ở mục 3.2): HR@1 = 0.885, kiểm định McNemar, khoảng tin cậy bootstrap và bốn hàng đầu của Bảng II. Những số có thể đổi: các ô HR@5, MRR@10, nDCG@10 của hàng hybrid-rerank, và số liệu nhánh baseline ở mục V-F. Các số này được đo lại trong một môi trường cô lập tái lập đúng corpus của paper; kết quả ở Phụ lục D (theo Q7, README không có mục đính chính).

**Chỉ mục đang chạy có thể đổi bất cứ lúc nào.** Ngay trong lúc khảo sát, `reg_chunks` trên máy phát triển có **4.313 point**: một tài liệu thử (89 chunk) được upload qua giao diện lúc 14:21 rồi bị xoá trước khi khảo sát xong. Cuối buổi khảo sát, chỉ mục đã về đúng corpus của paper: 4.224 point, tập id trùng khớp 4.224 `chunk_id` trong git, và `bm25.pkl` giống từng byte bản trong git. Vì vậy mọi phép đo và mọi thứ được phát hành đều lấy corpus từ git (34 văn bản, 4.224 chunk) và chạy trên môi trường cô lập, không phụ thuộc chỉ mục đang chạy.

---

## 1. Mục tiêu, phạm vi, quyết định

### 1.1 Mục tiêu

| ID | Mục tiêu |
|---|---|
| MT-1 | Kết quả rerank không còn chunk lặp, đúng như paper mô tả ("with redundancy removal", III-C). |
| MT-2 | Thanh trạng thái báo đúng: Qdrant chạy và có collection → "System online"; Qdrant tắt hoặc thiếu collection → "Qdrant offline"; API tắt → "API offline". |
| MT-3 | Ai cũng clone và chạy được bản production bằng `docker compose up -d` (Linux/Windows/macOS có Docker), và nhận kết quả giống môi trường của paper. |
| MT-4 | Số liệu trong paper tái lập được (tag `kse2026-paper`); số liệu sau khi sửa được ghi lại ở Phụ lục D. |
| MT-5 | Không hồi quy chức năng khác; không công khai nội dung ngoài phạm vi được phép. |

### 1.2 Quyết định đã chốt (2026-09-26)

| # | Chủ đề | Quyết định | Hệ quả trong thiết kế |
|---|---|---|---|
| Q0 | Hai lỗi | Sửa cả F1 và F2; chạy lại ablation để có số chính xác | Mục 4, 5, 6 |
| Q1 | Nơi chạy | Chỉ public mã nguồn: repo, image Docker trên GHCR và hướng dẫn tự chạy. Không có URL chạy sẵn. | Không thuê máy chủ, không cấu hình domain hay HTTPS |
| Q2 | Chức năng quản trị | Giữ API mở như hiện tại (không đăng nhập) | Không sửa app. README ghi cảnh báo. Compose mặc định chỉ nghe `127.0.0.1` (D4) |
| Q3 | Nội dung công khai | Mã nguồn, tests, docs, `data/` (34 PDF và chỉ mục), `eval/` (test collection và script). **Không** công khai `Report/`. | Allowlist ở mục 7.1; lịch sử public không chứa `Report/` |
| Q4 | Paper | Giữ nguyên paper; gắn tag cho code của paper (phần đính chính được điều chỉnh bởi Q7) | Mục 3.3 |
| Q5 | License | MIT cho code; CC BY 4.0 cho test collection | `LICENSE`, `eval/LICENSE` |
| Q6 | Giao diện (chốt khi triển khai) | Sửa `API_BASE` để UI gọi API ở chính địa chỉ đã phục vụ trang | `ui/index.html` (E-6) |
| Q7 | Đính chính (chốt khi triển khai) | Không ghi đính chính vào README | Mục 3.3; số liệu chỉ ở Phụ lục D |
| Q8 | Commit public (chốt khi triển khai) | Bỏ dòng đồng tác giả khỏi message của commit public; lịch sử local giữ nguyên | Mục 7.2 |
| Q9 | Gán nhãn thêm (chốt khi triển khai) | Không; giữ quy ước "unjudged = non-relevant" của paper | Mục 6.5 |

### 1.3 Mặc định do thiết kế chọn (có thể đổi khi duyệt)

| # | Mặc định | Lý do |
|---|---|---|
| D1 | Sửa F2 phía API (thêm field), không sửa UI | UI vốn đã đọc `d.qdrant`. Đây là thay đổi nhỏ nhất và chỉ **thêm** field. |
| D2 | F1 đánh dấu chunk đã chọn theo **vị trí** trong danh sách đã chấm điểm | `_dedup` chạy trước nên mỗi vị trí là một chunk khác nhau; cách này không phụ thuộc chunk có `chunk_id` hay không. |
| D3 | Model được tải khi container khởi động lần đầu, khoá đúng revision đã dùng cho paper. Model **không** được nhúng vào image. | Image nhỏ hơn khoảng 2.8 GB và không phải phân phối lại trọng số model. Model card của cả hai model ghi license Apache-2.0 (đã kiểm tra khi triển khai). |
| D4 | `docker-compose.yml` chỉ publish cổng `127.0.0.1:8000`. Muốn mở ra mạng LAN thì đặt `APP_BIND=0.0.0.0`. | Q2 giữ API không đăng nhập, nên mặc định này tránh vô tình mở ra mạng. Chạy bằng `start.bat`/`start.sh` vẫn như cũ. |
| D5 | Qdrant được nạp từ file vector xuất từ collection của paper, không embed lại | Giữ đúng từng vector đã đo; lần khởi động đầu mất vài giây thay vì khoảng 14 phút embed. Nếu thiếu file thì embed lại như cũ. |
| D6 | Lịch sử public gồm các commit mới nối tiếp `origin/main`, không force-push. Đầu nhánh cũ được gắn tag `legacy-2026-04`. | Không mất lịch sử cũ trên GitHub và không đưa lịch sử local (có `Report/`) lên. |
| D7 | README public: tiêu đề theo bản đã sửa trên GitHub hôm nay ("Hệ thống truy xuất văn bản pháp quy học thuật"); nội dung lấy từ README local, bổ sung Docker, tái lập kết quả, vấn đề đã biết và license | README trên GitHub còn mô tả kiến trúc tháng 4 (Streamlit, e5-base). |
| D8 | Phiên bản phát hành `v2.0.1`; `version` trong FastAPI đổi `2.0.0 → 2.0.1` | Để `/docs` khớp tag. Có thể bỏ nếu không muốn sửa dòng này. |
| D9 | Mỗi phase là một commit cục bộ, chỉ add đúng các file của phase đó (không cuốn theo file chưa track như `data/processed/_ingest/`). Chỉ push khi được đồng ý ở checkpoint CP3. | Dễ rollback. Mọi thao tác ra bên ngoài đều cần xác nhận. |

### 1.4 Ngoài phạm vi

- Host công khai, domain, HTTPS, đăng nhập/phân quyền, giới hạn tốc độ (theo Q1, Q2).
- `_dedup` gộp các chunk trùng 80 ký tự đầu (O6, Phụ lục C). Sửa lỗi này sẽ đổi cả HR@1, tức số liệu chính của paper.
- Tăng tốc reranker (paper xếp vào future work).
- Các vấn đề có sẵn khác liệt kê ở Phụ lục C.

### 1.5 Bất biến không hồi quy

| ID | Bất biến | Cách kiểm |
|---|---|---|
| INV-1 | Parser, JSONL, `chunk_id`, BM25 và collection Qdrant của corpus paper không đổi. | Không sửa file nào trong `src/pipeline/` và `src/ingest/`; cổng G1 |
| INV-2 | Thứ hạng giai đoạn 1 (BM25 top-30, dense top-30, RRF top-20) giống hệt trước và sau khi sửa. | G2 |
| INV-3 | Với mọi truy vấn mà code cũ không sinh chunk lặp: output reranker, câu trả lời, tier và danh sách nguồn giống hệt từng byte. | Tính chất P3 (mục 4.2), G3' |
| INV-4 | Endpoint cũ giữ nguyên đường dẫn và field; `/health` chỉ **thêm** field `qdrant`. | G5 |
| INV-5 | HR@1, kiểm định McNemar và khoảng tin cậy bootstrap của paper không đổi. | Chứng minh ở mục 3.2, G7 |
| INV-6 | Cách chạy local giữ nguyên: `start.bat`, `start.sh`, CLI `01/02/03`, `scripts/test_*.py`. | G4 |
| INV-7 | Repo public, kể cả lịch sử, không chứa `Report/`, `.env`, `.claude/`, `data/processed/_ingest/` hay tài liệu do người dùng upload thêm. | Cổng P1 và job CI `publish-guard` |

---

## 2. Hiện trạng

### 2.1 F1 — `_diversify` (`src/retrieval/reranker.py:31-53`)

```python
    # Second pass: fill remaining slots ignoring cap
    for score, doc in scored:
        if len(result) == top_k:
            break
        if doc not in [r for r in result]:      # luôn đúng: r là bản sao có thêm _score_rerank
            doc = dict(doc)
            doc["_score_rerank"] = float(score)
            result.append(doc)
```

`rerank()` chạy ba bước:

1. `_dedup` loại trùng theo `chunk_id` và 80 ký tự đầu.
2. Cross-encoder chấm điểm 20 ứng viên.
3. `_diversify(top_k=10, max_per_source=5)`.

Lượt 1 của `_diversify` lấy chunk theo thứ tự điểm, mỗi văn bản tối đa 5 chunk. Nếu lượt 1 chưa đủ 10, lượt 2 lẽ ra phải bổ sung các chunk bị giới hạn. Thực tế, lượt 2 duyệt lại toàn bộ danh sách từ đầu và thêm mọi chunk gặp phải: các chunk đã chọn ở lượt 1 bị thêm lần thứ hai, còn các chunk bị giới hạn chỉ xuất hiện xen sau chúng.

**Khi nào lỗi xảy ra:** khi lượt 1 chọn được ít hơn 10 chunk. Tình huống này xuất hiện khi ứng viên (sau `_dedup`) dồn vào tối đa 2 văn bản, hoặc khi chỉ còn dưới 10 ứng viên phân biệt.

**Bằng chứng (Phụ lục A).** `eval/pool.jsonl` lưu mọi chunk phân biệt mà hybrid-rerank@10 đóng góp cho pool. `build_pool.py` gọi `chain.retrieve`, tức cùng đường code với Chat. Lượt 2 của code lỗi luôn chèn `scored[0]` trước tiên, nên "ít hơn 10 chunk phân biệt" tương đương với "có chunk lặp". Kết quả:

- 91 câu có đủ 10 chunk phân biệt.
- 9 câu chỉ có 3–9 chunk phân biệt (topic 3, 4, 5, 6, 13, 14, 51, 64, 72). Pool cho cận trên 32 vị trí lặp; E1 đo chính xác được 28 (topic 51 chỉ có 6 phần tử). Cả 9 câu đều có nhãn, tức đều được tính điểm.

Kết quả này nhất quán với lần kiểm tra ngày 25/9 (6/20 câu được ghi lại có chunk lặp).

**Nơi gọi `Reranker.rerank`** (tất cả đều nhận bản sửa):

| Nơi gọi | Dùng để |
|---|---|
| `/chat` (`rag_chain.py:661-663`) | Câu trả lời và danh sách nguồn |
| `/retrieve` | Kết quả truy xuất |
| `/search?mode=hybrid_rerank` (`api/main.py:275-279`) | Kết quả tìm kiếm |
| `eval/run_ablation.py:211-212` | Bảng II |
| `eval/build_pool.py:126` | Pool ở mục IV-B |
| `eval/compare_answers.py:87` | Mục V-F |

### 2.2 F2 — `/health` và thanh trạng thái

```python
# api/main.py:218-224
@app.get("/health")
def health():
    return {"status": "ok", "ready": _chain is not None, "timestamp": time.time()}
```

```javascript
// ui/index.html:880-885 (checkHealth, chạy mỗi 30 giây)
const d = await r.json();
dot.className = 'status-dot' + (d.qdrant ? '' : ' warn');
text.textContent = d.qdrant ? 'System online' : 'Qdrant offline';
```

`d.qdrant` luôn là `undefined`. Vì vậy, hễ API còn sống là thanh trạng thái báo "Qdrant offline" với chấm vàng, không liên quan gì tới trạng thái thật của Qdrant. `/index/health` (`api/main.py:1269`) có đếm point trong Qdrant, nhưng nó đọc toàn bộ JSONL và BM25 nên quá nặng để gọi 30 giây một lần.

### 2.3 Repo và môi trường

**Repo local**

- Nhánh `master`, 6 commit (`c3c4906` → `2c11fec`), chưa có remote.
- 151 MB file được track: `data/` 121 MB (PDF 100.1 MB, `processed/` 10.6 MB, `processed_backup_20260728/` 10.1 MB), `eval/` 18.8 MB, `Report/` 10.0 MB.
- File lớn nhất 27.4 MB, dưới ngưỡng cảnh báo 50 MB của GitHub nên không cần Git LFS.
- Không có secret nào trong lịch sử; `.env` chưa từng được commit.

**GitHub `main`**

- 12 commit (2025-10-15 → 2026-09-26), bố cục cũ của tháng 4: `scripts/`, `configs/`, `serve.py`, UI Streamlit, compose chỉ có Qdrant 1.15.1.
- Commit mới nhất `08c9eb9` lúc 14:07 hôm nay, chỉ đổi tiêu đề README.
- Lịch sử hai bên không có gốc chung.

**Cả hai nơi đều chưa có:** LICENSE, Dockerfile cho app, CI.

**Chỉ mục đang chạy** (Qdrant 1.16.1)

- Lúc bắt đầu khảo sát, `reg_chunks` có 4.313 point (4.224 của paper và 89 của một tài liệu thử), và `bm25.pkl` trong working tree khác bản trong git. Tài liệu thử bị xoá trong lúc khảo sát. Cuối buổi còn 4.224 point, tập id trùng khớp các `chunk_id` trong git, và `bm25.pkl` giống từng byte bản trong git.
- `indexed_vectors_count` = 0: với ngưỡng 10.000, Qdrant quét toàn bộ.
- `data/processed/_ingest/` (trạng thái job của Upload v2) chưa có trong `.gitignore`.

**Môi trường hiện tại** (sẽ được khoá cho production; E1 xác nhận nó tái lập được paper)

- Python 3.11.9, torch 2.11.0 (CPU), sentence-transformers 5.4.1, transformers 5.5.4, tokenizers 0.22.2, qdrant-client 1.17.1, huggingface_hub 1.11.0, underthesea 9.4.0, numpy 2.4.4, fastapi 0.136.0, uvicorn 0.44.0, PyMuPDF 1.27.2.2; Tesseract 5.4.0 (Windows).
- Model: `bkai-foundation-models/vietnamese-bi-encoder@84f9d9ada0d1a3c37557398b9ae9fcedcdf40be0` (Apache-2.0) và `AITeamVN/Vietnamese_Reranker@f536976248403314225d7fdfdbc87f0e9516a54e`. Mỗi model chỉ có một snapshot trong cache, tức đúng revision đã dùng cho paper.
- `pip freeze` có `pywin32`, gói chỉ chạy trên Windows, nên file khoá phiên bản cho Linux phải loại gói này ra.

---

## 3. Đối chiếu với paper

### 3.1 Ma trận tác động

| Phần của paper | Phụ thuộc `_diversify`? | Sau khi sửa F1 | Kiểm chứng |
|---|---|---|---|
| III-C: "reranked … with redundancy removal and limiting the number of units taken from the same source text" | Chính là mô tả bước này | Code **khớp** mô tả. Trước đây không khớp vì còn chunk lặp. | Unit test P2 |
| Eq. (5), τ_min, τ_gap, A(q) | Dùng c(1), c(2) và 4 vị trí đầu | Không đổi, vì 4 vị trí đầu luôn thuộc lượt 1: lượt 1 luôn có ít nhất 5 phần tử khi có từ 5 ứng viên trở lên (khi có ít hơn, phần tử lặp cũng không làm đổi kết quả phép kiểm tra). Ngoại lệ lý thuyết: clause assembly gộp nhiều chunk đầu làm dồn vị trí. | E3: đếm số câu đổi tier (kỳ vọng 0) |
| Bảng II: bm25, dense-exact, dense-hnsw, hybrid-rrf | Không | Không đổi | E0 |
| Bảng II: hybrid-rerank, HR@1 = 0.885 | Chỉ vị trí 1, luôn là `scored[0]` và luôn được chọn ở lượt 1 | **Không đổi** (chứng minh ở 3.2) | E2 |
| Bảng II: hybrid-rerank, HR@5† = 0.979, MRR@10† = 0.927 | Phụ thuộc hit đầu tiên | Chỉ có thể giữ nguyên hoặc tăng; chỉ 9 câu có khả năng đổi | E2 |
| Bảng II: hybrid-rerank, nDCG@10† = 0.919 | Phụ thuộc mọi vị trí; bản lỗi cộng điểm cả chunk lặp nên có thể vượt 1 | **Đổi**, dự kiến giảm | E2 |
| Bảng II: latency 29,767.4 ms | Chi phí rerank không đổi | Giữ số của paper (paper đã nêu độ trễ dao động tới 2 lần); không thay | — |
| V-B: 72/13/1, McNemar p = 0.0018, CI [+0.052, +0.198], "12.5 points", "1,700×" | Chỉ phụ thuộc HR@1 hoặc độ trễ | Không đổi | E2 (so vector hit@1) |
| V-B: phân tích pool depth-100, trần 88.5% / 99.0% | Chỉ phụ thuộc giai đoạn 1 | Không đổi | — |
| IV-B: pool = top-10 hybrid-rerank + top-3 BM25 + top-3 dense | Pool được xây bằng reranker lỗi | Pool (đã gán nhãn) giữ nguyên. Ở 9 câu, pool chỉ nhận 3–9 đơn vị phân biệt từ nhánh rerank. Sau khi sửa, top-10 của các câu này có 12 đơn vị chưa gán nhãn (đo ở E2); chúng được tính là không liên quan, đúng quy ước "unjudged = non-relevant" của paper. | E2 đếm số đơn vị chưa gán nhãn |
| V-F (20 câu): 1,651 → 1,134; 4,366 → 2,752; 60 → 0; 53; 2; tier 12/7/1 | Nhánh baseline dùng mọi chunk | Mẫu 20 câu có 2 câu bị ảnh hưởng (topic 6 và 51, đều tier 3), nên số của nhánh baseline có thể đổi. Nhánh mới (giới hạn 4 clause) và phân bố tier dự kiến không đổi. | E3: tái lập file ngày 10/9 trước, rồi đo lại |
| I, III, Hình 1–3, V-A, V-C, V-D, V-E, VI, VII, tóm tắt | Không | Không đổi | — |

F2 và phần phát hành không tác động tới số liệu nào. Gói production giữ đúng những gì paper mô tả:

- Qdrant chạy bằng Docker (IV-A).
- Collection giữ HNSW m = 16, ef_construct = 100, ngưỡng 10.000. Vì vậy ở quy mô 4.224 đơn vị, Qdrant quét toàn bộ, đúng khuyến nghị dùng exact search dưới khoảng 8.000 đơn vị ở mục VI.
- Model và thư viện được khoá đúng phiên bản; mọi thứ chạy trên CPU.
- Bản phát hành này thực hiện cam kết ở mục VII: "Code, the graded test collection, and the scripts will be published".

### 3.2 Chứng minh

Gọi F là kết quả lượt 1 và f = |F|. Cả code cũ lẫn code mới đều trả F ở f vị trí đầu.

- Nếu f = 10: hai bản cho kết quả giống hệt (**P3**). Theo mục 2.1, đây là trường hợp của 91/100 câu.
- Nếu f < 10: code cũ duyệt lại toàn bộ danh sách từ đầu và thêm mọi chunk gặp phải (chunk đã có trong F xen lẫn chunk bị giới hạn) cho tới khi đủ 10 hoặc hết danh sách. Code mới duyệt đúng như vậy nhưng bỏ qua chunk đã có trong F.

Từ đó suy ra:

- **(a)** `scored[0]` luôn thuộc F, nên vị trí 1 không đổi. Do đó HR@1, cùng mọi kiểm định dựa trên HR@1, không đổi (**P4**).
- **(b)** Phần sau F của code mới chính là phần sau F của code cũ khi bỏ đi các chunk lặp. Vì vậy mọi chunk giữ nguyên vị trí hoặc được đẩy lên sớm hơn, còn chunk lặp biến mất; chunk lặp vốn không bao giờ là "hit đầu tiên" vì bản gốc của nó đứng trước. Suy ra, xét từng câu, HR@k và MRR của code mới lớn hơn hoặc bằng code cũ (**P5**), và câu chỉ thay đổi khi code cũ có chunk lặp.
- **(c)** nDCG cộng điểm ở mọi vị trí, nên bản cũ cộng thêm lần nữa điểm của chunk lặp. Ví dụ: một chunk liên quan ở vị trí 1, bị lặp lại ở vị trí 6, cho nDCG = 1 + 1/log₂7 > 1.

Precision@k và MAP (không có trong paper) cũng bị thổi phồng theo cách tương tự.

### 3.3 Công bố (Q4, Q7)

- Tag `kse2026-paper` trỏ tới code đúng như lúc đo (từ commit gốc `c3c4906`, đã lọc theo allowlist). Chạy `eval/run_ablation.py` trên tag này cho ra đúng Bảng II (đã kiểm ở E1).
- Theo Q7, README không có mục đính chính. README chỉ ghi rằng tag `kse2026-paper` là code của paper và nhánh `main` có thêm các thay đổi sau đó.
- Số liệu đo lại nằm ở Phụ lục D của tài liệu này.
- Không sửa file paper.

---

## 4. Thiết kế F1 — khử trùng ở lượt 2 của `_diversify`

### 4.1 Thay đổi code

```diff
 def _diversify(scored: list[tuple[float, dict]], top_k: int, max_per_source: int) -> list[dict]:
     """Pick top_k docs while capping how many come from the same source file."""
     source_count: dict[str, int] = {}
     result = []
+    taken: set[int] = set()  # positions in `scored` already picked
     # First pass: pick within cap
-    for score, doc in scored:
+    for i, (score, doc) in enumerate(scored):
         src = doc.get("source", "")
         if source_count.get(src, 0) < max_per_source:
             doc = dict(doc)
             doc["_score_rerank"] = float(score)
             result.append(doc)
+            taken.add(i)
             source_count[src] = source_count.get(src, 0) + 1
         if len(result) == top_k:
             return result
-    # Second pass: fill remaining slots ignoring cap
-    for score, doc in scored:
+    # Second pass: fill remaining slots ignoring cap, never re-adding a pick.
+    # Testing `doc not in result` cannot work: the entries of `result` are
+    # copies carrying `_score_rerank`, so no original ever compares equal.
+    for i, (score, doc) in enumerate(scored):
         if len(result) == top_k:
             break
-        if doc not in [r for r in result]:
+        if i not in taken:
             doc = dict(doc)
             doc["_score_rerank"] = float(score)
             result.append(doc)
     return result
```

Chỉ sửa hàm này. `_dedup`, `RERANK_MAX_CHARS`, `min_score`, `top_k` và `max_per_source` giữ nguyên. Danh sách trả về có thể ngắn hơn 10 khi không còn ứng viên phân biệt (ví dụ topic 51 chỉ có 3). Nhánh reranker tắt vốn đã có thể trả về danh sách ngắn như vậy; mọi nơi gọi đều duyệt danh sách, không giả định đủ 10 phần tử.

### 4.2 Tính chất được đảm bảo

| ID | Tính chất |
|---|---|
| P1 | f vị trí đầu (kết quả lượt 1) giống hệt code cũ. |
| P2 | Không vị trí nào trong `scored` xuất hiện hai lần trong kết quả; kết quả không chứa hai `chunk_id` trùng nhau. |
| P3 | Nếu code cũ không sinh chunk lặp thì output hai bản giống hệt, kể cả `_score_rerank`. |
| P4 | Phần tử đầu tiên luôn là `scored[0]`. |
| P5 | Không chunk nào tụt hạng so với bản cũ; do đó với mọi tập nhãn, HR@k và MRR@k của bản mới lớn hơn hoặc bằng bản cũ, xét từng truy vấn. |

### 4.3 Tác động lên Chat

| Tier | Phần danh sách được dùng | Có thể đổi? |
|---|---|---|
| tier1 | `docs[0]` và footer nguồn (khử trùng theo văn bản) | **Không.** Chunk bổ sung luôn thuộc văn bản đã có trong F, vì văn bản chưa có trong F thì đã được chọn ở lượt 1. |
| tier2 | Mọi chunk cùng Điều với `docs[0]` (tối đa 6 khoản) | **Có.** Chunk mới cùng Điều sẽ được trích thêm. |
| tier3 | 4 chunk đầu sau clause assembly | Không, trừ khi clause assembly gộp chunk làm dồn vị trí |
| tier4 | — | Không |
| Bảng References (`sources`) | Toàn bộ danh sách | **Có:** hết mục lặp; có thể thêm chunk mới hoặc danh sách ngắn hơn 10. |

Chỉ 9/100 câu GT có thể đổi. E3 liệt kê cụ thể từng câu thay đổi ở CP2.

### 4.4 Unit test (`tests/test_reranker_diversify.py`, dùng `unittest` như các test hiện có)

- 20 ứng viên cùng một văn bản, `top_k=10`, `max_per_source=5`: 10 chunk khác nhau, 5 chunk đầu giống code cũ, 5 chunk sau là `scored[5:10]`.
- 3 ứng viên: trả về 3 chunk, không lặp.
- Lượt 1 đủ 10: output giống code cũ (P3).
- Kiểm tra ngẫu nhiên (seed cố định, 2.000 trường hợp; số văn bản, số ứng viên và điểm đều ngẫu nhiên):
  - P1–P5 luôn đúng;
  - output bằng code cũ (chép nguyên vào test làm bản tham chiếu) mỗi khi code cũ không sinh lặp.
- Chunk không có `chunk_id` vẫn được khử trùng đúng, vì so theo vị trí.

---

## 5. Thiết kế F2 — `/health` báo trạng thái Qdrant thật

### 5.1 Hợp đồng

`GET /health` trả thêm `"qdrant": bool`, bằng `true` khi Qdrant trả lời **và** collection được cấu hình tồn tại. Các field `status`, `ready`, `timestamp` giữ nguyên tên, kiểu và ý nghĩa. Endpoint vẫn luôn trả HTTP 200 khi API sống (script khởi động `start.bat`/`start.sh` và healthcheck của Docker dựa vào điều này).

### 5.2 Cài đặt (`api/main.py`)

Thêm `from qdrant_client import QdrantClient` vào phần import và hàm sau:

```python
_QDRANT_PROBE_TTL_S = 5.0
_qdrant_probe: dict = {"at": float("-inf"), "ok": False}


def _qdrant_ok() -> bool:
    """True when the configured Qdrant answers and holds the collection.

    Cached for a few seconds, since every open UI tab polls /health every
    30 s. Resolves URL and key like VectorRetriever does, and never loads
    the RAG chain, so it also answers while the models are warming up.
    """
    now = time.monotonic()
    if now - _qdrant_probe["at"] < _QDRANT_PROBE_TTL_S:
        return _qdrant_probe["ok"]
    ok, client = False, None
    try:
        from src.config import load_config

        vs = load_config(CONFIG_PATH)["vector_store"]
        api_key = vs.get("qdrant_api_key") or os.environ.get("QDRANT_API_KEY", "")
        client = QdrantClient(url=vs["qdrant_url"], api_key=api_key or None,
                              timeout=2, check_compatibility=False)
        ok = bool(client.collection_exists(vs["collection_name"]))
    except Exception:
        ok = False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    _qdrant_probe.update(at=now, ok=ok)
    return ok
```

`health()` thêm dòng `"qdrant": _qdrant_ok(),`.

- Timeout 2 giây, nằm trong giới hạn 3,5 giây mà UI chờ.
- `check_compatibility=False` tránh gọi thêm một request và tránh ghi warning mỗi 5 giây khi Qdrant tắt.
- qdrant-client 1.17.1 có đủ `collection_exists`, `close` và `check_compatibility` (đã kiểm tra).
- Không dùng `_chain`, nên không kích hoạt việc nạp model.

### 5.3 Trạng thái hiển thị (UI không đổi)

| Tình huống | `/health` | Thanh trạng thái |
|---|---|---|
| API sống, Qdrant sống, có collection | 200, `qdrant: true` | chấm xanh, "System online" |
| API sống, Qdrant tắt | 200, `qdrant: false` (sau tối đa 2 giây) | chấm vàng, "Qdrant offline" |
| API sống, Qdrant sống nhưng thiếu collection | 200, `qdrant: false` | chấm vàng, "Qdrant offline" (Chat cũng không chạy được, nên cảnh báo là đúng) |
| API tắt | không trả lời | chấm đỏ, "API offline" |

### 5.4 Kiểm thử

- **Unit (`tests/test_health.py`):** dùng `TestClient` không chạy startup event; patch `api.main.CONFIG_PATH` (trỏ config tạm) và `api.main.QdrantClient`, nên không đụng dữ liệu hay Qdrant thật.
  - collection có → `true`; không có → `false`; lỗi kết nối → `false`;
  - kết quả được cache trong TTL;
  - các field cũ còn đủ và đúng kiểu.
- **E2E:** thêm một assert `/health` → `qdrant is True` vào `tests/test_api_e2e.py`.
- **Trình duyệt (G6):** dừng container Qdrant → trong vòng 30 giây hiện "Qdrant offline"; bật lại → "System online"; dừng API → "API offline".

---

## 6. Đo lại số liệu trong môi trường cô lập

### 6.1 Vì sao phải cô lập

Chỉ mục đang chạy thay đổi mỗi khi có người upload hoặc xoá tài liệu qua giao diện; việc này đã xảy ra ngay trong lúc khảo sát (mục 2.3). Chỉ cần thêm một tài liệu là IDF của BM25 và láng giềng dense đã khác corpus của paper, và ablation không còn tái lập được Bảng II. Vì vậy phép đo chạy trên một môi trường dựng lại từ git, và không ghi gì vào chỉ mục đang chạy.

### 6.2 Dựng môi trường "corpus paper"

1. **Xuất vector** (chỉ đọc). Scroll `reg_chunks` kèm vector, giữ các point có `chunk_id` thuộc 34 JSONL trong git (HEAD). Kiểm tra:
   - đủ 4.224 point, mỗi chunk đúng một point;
   - payload trùng bản ghi JSONL.

   Kết quả ghi vào `data/qdrant_seed/`: `reg_chunks.f32.npy` (4.224 × 768, khoảng 13 MB), `reg_chunks.ids.json`, và `manifest.json` (model, revision, số lượng, sha256). Script: `scripts/export_qdrant_seed.py`.
2. **Qdrant tạm:** container `qdrant/qdrant:v1.16.1`, cổng `127.0.0.1:6399`, volume riêng. Nạp bằng `scripts/seed_qdrant.py` (chính script mà bản production dùng, mục 7.3), với cấu hình HNSW/optimizer lấy nguyên từ `config.yaml` qua `ensure_collection`. Nhờ vậy, bước đo cũng đồng thời kiểm chứng gói phát hành.
3. **Corpus từ git:** 34 JSONL và `bm25.pkl` lấy bằng `git show HEAD:…` vào thư mục tạm. File config tạm trỏ `processed_dir`, `bm25.index_path` và `qdrant_url` vào các thư mục/dịch vụ tạm này; mọi tham số khác giữ nguyên.

Nếu E0 không khớp (ví dụ thứ tự các vector bằng điểm nhau khác đi), phương án B là snapshot `reg_chunks` đang chạy, khôi phục vào Qdrant tạm rồi xoá mọi point không thuộc corpus paper (nếu có). Nguyên nhân sẽ được ghi vào báo cáo.

### 6.3 Các lần chạy

Tất cả các lần chạy dùng `--gt eval/test_queries_gt_v2.jsonl`.

| Lần | Code | Nội dung | Thời gian | Cổng |
|---|---|---|---|---|
| E0 | HEAD (chưa sửa) | `run_ablation.py --stages bm25,dense-exact,dense-hnsw,hybrid-rrf` | ~2 phút | 4 hàng đầu Bảng II khớp đến 4 chữ số |
| E1 | HEAD | `--stages hybrid-rerank`. Một wrapper trong thư mục tạm **ghi lại** điểm cross-encoder theo khoá (câu hỏi, text đã cắt 512 ký tự); không sửa repo. | ~50 phút CPU | Khớp hàng hybrid-rerank cũ: 0.8854 / 0.9792 / 0.9271 / 0.9188 |
| E2 | Đã sửa F1 | Như E1 nhưng **dùng lại** điểm đã ghi (đầu vào giống hệt nên điểm giống hệt) | ~1 phút | HR@1 và vector hit@1 giống E1; cho ra số mới của HR@5, MRR@10, nDCG@10; đếm đơn vị chưa gán nhãn trong top-10 |
| E3a | Cũ và mới | Chat cho 100 câu: `retrieve → expand_all → generate`, cùng điểm đã ghi | ~2 phút | G3' |
| E3b | Cũ và mới | `compare_answers.py --n 20`, cùng điểm đã ghi | ~1 phút | Bản cũ tái lập đúng `answers_ab_20260910_033215.json`; bản mới cho số V-F sau khi sửa |
| E4 | Đã sửa, **trong container Linux** | `run_ablation.py` đủ 5 stage, chạy mới hoàn toàn (không dùng điểm đã ghi) | ~50 phút CPU | Khớp E0 + E2 đến 4 chữ số (L3) |

Tổng thời gian khoảng 1 giờ 50 phút CPU. Số liệu chất lượng không phụ thuộc tải máy; chỉ độ trễ bị ảnh hưởng, mà độ trễ không được báo cáo lại. Dù vậy, nên chạy E1 và E4 khi máy rảnh.

### 6.4 Đầu ra

- Các file `eval/results/ablation_*_{kse2026-repro,dedupfix,linux}.json` (thư mục này bị gitignore nên không được commit).
- Bảng kết quả, danh sách câu thay đổi và số đơn vị chưa gán nhãn được ghi vào Phụ lục D của tài liệu này ở CP2.
- E4 là bằng chứng rằng người khác chạy lại từ đầu, trên Linux, sẽ nhận đúng các số của E2.

### 6.5 Tuỳ chọn: gán nhãn các đơn vị mới

Top-10 sau khi sửa chứa 12 đơn vị chưa gán nhãn (đo ở E2). Tác giả chọn cách mặc định (Q9). Hai cách xử lý:

- **Mặc định:** báo cáo với quy ước của paper (chưa gán nhãn = không liên quan) và nêu rõ số lượng.
- **Nếu tác giả muốn số chính xác hơn:** gán nhãn bằng `eval/judge.py`, theo đúng giao thức 0/1/2 ở `eval/ANNOTATION.md`. Ước tính khoảng 10–15 phút. Nhãn mới được ghi riêng, không sửa các nhãn cũ.

---

## 7. Phát hành mã nguồn (Q1: chỉ public mã nguồn)

### 7.1 Nội dung công khai

| Đường dẫn | Công khai? | Ghi chú |
|---|---|---|
| `api/`, `src/`, `ui/`, `img/`, `scripts/`, `tests/`, `docs/` | Có | Mã nguồn, test, tài liệu |
| `config.yaml`, `requirements.txt`, `requirements.lock`, `.env.example`, `.gitignore`, `start.bat`, `start.sh`, `run_pipeline.sh`, `analyze_chunks.sh` | Có | |
| `data/<7 nhóm>/*.pdf` (34 file), `data/groups.json`, `data/processed/*.jsonl` (34 file), `bm25.pkl` | Có (Q3) | Lấy từ git, **không** lấy từ working tree |
| `data/processed_backup_20260728/` | Có (thuộc `data/`, Q3) | Corpus trước khi sửa chunker; dùng để tái lập cột "Before" của Bảng I. Bỏ được nếu không muốn. |
| `data/qdrant_seed/` | Có (mới, khoảng 13 MB) | Vector của 4.224 chunk |
| `eval/` (GT v2, judgments, pool, script, cache) | Có (Q3) | GT, judgments, pool theo CC BY 4.0; script theo MIT |
| `Report/` | **Không** (Q3) | Kể cả trong lịch sử |
| `.env`, `.claude/`, `venv/`, `eval/results/`, `data/processed/_ingest/`, tài liệu upload thêm sau này | **Không** | Đã hoặc sẽ có trong `.gitignore`. Vì file được lấy từ git nên các file chưa track không bị cuốn theo. |

Repo public khoảng 155 MB. Không file nào vượt 50 MB, nên không cần Git LFS.

### 7.2 Lịch sử git public

Mọi thao tác chạy trong **một bản clone tạm**, không đụng working tree đang phát triển.

```bash
git clone <repo local> <tạm>/public && cd <tạm>/public
git remote add github https://github.com/Hoangnam25012004/Developing-an-Efficient-Vector-Database-System-for-Academic-Regulation-Retrival-System.git
git fetch github
git tag legacy-2026-04 github/main                      # 08c9eb9: giữ bản tháng 4
git switch -c public github/main
# Với mỗi mốc S theo thứ tự: c3c4906 → 2c11fec → commit các phase P1..P4
git rm -r -q . && git checkout S -- . && git rm -r -q --cached --ignore-unmatch Report && rm -rf Report
git commit                                              # message nêu rõ mốc nguồn S
git tag kse2026-paper <commit tạo từ c3c4906>
git tag v2.0.1 <commit cuối>
git push github public:main legacy-2026-04 kse2026-paper v2.0.1   # fast-forward, không --force
```

- Nếu `main` trên GitHub thay đổi trước lúc push (ví dụ README được sửa thêm), fetch lại rồi dựng lại các commit trên đầu mới. Vẫn không force-push.
- Repo local vẫn là nơi phát triển. Lần phát hành sau lặp lại quy trình này cho các commit mới.

### 7.3 Gói production (Docker)

**Thành phần**

| File | Vai trò |
|---|---|
| `requirements.lock` | `pip freeze` của venv hiện tại (E1 xác nhận venv này tái lập được paper), bỏ `pywin32`; `torch==2.11.0` cài từ index CPU của PyTorch |
| `models.lock.json` | Model → revision đã khoá (hai revision ở mục 2.3) |
| `Dockerfile` | `python:3.11-slim-bookworm` + `tesseract-ocr` + `tesseract-ocr-vie`; cài torch CPU rồi `requirements.lock`; user không phải root; `PYTHONUTF8=1`; `HF_HOME=/models`; `HEALTHCHECK` gọi `/health` |
| `.dockerignore` | Loại `.git`, `venv`, `Report`, `.claude`, `.env`, `eval/results`, `data/processed/_ingest`, `data/processed_backup_*`, `__pycache__` |
| `docker/entrypoint.sh` | Tải model → chờ Qdrant → nạp collection nếu chưa có → chạy app (mô tả bên dưới) |
| `scripts/fetch_models.py` | Tải đúng revision trong `models.lock.json` (bỏ qua file `*.bin` trùng với `model.safetensors`); ghi `refs/main` trỏ tới revision đó để app nạp đúng bản khi chạy offline mà không phải sửa code. Revision không còn trên Hugging Face → dừng và báo lỗi rõ ràng. |
| `scripts/seed_qdrant.py` | Nếu collection đã có thì bỏ qua. Nếu chưa, tạo bằng `ensure_collection(..., fresh=False, vs=config)` (cùng cấu hình như `02_embed_index`) rồi upsert `PointStruct(id=int(chunk_id,16), vector, payload=dict(rec))`, giống hệt cách 02 dựng point. Chunk có trong JSONL mà không có trong seed (tài liệu upload sau này) được embed bằng `upsert_records` có sẵn. Cuối cùng kiểm tra số point bằng số chunk JSONL. |
| `scripts/export_qdrant_seed.py` | Xuất seed từ một collection đang chạy (mục 6.2). Dùng khi corpus gốc thay đổi. |
| `docker-compose.yml` | Hai service `qdrant` và `app` (bên dưới) |

**`docker/entrypoint.sh`** chạy lần lượt:

1. `python scripts/fetch_models.py` (không làm gì nếu model đã có).
2. `python scripts/seed_qdrant.py --if-missing` (tự thử lại trong khi chờ Qdrant khởi động).
3. Đặt `HF_HUB_OFFLINE=1`, rồi `exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1`. Upload v2 yêu cầu chạy đúng một process.

**`docker-compose.yml`**

```yaml
name: arrs
services:
  qdrant:
    image: qdrant/qdrant:v1.16.1          # cùng phiên bản đang phục vụ collection của paper
    restart: unless-stopped
    volumes: [qdrant_storage:/qdrant/storage]
    # Không publish cổng: chỉ app truy cập qua mạng nội bộ của compose.
  app:
    image: ghcr.io/hoangnam25012004/<tên-repo-chữ-thường>:${ARRS_VERSION:-v2.0.1}
    build: .
    restart: unless-stopped
    depends_on: [qdrant]
    environment:
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ""
    volumes:
      - arrs_data:/app/data       # lần đầu Docker chép corpus trong image vào volume; tài liệu upload được giữ lại
      - hf_models:/models         # khoảng 2.7 GB model, tải một lần
    ports:
      - "${APP_BIND:-127.0.0.1}:${APP_PORT:-8000}:8000"
volumes: {qdrant_storage: {}, arrs_data: {}, hf_models: {}}
```

**Yêu cầu máy:** Docker cấp ít nhất 6 GB RAM (reranker khoảng 2.2 GB ở fp32) và khoảng 6 GB đĩa. Lần đầu mất vài phút để tải model; các lần sau khởi động trong khoảng 30 giây. Thời gian trả lời bị chi phối bởi reranker trên CPU, đúng như paper đã nêu.

**Chạy script eval trong container:** `docker compose exec app python eval/run_ablation.py --gt eval/test_queries_gt_v2.jsonl`.

### 7.4 CI/CD (GitHub Actions)

| Workflow | Kích hoạt | Nội dung |
|---|---|---|
| `ci.yml` / `publish-guard` | Mọi push và PR | Báo lỗi nếu có `Report/`, `.env`, `.claude/` hoặc `data/processed/_ingest/`, hoặc có file lớn hơn 50 MB |
| `ci.yml` / `tests` | Mọi push và PR | Python 3.11; cài torch CPU và `requirements.lock`; chạy `python -m unittest discover -s tests -t .`. Test Qdrant dùng `QdrantClient(":memory:")` nên không cần server; E2E tự bỏ qua. |
| `release.yml` | Push tag `v*`, hoặc chạy tay | Build image → `docker compose up -d` với image vừa build → chờ `/health` trả `ready: true, qdrant: true` → gọi `/chat` với một câu mẫu và kiểm tra tier khác tier4, có nguồn → chạy toàn bộ unit test và E2E (`ARRS_E2E=1`) trong container → push lên GHCR với hai tag `vX.Y.Z` và `latest`. |

Nếu package GHCR được tạo ở chế độ private (thường gặp ở lần publish đầu), tác giả chuyển sang public một lần trên web GitHub (CP4).

### 7.5 README, license, trích dẫn

**README public** gồm các phần:

1. Giới thiệu hệ thống: truy xuất và tổng hợp câu trả lời trích nguyên văn, không dùng mô hình sinh (đúng cách gọi của paper).
2. Chạy nhanh bằng Docker (3 lệnh) và yêu cầu máy.
3. Cảnh báo bảo mật (mục 7.6).
4. Chạy không dùng Docker (giữ nội dung hiện có).
5. Kiến trúc, cấu hình, API; ghi chú `/health` có thêm `qdrant`.
6. Tái lập kết quả paper: tag `kse2026-paper` và các lệnh chạy.
7. Vấn đề đã biết (Phụ lục C, kết quả L4).
8. License.
9. Trích dẫn.

**Các file đi kèm:**

- `LICENSE`: MIT, bản quyền thuộc Dang Hoang Nam, 2026.
- `eval/LICENSE`: CC BY 4.0 cho GT v2, judgments và pool, kèm yêu cầu trích dẫn paper.
- README ghi rõ các PDF trong `data/` là văn bản của cơ quan ban hành, được đưa vào để tái lập nghiên cứu, và không thuộc phạm vi hai license trên.
- `CITATION.cff`: metadata của paper (tiêu đề, tác giả, KSE 2026).

### 7.6 Bảo mật (Q2: giữ API mở)

Không thêm đăng nhập. README cảnh báo rằng ai truy cập được cổng 8000 đều có thể upload/xoá tài liệu, reindex, sửa `/config` và chạy `/evaluate`, và khuyên không mở cổng này ra Internet khi chưa có reverse proxy có xác thực. Compose mặc định chỉ nghe `127.0.0.1` (D4); Qdrant không publish cổng. Image chạy bằng user không phải root.

---

## 8. Ma trận tác động tới các chức năng khác

| Chức năng | Có thay đổi? | Chi tiết | Kiểm chứng |
|---|---|---|---|
| Chat `/chat` | Có (F1) | Hết nguồn lặp; 9/100 câu GT có thể đổi phần nguồn và câu trả lời tier 2 | G3', E3a |
| `/retrieve`, `/search?mode=hybrid_rerank` | Có (F1) | Hết kết quả lặp | Unit P2 |
| `/search` với mode khác | Không | Không qua reranker | — |
| `/health` | Có (F2) | Thêm `qdrant` | G5 |
| Thanh trạng thái UI | Hành vi đúng hơn, code không đổi | — | G6 |
| Upload, Documents, xoá, reindex, jobs | Không | — | E2E, L2 |
| `/config`, `/evaluate`, `/stats`, `/sources`, `/pdf`, `/groups`, `/index/health` | Không | — | E2E, G5 |
| CLI `01/02/03`, `scripts/test_*.py` | Không | — | G4 |
| Script `eval/` | Không sửa; `run_ablation`, `build_pool`, `compare_answers` nhận reranker đã sửa | Số liệu hybrid-rerank thay đổi đúng như mục 3 | E0–E4 |
| `start.bat`, `start.sh`, `.claude/launch.json` | Không | — | G4 |
| `.gitignore` | Thêm `data/processed/_ingest/` | Tránh commit nhầm trạng thái job | P1 |
| Chỉ mục đang chạy trên máy phát triển | Không (chỉ đọc khi xuất seed) | — | Số point trước/sau |

---

## 9. Kiểm thử và cổng

| Cổng | Điều kiện |
|---|---|
| G1 | `git diff` không chạm `src/pipeline/` và `src/ingest/`; JSONL và `bm25.pkl` trong cây public giống hệt bản ở `c3c4906` và `2c11fec`. |
| G2 | Trên 100 câu GT v2, thứ hạng BM25 top-30, dense top-30 và RRF top-20 giống hệt trước và sau khi sửa. |
| G3' | E3a: 91 câu không bị ảnh hưởng giống hệt (answer, tier, sources). 9 câu còn lại: f vị trí đầu giống hệt, khác biệt chỉ nằm sau vị trí f, tier không đổi. Nếu có câu đổi tier thì dừng lại và báo cáo. |
| G4 | Output của `scripts/test_synthesis.py` và `scripts/test_chunker.py` giống bản gốc; `start.bat` khởi động được; `/` và `/docs` mở được. |
| G5 | Mọi field cũ của các endpoint còn nguyên kiểu dữ liệu; `/health` có thêm `qdrant: bool`. |
| G6 | Trình duyệt: đủ 3 trạng thái của thanh trạng thái; Chat trả lời một câu tier 1 và một câu thuộc nhóm bị ảnh hưởng (References không lặp); Documents, Upload, Xoá hoạt động như trước (trên server thử có dữ liệu riêng, như cách làm ở v2). |
| G7 | E0 khớp 4 hàng đầu Bảng II; E1 khớp hàng hybrid-rerank cũ; E2 giữ nguyên HR@1 và vector hit@1, nên McNemar và CI giữ nguyên. |
| G8 | Trên Windows: unit test (60 test hiện có cộng test mới) đạt; E2E (`ARRS_E2E=1`) đạt. |
| L1 | Unit test đạt trên Linux (CI). |
| L2 | E2E đạt bên trong container. |
| L3 | E4 (Linux, chạy mới) khớp E0 + E2 đến 4 chữ số. Nếu lệch: liệt kê từng câu và nguyên nhân (sai số dấu phẩy động, điểm bằng nhau). |
| L4 | Parse 34 PDF trong container rồi so với JSONL trong git: PDF có lớp chữ phải giống hệt; khác biệt ở trang OCR (Tesseract 5.3 so với 5.4) được đo và ghi vào mục Vấn đề đã biết. |
| P1 | Cây public: không có đường dẫn cấm (INV-7), không file nào lớn hơn 50 MB, quét regex không thấy secret, có `LICENSE` và `eval/LICENSE`. |
| P2 | Trước khi push: clone sạch nhánh public → `docker compose up -d` → trả lời được câu mẫu, `/health` báo `qdrant: true`. |
| P3 | Sau khi push: CI xanh; image kéo từ GHCR chạy được (lặp lại P2 với image từ GHCR). |

Nếu một cổng không đạt thì dừng lại và báo cáo; không sửa cho qua.

---

## 10. Kế hoạch triển khai

| Phase | Nội dung | Cổng | Thời gian máy |
|---|---|---|---|
| P0 | Tag local `pre-fix-2026-09-26`. Xuất seed (chỉ đọc Qdrant đang chạy). Dựng Qdrant tạm và corpus từ git. Chạy E0, E1. | G7 (E0, E1) | ~55 phút CPU |
| P1 | F2 (`/health`) cùng test | G5, G6 (thanh trạng thái), G8 | nhỏ |
| P2 | F1 (`_diversify`) cùng test; chạy E2, E3 | G2, G3', G7, G8 | vài phút |
| P3 | Gói production: `requirements.lock`, `models.lock.json`, Dockerfile, compose, entrypoint, các script, `.dockerignore`, `.gitignore`. Build image local; chạy L2, L4, E4. | L2–L4 | ~60 phút CPU |
| P4 | Tài liệu và CI: README, `LICENSE`, `eval/LICENSE`, `CITATION.cff`, workflow, phiên bản 2.0.1, kết quả vào Phụ lục D | P1 | nhỏ |
| P5 | Dựng nhánh public (mục 7.2). Kiểm P1, P2 → CP3 → push và tag → CI → CP4 → P3. | P1–P3 | ~30 phút |

**Checkpoint cần tác giả xác nhận**

| CP | Khi nào | Nội dung |
|---|---|---|
| CP1 | Trước P0 | Duyệt tài liệu này. |
| CP2 | Sau P2 | Xem số liệu mới, danh sách câu thay đổi và số đơn vị chưa gán nhãn; quyết định có gán nhãn thêm không (6.5) và cách xử lý đính chính (kết quả: Q6–Q9). |
| CP3 | Trước khi push | Xem tóm tắt cây public (thư mục, dung lượng, danh sách commit kèm tác giả và message, diff so với remote), rồi đồng ý push. |
| CP4 | Sau khi CI chạy | Chuyển package GHCR sang public trên web GitHub. |

---

## 11. Rủi ro và giả định

| # | Nội dung | Xử lý |
|---|---|---|
| R1 | Seed vector cho thứ tự khác ở các vector bằng điểm nhau (chunk trùng text), làm E0 lệch | Dùng phương án B ở mục 6.2 (snapshot); ghi nguyên nhân |
| R2 | Linux khác Windows ở sai số dấu phẩy động (embedding câu hỏi, điểm rerank) hoặc ở OCR khi parse lại | L3, L4; ghi rõ trong mục Vấn đề đã biết. Corpus được nạp từ seed nên không có sai lệch phía corpus. |
| R3 | Hugging Face xoá hoặc đổi revision của model | `fetch_models.py` dừng với thông báo rõ ràng; README hướng dẫn chép model từ một cache có sẵn |
| R4 | Người tự host chạy "Đồng bộ chỉ mục" (reindex) | Reindex parse lại mọi PDF (OCR bằng Tesseract của Debian) và embed lại, nên chỉ mục có thể lệch khỏi corpus paper. Ghi vào mục Vấn đề đã biết; cách khôi phục: xoá volume rồi khởi động lại. |
| R5 | `main` trên GitHub thay đổi trước lúc push | Fetch lại và dựng lại commit trên đầu mới; không force |
| R6 | Package GHCR ở chế độ private | CP4 |
| R7 | Người tự host mở API không đăng nhập ra Internet (Q2) | Cảnh báo trong README; mặc định chỉ nghe localhost |
| R8 | Docker Desktop cấp ít RAM (dưới 6 GB) làm reranker bị OOM | Ghi yêu cầu trong README; entrypoint ghi log rõ ràng |
| R9 | Volume `arrs_data` không tự cập nhật khi nâng cấp image | README: `docker compose down -v` để về corpus của bản mới |
| R10 | Câu trả lời của 9 câu đổi, người dùng thấy khác trước | Đây là thay đổi mong muốn; được liệt kê ở CP2 |
| A1 | Corpus trong git (`c3c4906` = `2c11fec` ở phần `data/`) đúng là corpus của paper | Được kiểm bằng E0 |
| A2 | Snapshot model trong cache đúng là revision đã dùng cho paper (mỗi model chỉ có một snapshot) | Được kiểm bằng E1 |
| A3 | Chưa rõ phiên bản server Qdrant lúc đo tháng 7 | Nếu E0 và E1 khớp trên 1.16.1 thì phiên bản không ảnh hưởng tới chất lượng |

---

## Phụ lục A — Bằng chứng (probe ngày 2026-09-26, chỉ đọc)

| Probe | Kết quả |
|---|---|
| `git ls-remote` GitHub | `main` = `08c9eb9` |
| Clone GitHub vào thư mục tạm | 12 commit, 108 file, bố cục tháng 4; commit cuối (14:07) chỉ đổi tiêu đề README |
| Quét regex key/secret/token/password trên toàn bộ lịch sử local | 0 kết quả; `.env` chưa từng được commit |
| `GET /collections/reg_chunks` (Qdrant 1.16.1) | Lúc đầu 4.313 point, cuối buổi 4.224 point; `indexed_vectors_count` 0; 8 segment; HNSW m=16, ef_construct=100; `full_scan_threshold` = `indexing_threshold` = 10.000 |
| Tài liệu thử | 89 chunk, upload 14:21:35, index xong 14:22:43, nhóm `Luat-quoc-gia`, ngôn ngữ `en`; bị xoá trong lúc khảo sát |
| Scroll toàn bộ id của `reg_chunks` (cuối buổi), so với `chunk_id` của 34 JSONL trong git | 4.224 = 4.224, hai tập trùng khớp; `git diff` không thấy thay đổi ở `bm25.pkl` |
| `eval/pool.jsonl`: số chunk phân biệt từ hybrid-rerank theo topic | {10: 91, 9: 1, 8: 1, 7: 3, 6: 2, 5: 1, 3: 1}; cận trên 32 vị trí lặp (E1 đo được 28); các topic 3, 4, 5, 6, 13, 14, 51, 64, 72 |
| Bảng II ↔ file kết quả | `eval/results/ablation_v2_20260731_020157.json` (độ trễ khớp từng số). Các chỉ số chất lượng lặp lại y hệt ở `…_020137.json` và `ablation_v2_stats_20260909_033030.json`. |
| V-F ↔ file kết quả | `eval/results/answers_ab_20260910_033215.json`: 1651/1134, 4366/2752, 60→0, 53, 2, tier 12/7/1; topic 6 và 51 nằm trong mẫu |
| Chunk có cùng 80 ký tự đầu (quy tắc của `_dedup`) | 208/4.224 (4.9%), trong đó 134 là dòng bảng; nhóm lớn nhất 30 chunk |
| qdrant-client 1.17.1 | Có `collection_exists`, `close`, `check_compatibility` |
| Test | 60 test; test Qdrant dùng `":memory:"` |
| `pip freeze` | 129 gói; `pywin32` chỉ chạy trên Windows |

## Phụ lục B — File dự kiến thay đổi hoặc thêm mới

| File | Loại | Phase |
|---|---|---|
| `src/retrieval/reranker.py` | Sửa `_diversify` (mục 4.1) | P2 |
| `api/main.py` | Thêm `_qdrant_ok()` và field `qdrant`; `version` 2.0.1 (D8) | P1, P4 |
| `tests/test_reranker_diversify.py`, `tests/test_health.py` | Mới | P1, P2 |
| `tests/test_api_e2e.py` | Thêm một assert cho `/health` | P1 |
| `requirements.lock`, `models.lock.json` | Mới | P3 |
| `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/entrypoint.sh` | Mới | P3 |
| `scripts/fetch_models.py`, `scripts/seed_qdrant.py`, `scripts/export_qdrant_seed.py` | Mới | P3 |
| `data/qdrant_seed/` (`.npy` khoảng 13 MB, `ids.json`, `manifest.json`) | Mới | P0 |
| `.gitignore` | Thêm `data/processed/_ingest/` | P3 |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | Mới | P4 |
| `README.md` | Viết lại theo mục 7.5 | P4 |
| `LICENSE`, `eval/LICENSE`, `CITATION.cff` | Mới | P4 |
| `docs/design/bugfix-and-public-release.md` | Tài liệu này, bổ sung kết quả sau mỗi phase | P0–P5 |

## Phụ lục C — Vấn đề quan sát được nhưng **không** sửa (cần duyệt riêng)

| ID | Vấn đề | Vì sao không sửa |
|---|---|---|
| O6 | `_dedup` (`reranker.py:19`) coi hai chunk là trùng khi 80 ký tự đầu giống nhau. 208/4.224 chunk bị ảnh hưởng, chủ yếu là dòng bảng mang cùng tiêu đề bảng. Nhóm lớn nhất là 30 dòng của bảng tiêu chí điểm rèn luyện. Topic 51 ("Tiêu chí 1 trong điểm rèn luyện…") chỉ còn 3 ứng viên phân biệt trước khi rerank, nên dòng đúng có thể bị loại trước khi được chấm điểm. | Sửa sẽ đổi thứ hạng từ vị trí 1, tức đổi HR@1 (số chính của paper). Cần quyết định riêng và một ablation mới. |
| O9 | `eval/cache/corpus_*` và các GT v1, strict mang `chunk_id` từ trước khi chia lại chunk. Chạy dense bằng cache này ra 0. | Là dữ liệu lịch sử của paper. README ghi rõ chỉ GT v2 hợp lệ. |
| O10 | Docstring và `title` của FastAPI gọi hệ thống là "RAG … + LLM", trong khi không có LLM. | Chỉ là mô tả; README public dùng đúng cách gọi của paper. |
| O11 | `eval/make_tables.py` mặc định ghi vào `Report/tables.tex`, thư mục không có trong repo public. | Người dùng truyền `--out`; README có ghi chú. |
| O2 (từ v2) | Đoạn "Điều N" ngắn bị gộp vào chunk sau, hoặc bị bỏ nếu nằm ở cuối tài liệu. | Đã quyết định giữ nguyên như trong paper (25/9). |

## Phụ lục D — Kết quả kiểm thử (2026-09-26)

Bảng kết quả chi tiết của phụ lục này không có trong bản công khai của tài liệu.

## Phụ lục E — Điều chỉnh khi triển khai (so với bản thiết kế)

| # | Điều chỉnh | Lý do |
|---|---|---|
| E-1 | Lập luận ở 3.2 (b) được sửa: ở lượt 2, code cũ duyệt lại toàn bộ danh sách, nên ngoài chunk lặp còn thêm cả chunk bị giới hạn (ở vị trí muộn hơn). Tính chất P5 vẫn đúng và còn mạnh hơn: không chunk nào tụt hạng. | Unit test ngẫu nhiên phát hiện; E2 xác nhận HR@5 và MRR không đổi. |
| E-2 | Số vị trí lặp chính xác là 28, không phải 32. 32 là cận trên suy từ pool; topic 51 chỉ có 6 phần tử. | E1. |
| E-3 | `tests/test_health.py` import `api.main` trong `setUp`, không ở đầu module. | Giữ được cách chạy `ARRS_E2E=1 … discover`, vì E2E phải là module đầu tiên import API. |
| E-4 | `tests/_util.py` chọn font Unicode theo hệ điều hành (Windows vẫn dùng Arial như cũ; Linux dùng DejaVu Sans); image và CI cài `fonts-dejavu-core`. | Trên Linux, PDF mẫu của test được tạo bằng Helvetica nên mất chữ tiếng Việt, làm 2 test fail. Chỉ sửa test, không sửa ứng dụng. |
| E-5 | `fetch_models.py` bỏ qua `*.bin`. Model card của cả hai model ghi Apache-2.0. | Repo bi-encoder có thêm `pytorch_model.bin` (540 MB), trùng với `model.safetensors` mà app thực sự dùng. |
| E-6 | `ui/index.html`: `API_BASE` lấy địa chỉ của chính trang khi trang được phục vụ qua http(s) (Q6). | Lỗi có sẵn phát hiện ở G6: UI gán cứng `127.0.0.1:8000` nên hỏng khi đổi cổng, mở từ máy khác trong LAN hay qua tunnel. |
| E-7 | Dockerfile: cache pip bằng BuildKit, thêm `--retries`; `HF_HUB_OFFLINE=1` đặt cho toàn image (`fetch_models.py` tự tắt cho riêng nó). | Lần build đầu hỏng vì một file tải về bị lỗi mạng. Đặt offline cho toàn image để cả tiến trình chạy bằng `docker compose exec` cũng dùng đúng revision đã khoá. |
| E-8 | Thêm `.gitattributes` (`*.sh eol=lf`). | Máy phát triển đặt `core.autocrlf=true`; entrypoint phải giữ LF trong container. |
| E-9 | README: sửa các thông tin sai có sẵn (kích thước model, tên collection `rag_docs` → `reg_chunks`, lệnh clone, output của `/health`, thời gian trả lời trên CPU) và ghi chú rằng bảng số liệu cũ trong README là kết quả trên GT v1. | Để README public khớp với hệ thống và với paper. |
| E-10 | G6 dùng server riêng (cổng 8010, bản sao dữ liệu, Qdrant riêng ở cổng 6398), không dừng Qdrant hay server của tác giả. | Server của tác giả (cổng 8000, còn chạy code cũ) và dữ liệu thật không bị động tới. |
| E-11 | Release workflow chạy thêm toàn bộ unit test trong container; không cache model trong CI. | Kiểm thêm môi trường Linux; model được tải lại mỗi lần release (khoảng 2.8 GB). |
| E-12 | README không có mục đính chính (Q7). | Quyết định của tác giả khi triển khai. |
