# Detail Design — Trang Chat thành "Ask the Regulations"

| Mục | Nội dung |
|---|---|
| Trạng thái | **Đã triển khai** (2026-09-28, bản 2.2.0). Điều chỉnh khi triển khai: Phụ lục G. Kết quả kiểm thử: Phụ lục H. |
| Phạm vi code | `ui/index.html`: CSS, HTML, JS của màn hình Chat (`#screen-chat`); nhãn và icon của mục Chat trên sidebar dùng chung. `api/main.py`: 2 dòng trong `_doc_to_source` để `POST /chat` trả điểm rerank (D4). `README.md`: mục giao diện "1. Chat" (nay là "1. Ask"), các chỗ khác nhắc tới Chat, ví dụ response của `/chat`. Phát hành 2.2.0 (D7): version của API, `docker-compose.yml`, `CITATION.cff`. |
| Không đụng tới | `src/**` (parser, ingest, retrieval, bộ tổng hợp câu trả lời), `config.yaml`, JSONL, Qdrant, BM25, nội dung câu trả lời, các endpoint khác, trang Library (trừ nhãn sidebar và 6 chuỗi "Chat" → "Ask" của D6), modal tải lên, toast, `eval/`, Dockerfile, CI |
| Bản mẫu | [`chat-ask-mockup.html`](chat-ask-mockup.html) — mở trực tiếp bằng trình duyệt. Dữ liệu thật: 4 câu hỏi gợi ý và 1 câu GT (không tìm thấy), với câu trả lời và đoạn trích lấy từ `POST /chat` ngày 2026-09-27, điểm rerank từ `POST /retrieve` cho cùng câu hỏi, tên văn bản từ catalog. Bản mẫu không gọi API: câu hỏi gõ vào được trả bằng câu trả lời đã ghi (câu lạ nhận câu "không tìm thấy" đã ghi). Thanh "Mockup" chuyển giữa các trạng thái Empty, Conversation, Waiting, Error. Liên kết văn bản trỏ tới server cục bộ 127.0.0.1:8000. |
| Liên quan | Thiết kế trang Library: [`documents-library-redesign.md`](documents-library-redesign.md). Token màu, font, thang cỡ chữ dùng chung với tài liệu đó; tài liệu này xử lý O1 và O7 ở Phụ lục C của nó. |

---

## 0. Tóm tắt

**Vấn đề.** Trang Chat dùng bố cục của một chatbot AI mẫu: biểu tượng mũ tốt nghiệp 🎓 và "Welcome to ARRS", bốn thẻ gợi ý có emoji, avatar tròn gradient ghi "AI" và "SV", bong bóng câu hỏi xanh `#2563EB`, ba chấm "đang gõ", nút gửi ➤. Màu, font và cỡ chữ khác hẳn trang Library (nền xám xanh `#F1F5F9`, chỉ có Inter, chữ 13.5 px). Nguồn của câu trả lời hiện dưới dạng tên file thô trong khối "Tài liệu đính kèm", lẫn cả văn bản không được trích (54/93 câu trả lời). Khung References (10 đoạn trích) và nút xoá cuộc hỏi–đáp **không bao giờ hiện ra** vì nằm trong một thanh công cụ bị CSS ẩn (2.2).

**Đề xuất.** Biến trang thành **"Ask the Regulations"** — bàn tra cứu quy chế theo kiểu thư viện và cơ sở dữ liệu văn bản pháp luật:

1. **Đầu trang giống Library:** cùng dòng tên trường, tiêu đề serif, câu giới thiệu nói rõ câu trả lời được **trích nguyên văn** từ văn bản chứ không do máy viết ra; dòng phạm vi "Searches 34 documents in 7 collections · Browse the Library".
2. **Mỗi lượt hỏi–đáp là một phiếu tra cứu** (enquiry record), đọc theo một cột như một trang tài liệu: câu hỏi là tiêu đề serif; câu trả lời gồm các **đoạn trích điều khoản** (khối trích kẻ lề trái, chữ serif); câu mà hệ thống đánh dấu là liên quan nhất được tô như bút dạ; số trích `[1]` bấm được.
3. **Danh sách nguồn kiểu tài liệu tham khảo** dưới mỗi câu trả lời: tên văn bản, loại, số hiệu, năm (từ catalog), tên file, liên kết mở đúng trang. Tách "Cited in the answer" với "Also found by the search".
4. **Khung "Passages consulted"** (sửa lỗi không hiện): mở từ nút của từng câu trả lời; các đoạn trích trình bày như sổ văn bản của Library, có vị trí Điều/Khoản, trang, điểm rerank chữ nhỏ màu xám.
5. **Ô hỏi** cùng kiểu ô tra cứu của Library, nút chính navy "Ask"; khi chờ hiện chữ và đồng hồ giây, không có ba chấm.
6. **"New enquiry"** — đưa lại chức năng xoá cuộc hỏi–đáp đang bị ẩn.
7. **Cùng hệ thị giác với Library:** token `--lib-*`, Source Serif 4 / Inter / JetBrains Mono, cùng thang cỡ chữ, đường kẻ thay cho thẻ và bóng đổ, icon nét, không emoji. Sidebar: "Chat" → "Ask".

**Cam kết không hồi quy.** Không sửa `src/**`, nên câu trả lời không thể đổi; `api/main.py` chỉ đổi 2 dòng để điểm rerank có giá trị, câu trả lời và danh sách nguồn giữ nguyên (kiểm trên 100 câu GT). Trang Library, modal, toast giữ nguyên (trừ nhãn sidebar). Việc trình bày lại câu trả lời **không bỏ nội dung nào** (cổng G4 kiểm trên 100 câu trả lời đã ghi). Mọi chức năng hiện có ở Phụ lục A được giữ, và những chức năng đang không dùng được thì được sửa.

---

## 1. Mục tiêu, phạm vi, quyết định

### 1.1 Mục tiêu

| ID | Mục tiêu |
|---|---|
| MT-1 | Người xem nhận ra đây là **dịch vụ tra cứu văn bản quy chế** (như bàn tra cứu của thư viện hay cơ sở dữ liệu văn bản pháp luật), không phải chatbot AI. |
| MT-2 | **Nhất quán với Library**: cùng màu, font, thang cỡ chữ, đường kẻ, nút, ô nhập, vòng focus, cùng giọng văn nhãn (tiếng Anh). |
| MT-3 | **Trích dẫn là trung tâm**: mỗi câu trả lời cho biết văn bản nào, Điều/Khoản nào, trang nào; mở được đúng trang; xem được các đoạn đã tra. |
| MT-4 | Giữ **100% chức năng** hiện có của trang (Phụ lục A); sửa các chức năng đang không dùng được (2.2). |
| MT-5 | Không hồi quy chức năng khác (1.5). |

### 1.2 Quyết định đã chốt (2026-09-28)

| # | Chủ đề | Quyết định | Hệ quả trong design |
|---|---|---|---|
| D1 | Vùng được đổi | (a) giao diện trang Chat: màn đầu, luồng hỏi–đáp, ô nhập, trạng thái chờ và lỗi; (b) nguồn dưới mỗi câu trả lời, lấy tên văn bản từ catalog qua `GET /documents`; (c) khung đoạn trích References: sửa lỗi không hiện, trình bày lại, nhãn tiếng Anh; (d) nút bắt đầu lại "New enquiry" | Mục 5, 7. Library, modal, toast giữ nguyên. |
| D2 | Tên | Sidebar "Ask"; tiêu đề trang "Ask the Regulations" | 5.9, 6 |
| D3 | Điểm rerank | Hiện nhỏ, chữ xám ("rerank 0.97") trong danh sách đoạn trích | 5.8 |
| D4 | Điểm rerank rỗng ở `/chat` | Sửa `_doc_to_source` (`api/main.py`) để đọc thêm khoá `score_rerank` / `score_rrf` khi không có khoá `_score_*` | 7.1, 8.5, cổng G2 |
| D5 | Ngôn ngữ câu hỏi (2026-09-28) | Người dùng hỏi bằng **tiếng Việt**: mọi văn bản trong kho đang là tiếng Việt, hỏi bằng tiếng Anh cho kết quả không chính xác. Giao diện vẫn bằng tiếng Anh. Câu giới thiệu: "Ask in Vietnamese. …" | 5.2, 6 |
| D6 | Chữ "Chat" trên trang Library (2026-09-28) | 6 chuỗi của trang Library nhắc tới trang Chat đổi "Chat" → "Ask": tooltip trạng thái *Indexed*, dòng thống kê "… available in Ask", thông báo sau khi upload, hộp xác nhận, thông báo và tooltip của nút *Sync index* | Phụ lục G, cổng G3 |
| D7 | Phát hành (2026-09-28) | Triển khai, kiểm thử rồi đẩy lên GitHub: tag `v2.2.0`; `docker-compose.yml` mặc định image `v2.2.0`; version của API và `CITATION.cff` là 2.2.0; đưa tài liệu này và bản mẫu lên repo. Chữ gợi ý của ô hỏi giữ nguyên | Mục 11, Phụ lục H |
| — | Kế thừa từ Library | Nhãn giao diện tiếng Anh; dữ liệu (tên văn bản, câu trả lời, thông điệp của server) giữ nguyên văn; token `--lib-*` | Mục 4, 6 |

### 1.3 Mặc định do thiết kế chọn (có thể đổi khi duyệt)

| # | Mặc định | Lý do | Phương án khác |
|---|---|---|---|
| M1 | Một cột đọc rộng tối đa 760 px, căn trái theo khung trang của Library; không xếp hai bên như tin nhắn | Đọc như văn bản; tiêu đề hai trang trùng mép trái khi chuyển qua lại | Cột căn giữa |
| M2 | Câu hỏi là tiêu đề serif 20 px; không avatar, không "AI"/"SV" | Bỏ nhân cách hoá | Nhãn "You" |
| M3 | Khối "Tài liệu đính kèm" không hiện nguyên văn mà được trình bày lại thành danh sách nguồn; mọi thông tin của nó được giữ (số `[n]`, tên file, trang, liên kết) | D1-b | Hiện cả hai |
| M4 | Tiêu đề đoạn trích mà server ghi bằng tên file (văn bản không có cấu trúc Điều, ví dụ `**2. 9.-QD688-MGHP-2024.pdf** [1]`) hiện tên văn bản từ catalog; tên file đặt ngay dưới bằng chữ mono | Tên file không phải tên văn bản; nội dung vẫn đủ | Giữ tên file |
| M5 | Câu mà server in đậm trong đoạn trích (câu liên quan nhất) hiện như tô bút dạ `--lib-hl`, không in đậm | Giống cách tô kết quả tìm ở Library | Giữ in đậm |
| M6 | 4 câu hỏi gợi ý giữ nguyên văn tiếng Việt, trình bày thành danh sách "Common questions" | Không đổi nội dung | Thêm câu hỏi mới |
| M7 | Khung đoạn trích là cột phải khi cửa sổ ≥ 1440 px; ngăn kéo phủ lên nội dung khi hẹp hơn; toàn chiều ngang khi ≤ 640 px | Cột đọc 760 px + khung 360 px + sidebar cần khoảng 1440 px | Luôn là ngăn kéo |
| M8 | Khung đoạn trích hiện đoạn trích **của câu trả lời được chọn** (mỗi câu trả lời có nút riêng) | Hiện tại chỉ giữ nguồn của câu cuối (B5) | Chỉ câu cuối |
| M9 | Bấm tên văn bản ở danh sách nguồn hoặc đoạn trích mở PDF **đúng trang** (`#page=N`) | Khung References cũ mở PDF từ trang 1 (B6) | Như cũ |
| M10 | "Copy answer" chép câu trả lời và danh sách nguồn dạng chữ, có tên văn bản | Bản chép dùng được ngay để dẫn nguồn | Chép đúng chữ đang hiện |
| M11 | Sau khi gửi, cuộn để câu hỏi mới nằm đầu vùng nhìn; khi có câu trả lời thì giữ nguyên vị trí đó | Câu trả lời dài; người đọc bắt đầu từ đầu | Cuộn xuống cuối như cũ |
| M12 | Phím `/` đưa focus vào ô hỏi khi đang ở trang Ask | Giống ô tra cứu của Library | Không có phím tắt |
| M13 | "New enquiry" xoá ngay, không hỏi xác nhận; nếu còn câu đang chờ thì huỷ yêu cầu đó ở trình duyệt (`AbortController`) để hỏi được ngay | Giống hàm hiện có; không phải chờ tới 3 phút | Hỏi xác nhận; chờ câu cũ xong |
| M14 | Thông điệp của server (câu "không tìm thấy", lỗi) hiện nguyên văn; UI thêm một dòng hướng dẫn tiếng Anh | Giống quy ước M3 của Library | Dịch |
| M15 | Mục "Also found by the search" mặc định **thu gọn**, hiện số lượng; tự mở khi thân bài không trích số nào | Giữ trọng tâm vào nguồn được trích | Luôn mở |
| M16 | Chặn gửi câu hỏi thứ hai khi câu trước chưa có trả lời (cả phím Enter); chữ vừa gõ vẫn giữ trong ô | Hiện tại nút bị khoá nhưng phím Enter vẫn gửi được (B8) | Như cũ |

### 1.4 Ngoài phạm vi

- Nội dung câu trả lời do server tạo: khối nguồn liệt kê cả văn bản không được trích, số hiệu nhiễu từ JSONL, câu "Tôi không tìm thấy…" ở ngôi thứ nhất. Tài liệu này chỉ trình bày lại ở UI (Phụ lục C).
- Lưu lịch sử hỏi–đáp qua lần tải trang, xuất lịch sử, trả lời dạng stream.
- Trang Library (trừ nhãn sidebar), modal tải lên, toast.
- Dọn CSS và thư viện không dùng (Chart.js, CSS các màn hình đã bỏ) — O6 của Library.
- Dark mode, bản in.

### 1.5 Bất biến không hồi quy

| ID | Bất biến | Kiểm chứng |
|---|---|---|
| INV-1 | `src/**`, `config.yaml`, dữ liệu và chỉ mục không đổi | `git diff --stat` rỗng; SHA-256 của JSONL và `bm25.pkl` |
| INV-2 | `POST /chat` trả câu trả lời và danh sách nguồn (chunk, file, trang, đoạn trích, Điều, Khoản) giống hệt cho 100 câu GT v2; chỉ `score_rerank`, `score_rrf` đổi từ `null` thành số | Cổng G2 |
| INV-3 | Các endpoint khác trả response giống hệt từng byte (`/retrieve`, `/search` vốn đã có điểm, không đổi) | Snapshot trước/sau |
| INV-4 | Trang Library: DOM `#screen-documents` sau khi tải, CSS và JS của Library giống hệt; sidebar chỉ khác nhãn, tooltip và icon của mục Ask | Cổng G3 |
| INV-5 | Trình bày lại không bỏ nội dung: mọi dòng chữ của câu trả lời đều hiện; mỗi dòng nguồn `[n]` có một mục nguồn với cùng tên file, trang và URL | Cổng G4 (100 câu trả lời đã ghi) |
| INV-6 | Mọi chức năng ở Phụ lục A chạy như cũ: cùng API, cùng body, cùng timeout 180 s | Cổng G5 |
| INV-7 | Trang vẫn chạy khi không có `data/catalog.json` hoặc `GET /documents` lỗi: hiện tên suy từ tên file | Cổng G5 (C17, C18) |
| INV-8 | 83 unit test và E2E `ARRS_E2E=1` vẫn qua | Chạy test |

---

## 2. Hiện trạng

Số dòng tham chiếu `ui/index.html` tại bản 2.1.0 (tag `v2.1.0`).

### 2.1 Vì sao trang trông như "chatbot AI mẫu"

| Thành phần | Hiện tại | Vì sao trông như template | Hướng xử lý |
|---|---|---|---|
| Màn đầu | 🎓 cỡ 56 px, "Welcome to ARRS", đoạn giới thiệu căn giữa (dòng 586–589) | Mẫu "hero" của mọi chatbot | Masthead như Library (5.2) |
| Câu hỏi gợi ý | 4 thẻ bo góc 10 px, emoji 🎓 📋 ⏱️ 📝, nổi lên khi trỏ chuột (dòng 590–607; CSS 99–103) | Thẻ gợi ý có emoji là dấu hiệu quen thuộc của UI AI | Danh sách "Common questions" chữ serif (5.4) |
| Người nói | Avatar tròn gradient "AI" (xanh–xanh trời) và "SV" (xanh ngọc) (CSS 107–109; JS 1076) | Nhân cách hoá, gradient | Bỏ avatar; nhãn "Enquiry" / "Answer" (5.5) |
| Câu hỏi | Bong bóng xanh `#2563EB` bên phải, bo 16 px (CSS 110–112) | Giao diện nhắn tin | Tiêu đề serif của phiếu tra cứu |
| Câu trả lời | Bong bóng trắng bo 16 px, chữ 13.5 px, rộng tối đa 72% (CSS 110–121) | Tin nhắn thay cho văn bản | Văn bản một cột; đoạn trích kiểu trích dẫn pháp luật (5.6) |
| Khi chờ | Ba chấm nảy trong bong bóng "AI" (CSS 133–137; JS 1112–1119) | "Đang gõ" giả lập con người | "Searching the regulations… 8 s" với vòng trạng thái của Library |
| Dòng meta | "23:40 ⏱ 11.37s 📋 Copy" | Emoji | Chữ và icon nét |
| Nguồn | Khối "Tài liệu đính kèm" hiện nguyên văn, ví dụ `[3]: 43/2019/QH14 – 32.Luat-Giao-duc-2019.pdf — trang 33` | Tên file thô, số hiệu nhiễu, không tách nguồn được trích | Danh sách nguồn có tên văn bản (5.7) |
| Ô nhập | Thanh trắng tràn hết chiều ngang, nút ➤ xanh (CSS 153–161; HTML 613–621) | Ô chat | Ô hỏi cùng kiểu ô tra cứu Library, nút "Ask" (5.3) |
| Màu, chữ | `--bg #F1F5F9`, `--blue #2563EB`, chỉ Inter | Khác Library (giấy, mực, navy, serif) | Token `--lib-*` (mục 4) |
| Sidebar | Mục "Chat", icon bong bóng hội thoại | Từ ngữ của chatbot | "Ask" (D2) |

### 2.2 Lỗi và hạn chế hiện có (đo ngày 2026-09-27, cửa sổ 1280×720)

| # | Hiện tượng | Nguyên nhân | Xử lý |
|---|---|---|---|
| B1 | Khung References (10 đoạn trích) **không bao giờ hiện**: sau khi trả lời, `#source-panel` vẫn mang lớp `hidden`, rộng 0 px, độ mờ 0 | `renderSourcesPanel` (1122) chỉ đổ nội dung; nút bật `#toggle-sources` nằm trong `.topbar` bị ẩn bằng `#screen-chat > .topbar{display:none!important}` (92) | Nút "Passages consulted" ở mỗi câu trả lời (5.8) |
| B2 | Không có cách xoá cuộc hỏi–đáp | Nút `#clear-chat` cũng nằm trong thanh bị ẩn | "New enquiry" (5.2) |
| B3 | Điểm ở References luôn là "—" | `POST /chat` trả `score_rerank: null` cho mọi nguồn (953/953 nguồn ghi ngày 2026-09-27): `chain.query()` trả khoá `score_rerank`, còn `_doc_to_source` đọc `_score_rerank` | D4 (7.1) |
| B4 | Thẻ References chỉ có "Điều N", không có loại hay nhóm văn bản | `POST /chat` không trả `doc_type`, `doc_group`, `doc_number`, `article_title`, `chapter`: `chain.query()` chỉ trả một tập trường rút gọn | Tên, loại, số hiệu lấy từ catalog (7.3) |
| B5 | References chỉ giữ nguồn của câu trả lời cuối | Biến dùng chung `lastChatSources` | Lưu nguồn theo từng câu trả lời (M8) |
| B6 | Bấm thẻ References mở PDF ở trang 1 | URL không có `#page` | M9 |
| B7 | Ô nhập tràn hết chiều ngang (1906 px ở ảnh chụp của tác giả): dòng chữ quá dài để đọc | Không giới hạn độ rộng | Cột 760 px (M1) |
| B8 | Nhấn Enter khi câu trước chưa trả lời sẽ gửi thêm một yêu cầu song song | Nút gửi bị khoá nhưng `sendChat()` không kiểm tra; hai chỉ báo chờ trùng `id="__typing"` | M16 |

### 2.3 Câu trả lời của server (định dạng, đo trên 100 câu GT v2)

Câu trả lời là Markdown do bộ tổng hợp không dùng mô hình ngôn ngữ (`src/chat/rag_chain.py`) sinh ra theo 4 mẫu. UI nhận biết mẫu qua cấu trúc chữ, không cần trường `tier` (API không trả trường này).

| Mẫu | Dấu hiệu | Ví dụ thật |
|---|---|---|
| Một Khoản | Tiêu đề in đậm + `[n]`, một khối trích | `**Điều 29, Khoản 6 – Sử dụng kết quả rèn luyện** [1]`, rồi `> 6. Để xét cấp học bổng tài trợ…` |
| Nhiều Khoản cùng Điều | Tiêu đề Điều, nhãn `**Khoản k:**`, nhiều khối trích | `**Điều 7 – …** [1]`, `**Khoản 2:**`, `> 2. …` |
| Nhiều Điều hoặc nhiều văn bản | Tiêu đề đánh số `**i. Điều N – …** [n]` hoặc `**i. <tên file>** [n]`; nhãn nghiêng `*Khoản k:*` | Câu hỏi gợi ý 3: 4 mục từ 2 văn bản |
| Không tìm thấy | Không có khối nguồn | `Tôi không tìm thấy thông tin này trong tài liệu hiện có.` |

Chân câu trả lời luôn có dạng: dòng `---`, dòng `**Tài liệu đính kèm:**`, rồi mỗi dòng `[n]: [<số hiệu> – ]<tên file> — trang <p>` (`build_citations_footer`). Mỗi dòng ứng với một văn bản khác nhau trong kết quả tra, theo thứ tự xuất hiện; số `[n]` trong thân bài trỏ vào đây. Khoản quá dài có ghi chú `*(Trích 2/5 phần của Khoản này — xem văn bản gốc để đầy đủ.)*`.

Số liệu trên 100 câu:
- 7 câu không tìm thấy; độ dài trung vị 954 ký tự, dài nhất 4.894.
- 93 câu có khối nguồn; trong đó **54 câu liệt kê nhiều văn bản hơn số được trích** (hay gặp nhất: trích 1, liệt kê 5–6).
- 209 dòng trích; 51 dòng bắt đầu bằng số Khoản (`> 6. …`), mà `marked` hiện thành danh sách đánh số nằm trong khối trích.
- 63 nhãn nghiêng `*Khoản k:*`; 14 dòng gạch đầu dòng.
- Mỗi câu có 3–10 đoạn trích (68 câu có đủ 10).

### 2.4 Kiểm kê chức năng

Toàn bộ chức năng (hàm, sự kiện, API) được liệt kê ở **Phụ lục A**. Đây là "hợp đồng" mà bản thiết kế lại phải giữ.

---

## 3. Định hướng thiết kế

### 3.1 Ý tưởng: bàn tra cứu và phiếu trả lời có trích dẫn

Trang mượn quy ước của ba nơi mà người dùng học thuật đã quen:

- **Cơ sở dữ liệu văn bản pháp luật:** câu trả lời là *trích đoạn điều khoản* kèm vị trí chính xác (văn bản, Điều, Khoản, trang); điều khoản được trích nguyên văn trong khối trích.
- **Dịch vụ "hỏi thủ thư" của thư viện đại học:** mỗi yêu cầu là một phiếu gồm câu hỏi, câu trả lời và các nguồn đã tra.
- **Danh mục tài liệu tham khảo của bài báo khoa học:** nguồn đánh số `[1]`; mỗi mục ghi đủ tên, loại, số hiệu, năm, vị trí.

### 3.2 Nguyên tắc

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| P1 | Văn bản quy định là nhân vật chính | Đoạn trích serif 16 px, là chữ lớn nhất của câu trả lời; nhãn và meta chữ nhỏ |
| P2 | Không nhân cách hoá | Không avatar, không "AI", không ba chấm "đang gõ", không lời chào |
| P3 | Nói rõ bản chất của hệ thống | "Answers are quoted from the regulations, not generated." ở đầu trang và dưới ô hỏi |
| P4 | Trích dẫn đầy đủ và mở được | Mỗi `[n]` dẫn tới một mục nguồn; mỗi mục nguồn mở đúng trang |
| P5 | Cùng hệ thị giác với Library | Token, font, thang chữ, đường kẻ, nút, ô nhập, vòng focus (mục 4) |
| P6 | Đường kẻ thay cho thẻ và bong bóng | Bo góc 3 px; không bóng đổ, trừ lớp nổi (ngăn kéo đoạn trích) |
| P7 | Không emoji | Icon nét 16 px của Library |

### 3.3 Bỏ gì, thay bằng gì

| Bỏ | Thay bằng |
|---|---|
| 🎓 và "Welcome to ARRS" | Masthead: "International University · VNU-HCM" / "Ask the Regulations" |
| 4 thẻ gợi ý có emoji | Danh sách "Common questions" (serif, mũi tên) |
| Avatar "AI" / "SV" | Nhãn "Enquiry 1 · 23:40" và "Answer · quoted from 1 of 5 documents found · 12.9 s" |
| Bong bóng xanh và trắng | Phiếu tra cứu một cột, ngăn nhau bằng đường kẻ |
| Ba chấm nảy | Vòng trạng thái của Library và "Searching the regulations… 8 s" |
| "⏱ 11.37s 📋 Copy" | Thời gian trong nhãn "Answer"; nút chữ "Copy answer" có icon nét |
| Khối "Tài liệu đính kèm" thô | Danh sách "Sources": "Cited in the answer" và "Also found by the search" |
| Khung References bị ẩn | Nút "Passages consulted (10)" ở mỗi câu trả lời |
| Nút ➤ xanh | Nút navy "Ask" |

---

## 4. Hệ thống thị giác (dùng chung với Library)

Trang Ask **không thêm token màu hay họ font mới**; mọi giá trị lấy từ khối `--lib-*` mà trang Library đã khai báo trong `:root`.

### 4.1 Màu

| Token (Library) | Dùng ở trang Ask |
|---|---|
| `--lib-paper` #F7F5F0 | Nền trang, nền thanh ô hỏi |
| `--lib-sheet` #FFFFFF | Ô hỏi, khung đoạn trích |
| `--lib-hover` #FBFAF6 | Câu hỏi gợi ý và mục nguồn khi trỏ chuột |
| `--lib-ink` / `--lib-ink-2` / `--lib-ink-3` | Chữ chính / chữ phụ / chú thích (tương phản 15.5 / 8.1 / 5.1:1 trên trắng, như 4.1 của Library) |
| `--lib-rule` / `--lib-rule-strong` / `--lib-rule-ui` | Kẻ giữa các mục / kẻ ngăn phiếu và đầu khối / viền ô hỏi, nút phụ, lề khối trích |
| `--lib-navy` / `--lib-navy-2` | Nút "Ask", liên kết, số trích `[n]`, vòng focus |
| `--lib-gold` | Vạch mục đang chọn ở sidebar (như Library) |
| `--lib-hl` #FFF1B8 | Câu liên quan nhất trong đoạn trích (M5); mục nguồn vừa được nhảy tới |
| `--lib-busy` / `--lib-bad` | Vòng chờ / lỗi |

Các token cũ của Chat (`--blue`, `--bg`, `--text`…) không còn được trang Ask dùng, nhưng vẫn giữ trong `:root` vì CSS khác còn tham chiếu.

### 4.2 Chữ — cùng thang với Library

| Vai trò | Library | Ask |
|---|---|---|
| Dòng tên trường (kicker) | Inter 11/600, in hoa, giãn .14em | Giống hệt |
| Tiêu đề trang | Source Serif 4 34/600 (27 khi ≤ 640 px) | Giống hệt |
| Câu giới thiệu | Inter 15, ink-2 | Giống hệt |
| Dòng thống kê | Inter 13, ink-2 | Dòng phạm vi: giống hệt |
| Tiêu đề phân đoạn | Serif 17/600 | Tiêu đề khung đoạn trích |
| Tên văn bản | Serif 16/600 | Serif 15/600 trong danh sách nguồn và đoạn trích |
| Nhãn nhóm | Inter 11/600, in hoa, giãn .1em | "COMMON QUESTIONS", "ENQUIRY", "ANSWER", "SOURCES" |
| Dòng mô tả | Inter 12.5, ink-2 | "Decision · No. 967/QĐ-ĐHQT · 2022 · p. 26" |
| Tên file | JetBrains Mono 11.5, ink-3 | Giống hệt |
| — | — | **Câu hỏi:** serif 20/600, dòng cao 1.35 (18 khi ≤ 640 px) |
| — | — | **Câu hỏi gợi ý:** serif 16/400 |
| — | — | **Chữ thường của câu trả lời:** Inter 15, dòng cao 1.7 |
| — | — | **Đoạn trích điều khoản:** serif 16, dòng cao 1.65, ink |
| — | — | **Tiêu đề vị trí** (Điều, Khoản): Inter 14.5/600, ink |
| — | — | **Nhãn Khoản:** Inter 12.5/600, ink-2 |
| — | — | **Điểm rerank:** JetBrains Mono 11, ink-3 |

Font đã được nạp sẵn từ đợt Library (Source Serif 4, Inter, JetBrains Mono); không thêm link font nào.

### 4.3 Khoảng cách, đường kẻ, bo góc

- Khung trang giống Library: rộng tối đa 1280 px, căn giữa; lề 30 px trên, 40 px hai bên (18 px khi ≤ 900 px).
- Cột đọc rộng tối đa **760 px**, căn trái trong khung. Khi cửa sổ ≥ 1440 px, khung đoạn trích là cột thứ hai rộng 360 px, cách 40 px.
- Masthead ngăn với phần dưới bằng **đường kẻ đôi** như Library (1 px ink, khe 4 px, 1 px rule-strong).
- Các phiếu tra cứu ngăn nhau bằng kẻ 1 px `--lib-rule-strong`, cách 32 px trên dưới.
- Khối trích: lề trái 2 px `--lib-rule-ui`, đệm trái 16 px, không nền.
- Bo góc 3 px (ô hỏi, nút, khung). Không bóng đổ, trừ ngăn kéo đoạn trích khi phủ lên nội dung (cùng bóng với menu của Library).

### 4.4 Icon

Dùng lại bộ icon nét 24/1.6 của Library (`LIB_ICONS`): `plus` (New enquiry), `open` (mở văn bản), `download` (DOCX), `x` (đóng khung), `check` (đã chép), `warn` (lỗi). Thêm 4 icon cùng kiểu: `ask` (bong bóng có dấu hỏi, cho sidebar), `arrow` (nút Ask, câu hỏi gợi ý), `copy`, `passages` (danh sách đoạn). Mã SVG có trong bản mẫu.

### 4.5 Trạng thái của một phiếu tra cứu

| Trạng thái | Nhãn | Thể hiện |
|---|---|---|
| Đang tra | "Searching the regulations… 8 s" | Vòng xoay của Library (màu busy) và đồng hồ giây; vòng đứng yên khi `prefers-reduced-motion` |
| Có trích dẫn | "Answer · quoted from 2 of 5 documents found · 12.9 s" | Thân câu trả lời, danh sách nguồn, các nút |
| Không có trích dẫn | "Answer · no provision matched · 13.1 s" | Thông điệp của server nguyên văn và một dòng hướng dẫn (M14) |
| Lỗi | "No answer" (màu bad) | Thông điệp lỗi và nút "Try again" |

---

## 5. Bố cục và thành phần

### 5.1 Khung trang

**≥ 1440 px, chưa có câu hỏi**

```
┌──────────────┬─────────────────────────────────────────────────────────────────────┐
│ [IU] ARRS    │  INTERNATIONAL UNIVERSITY · VNU-HCM                                   │
│              │  Ask the Regulations                                    (serif 34)    │
│ ▌ Ask        │  Ask in Vietnamese. Each answer quotes the articles and clauses       │
│   Library 34 │  of the regulations in the Library, with the page they come from.     │
│              │  Answers are quoted from the regulations, not generated.              │
│              │  Searches 34 documents in 7 collections · Browse the Library          │
│              │  ═══════════════════════════════════════════════════════════════      │
│              │  COMMON QUESTIONS                                                     │
│              │  Điều kiện xét học bổng khuyến khích học tập là gì?              →    │
│              │  Sinh viên vi phạm thi hộ lần đầu bị xử lý như thế nào?          →    │
│              │  Thời gian tối đa hoàn thành chương trình đại học là bao lâu?    →    │
│              │  Điều kiện để được xét tốt nghiệp sớm?                           →    │
│              │  THE SEARCH COVERS                                                    │
│              │  Luật quốc gia 9 · Thông tư Bộ GD&ĐT 3 · Quyết định ĐHQG-HCM 4 · …    │
│              ├─────────────────────────────────────────────────────────────────────┤
│ ● System     │  ┌────────────────────────────────────────────────┬────────┐         │
│   online     │  │ Ask about any IU or MOET regulation…           │ Ask  → │         │
│              │  └────────────────────────────────────────────────┴────────┘         │
│              │  Enter to ask · Shift+Enter for a new line · Answers are quoted…     │
└──────────────┴─────────────────────────────────────────────────────────────────────┘
```

**≥ 1440 px, đang có hỏi–đáp và mở khung đoạn trích**

```
│  (masthead như trên; bên phải có nút [+ New enquiry])                                │
│  ═══════════════════════════════════════════════════════════════════════════════     │
│  ENQUIRY 1 · 23:40                                 │ Passages consulted       [×]    │
│  Thời gian tối đa hoàn thành chương trình đại      │ Enquiry 1 · 9 passages, as      │
│  học là bao lâu?                         (serif 20)│ ranked by the search            │
│  ANSWER · QUOTED FROM 2 OF 2 DOCUMENTS · 11.5 s    │ ─────────────────────────────── │
│  1. Điều 2 – Chương trình đào tạo và thời gian ¹   │ 1  Quy chế đào tạo trình độ     │
│  Khoản 10                                          │    đại học theo hệ thống tín…   │
│  ┃ 10. Thời gian tối đa để sinh viên hoàn thành    │    Decision · Điều 2, Khoản 10  │
│  ┃ chương trình đào tạo là 1,5 lần…  (serif 16)    │    · p. 5 · [1]    rerank 0.97  │
│  …                                                 │    10. Thời gian tối đa để…     │
│  SOURCES                                           │ 2  …                            │
│  Cited in the answer                               │                                 │
│  [1] Quy chế đào tạo trình độ đại học theo hệ      │                                 │
│      thống tín chỉ tại Trường Đại học Quốc tế      │                                 │
│      Decision · No. 719/QĐ-ĐHQT · 2021 · p. 5      │                                 │
│      15.-QD-719-6.12.2021_Quy-che-Hoc-vu-…pdf      │                                 │
│  [Copy answer]   [Passages consulted (9)]          │                                 │
│  ───────────────────────────────────────────────   │                                 │
│  ENQUIRY 2 · 23:44 …                               │                                 │
```

Khi hẹp hơn 1440 px, khung đoạn trích là ngăn kéo rộng 380 px trượt ra từ mép phải và phủ lên nội dung; cột đọc không đổi.

### 5.2 Masthead

| Phần | Nội dung |
|---|---|
| Dòng tên trường | "International University · VNU-HCM" (giống Library) |
| Tiêu đề | "Ask the Regulations" (serif 34/600) |
| Câu giới thiệu | "Ask in Vietnamese. Each answer quotes the articles and clauses of the regulations in the Library, with the page they come from. Answers are quoted from the regulations, not generated." |
| Dòng phạm vi | "Searches **34** documents in **7** collections · Browse the Library". Số lấy từ `GET /documents` (cùng cách đếm với dòng thống kê của Library); "Browse the Library" chuyển sang trang Library. Khi chưa tải được số: chỉ hiện "Browse the Library". |
| Nút | "New enquiry" (`lib-btn`, icon `plus`), đặt bên phải như nhóm nút của Library; chỉ hiện khi đã có ít nhất một phiếu. Giữ `id="clear-chat"`. |

Masthead cuộn cùng trang (không dính); đường kẻ đôi ở dưới.

### 5.3 Ô hỏi

- Nằm cố định ở cuối màn hình như hiện tại; nền giấy, kẻ trên 1 px `--lib-rule-strong`. Bên trong dùng cùng khung 1280 px và lề của trang, nên ô hỏi thẳng mép trái với cột đọc; ô rộng tối đa 760 px.
- Ô: nền trắng, viền 1 px `--lib-rule-ui`, bo 3 px, cao tối thiểu 46 px (bằng ô tra cứu Library); khi focus có viền navy 2 px như ô tra cứu. Chữ Inter 15. Tự cao lên theo nội dung, tối đa **160 px** (hiện 120 px).
- Nút "Ask" (`lib-btn-primary`, icon `arrow`) nằm trong ô, bên phải. Khoá khi đang chờ câu trả lời (giữ `id="chat-send"`).
- Chữ gợi ý trong ô: "Ask about any IU or MOET regulation…".
- Dòng dưới ô (Inter 12, ink-3): "<kbd>Enter</kbd> to ask · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line · Answers are quoted from the regulations, not generated." Kiểu `<kbd>` giống ô tra cứu Library.

### 5.4 Màn đầu (chưa có câu hỏi)

- **Common questions:** nhãn nhóm, rồi danh sách 4 câu hỏi hiện có (giữ nguyên văn, M6). Mỗi câu là một `<button>` chiếm hết bề ngang cột: chữ serif 16, mũi tên bên phải; kẻ 1 px `--lib-rule` giữa các dòng, kẻ 1 px ink ở trên. Trỏ chuột: nền `--lib-hover`, gạch chân chữ, mũi tên chuyển navy. Bấm: điền câu hỏi vào ô và gửi, như `sendSuggest` hiện nay.
- **The search covers:** nhãn nhóm, rồi một dòng liệt kê các collection theo thứ bậc văn bản của Library kèm số văn bản: "Luật quốc gia 9 · Thông tư Bộ GD&ĐT 3 · Quyết định ĐHQG-HCM 4 · Quy chế trường ĐHQT 11 · Nội quy / quyết định ngắn ĐHQT 4 · Phụ lục 2 · Thông báo 1". Chỉ là chữ, không phải bộ lọc. Không có dữ liệu thì ẩn cả mục.
- Khi đã có phiếu đầu tiên, màn đầu ẩn đi (như `#chat-welcome` hiện nay); "New enquiry" đưa nó trở lại.

---

### 5.5 Phiếu tra cứu (enquiry record)

Mỗi lượt hỏi–đáp là một `<article>` trong cột đọc; các phiếu ngăn nhau bằng kẻ 1 px `--lib-rule-strong`.

```
ENQUIRY 3 · 23:44                                               ← nhãn nhóm, ink-3
Thời gian tối đa hoàn thành chương trình đại học là bao lâu?    ← serif 20/600, giữ xuống dòng người dùng gõ
ANSWER · QUOTED FROM 2 OF 2 DOCUMENTS · 11.5 s                  ← nhãn nhóm
<thân câu trả lời: tiêu đề vị trí, nhãn Khoản, đoạn trích>      ← 5.6
SOURCES                                                         ← 5.7
[Copy answer]   [Passages consulted (9)]                        ← lib-btn-quiet, icon nét
```

| Phần | Quy tắc |
|---|---|
| Nhãn "Enquiry" | "Enquiry {i} · {giờ:phút}". Số thứ tự đếm từ 1 và đặt lại khi "New enquiry". Giờ theo định dạng hiện có (`toLocaleTimeString('vi-VN')`). |
| Câu hỏi | Chữ người dùng gõ, escape HTML, giữ xuống dòng (`white-space: pre-wrap`). |
| Nhãn "Answer" | Có nguồn: "Answer · quoted from {c} of {n} documents found · {t} s"; khi c = n: "quoted from {n} document(s)". Không có khối nguồn: "Answer · no provision matched · {t} s". {t} lấy trường `elapsed` của server, làm tròn 1 chữ số; nếu thiếu thì lấy thời gian đo ở trình duyệt, như hiện nay. |
| Khi chờ | Thay cho phần Answer: vòng trạng thái của Library và "Searching the regulations… {s} s"; {s} tăng mỗi giây. |
| Lỗi | Nhãn "No answer" màu bad; một dòng mô tả (6); chi tiết server (nếu có) bằng chữ mono nhỏ, tối đa 600 ký tự; nút "Try again" gửi lại **đúng câu hỏi đó** và thay phần lỗi bằng câu trả lời mới. |
| Nút | "Copy answer" (icon `copy`; đổi thành "Copied" có icon `check` trong 1.8 s, như hiện nay). "Passages consulted ({số đoạn})" mở khung đoạn trích của phiếu này (5.8). |

### 5.6 Trình bày câu trả lời: Markdown của server → trang

Thân câu trả lời vẫn được dựng bằng `renderMarkdown()` hiện có (cùng `marked`, cùng tuỳ chọn). Sau đó UI gắn lớp CSS và liên kết; **không xoá chữ nào ngoài khối nguồn**, vì khối nguồn được trình bày lại đầy đủ ở 5.7.

| # | Trong Markdown | Nhận ra bằng | Trình bày |
|---|---|---|---|
| R1 | Khối nguồn cuối bài | Chuỗi `\n---\n**Tài liệu đính kèm:**\n` và các dòng `[n]: …` sau nó (7.2) | Tách khỏi thân bài, trình bày thành danh sách nguồn (5.7). Nếu không tách được dòng nào thì giữ nguyên văn bản cũ, trình bày như hiện nay (không mất gì). |
| R2 | Tiêu đề vị trí | Đoạn chỉ gồm một `<strong>` và các dấu `[n]` | Lớp `ask-loc`: Inter 14.5/600, ink; khoảng cách trên 18 px. Số thứ tự "1." ở đầu giữ nguyên. |
| R3 | Tiêu đề là tên file (M4) | Chữ trong `<strong>` (bỏ "i. " đầu) là tên file `.pdf`/`.docx`, có thể có "<số hiệu> – " đứng trước | Hiện tên văn bản từ catalog thay cho tên file; dòng dưới là tên file bằng chữ mono. Không có trong catalog thì giữ nguyên chữ. |
| R4 | Số trích | `[n]` trong thân bài, với n có trong khối nguồn | Liên kết `[n]` màu navy, cỡ 13 px (kiểu trích dẫn IEEE); bấm thì cuộn tới mục nguồn n và tô nền vàng nhạt trong 2 s (không đổi `location.hash`). Số không có trong khối nguồn giữ là chữ. |
| R5 | Nhãn Khoản | Đoạn chỉ gồm một `<em>` hoặc `<strong>` kết thúc bằng ":" (`*Khoản 9:*`, `**Khoản 2:**`) | Lớp `ask-clause`: Inter 12.5/600, ink-2; giữ nguyên chữ. |
| R6 | Đoạn trích | `<blockquote>` | Serif 16/1.65, ink; lề trái 2 px `--lib-rule-ui`, đệm 16 px. Danh sách đánh số bên trong (`> 6. …` thành `<ol start="6">`) giữ số Khoản ở đầu dòng. |
| R7 | Câu liên quan nhất | `<strong>` nằm trong `<blockquote>` | Tô nền `--lib-hl`, bỏ in đậm (M5); chỉ bằng CSS. |
| R8 | Ghi chú trích một phần | Đoạn chỉ gồm `<em>` bắt đầu bằng "(Trích " | Lớp `ask-note`: Inter 12.5, ink-3. |
| R9 | Tên file kèm trang trong thân bài | `… .pdf — trang N` | Liên kết như hiện nay (`linkifyFootnotePdfs`, không đổi), màu navy. |
| R10 | Câu không tìm thấy | Câu trả lời không có khối nguồn, không có `[n]`, không có khối trích | Hiện nguyên văn bằng serif 16, ink-2; dưới là dòng hướng dẫn "Try other words — the name of the regulation or an article number — or browse the Library." (M14). |
| R11 | Markdown khác (đoạn văn, gạch đầu dòng, in đậm ngoài khối trích) | — | Kiểu mặc định của thân bài: Inter 15/1.7, ink. |

### 5.7 Danh sách nguồn (Sources)

```
SOURCES
Cited in the answer
[1]  Quy chế công tác sinh viên Trường Đại học Quốc tế                  ← serif 15/600, liên kết
     Decision · No. 967/QĐ-ĐHQT · Trường Đại học Quốc tế · 2022 · p. 26  ← Inter 12.5, ink-2
     Quy-che-CTSV-theo-QD-967-12.2022-Signed-2.pdf  PDF                  ← mono 11.5, ink-3
▸ Also found by the search (4)                                           ← <details>, thu gọn (M15)
```

| Phần | Quy tắc |
|---|---|
| Phân nhóm | "Cited in the answer": các số `[n]` xuất hiện trong thân bài. "Also found by the search ({k})": các dòng còn lại của khối nguồn. Nếu thân bài không trích số nào, mọi dòng vào "Also found". Thứ tự: theo số `n`. |
| Tên văn bản | Từ catalog và registry qua `GET /documents` (cùng quy tắc với Library: tiêu đề nhập lúc upload → `catalog.title` → tên suy từ file). |
| Dòng mô tả | Loại (nhãn tiếng Anh của Library) · "No." số hiệu (catalog; nếu không có thì số hiệu ghi trong dòng nguồn của server) · cơ quan ban hành · năm · "p. {trang}". Thành phần nào trống thì bỏ. |
| Tên file | Chữ mono như Library, kèm "PDF"/"DOCX". |
| Liên kết | Tên văn bản mở `docUrl(file, trang)`: PDF mở đúng trang, DOCX tải về. Đúng URL mà liên kết hiện nay tạo ra từ dòng `[n]: … — trang N` (INV-5). |
| Mục được nhảy tới | Tô nền `--lib-hl` trong 2 s, nhận focus (`tabindex="-1"`). |

### 5.8 Khung đoạn trích (Passages consulted)

| Phần | Quy tắc |
|---|---|
| Mở | Nút "Passages consulted ({n})" của từng phiếu (`aria-controls="source-panel"`, `aria-expanded`). Khung hiện đoạn trích của đúng phiếu đó (M8); bấm nút của phiếu khác thì đổi nội dung. |
| Đầu khung | Tiêu đề "Passages consulted" (serif 17/600, giữ `id="sp-title"`); dòng phụ "Enquiry {i} · {n} passages, as ranked by the search"; nút đóng (icon `x`, `aria-label="Close"`). |
| Một đoạn | Số thứ tự (1…n, theo thứ tự server trả về); tên văn bản (serif 15/600, liên kết mở đúng trang, M9); dòng mô tả: loại · số hiệu · vị trí ("Điều 2, Khoản 10", hoặc nhãn cấu trúc của văn bản tải lên, cùng logic `levelName` hiện có) · "p. {trang}" · "[n]" nếu văn bản có trong khối nguồn; bên phải dòng mô tả là "rerank 0.97" (mono 11, ink-3, tooltip "Cross-encoder relevance score"; ẩn nếu server không trả điểm). |
| Đoạn trích | Inter 13/1.6, ink-2; hiện tối đa 4 dòng; nút chữ "Show full passage" / "Show less" (chỉ khi bị cắt). Toàn bộ chữ server trả (tối đa 800 ký tự) nằm trong DOM. |
| Rỗng | "Ask a question to see the passages consulted." |
| ≥ 1440 px | Cột phải rộng 360 px trong khung trang; dính khi cuộn (`position: sticky`), tự cuộn bên trong. Không có bóng. |
| < 1440 px | Ngăn kéo rộng 380 px, cố định ở mép phải, phủ lên nội dung, viền trái `--lib-rule-strong`, bóng như menu của Library; bấm ra ngoài hoặc Esc thì đóng. |
| ≤ 640 px | Ngăn kéo rộng hết màn hình. |
| Bàn phím | Khi mở, focus chuyển vào tiêu đề khung; Esc đóng và trả focus về nút đã mở. |
| "New enquiry" | Đóng khung và xoá nội dung. |
| ARIA | Dạng cột: `<aside aria-labelledby="sp-title">`. Dạng ngăn kéo: thêm `role="dialog"`, `aria-modal="false"`. |

---

### 5.9 Sidebar (dùng chung)

| Phần | Hiện tại | Mới |
|---|---|---|
| Nhãn | "Chat" | "Ask" (D2) |
| Tooltip (`title`) | "Chat" | "Ask the Regulations" |
| Icon | Bong bóng hội thoại | Bong bóng có dấu hỏi (icon `ask`, cùng nét 1.6) |
| `data-screen`, `aria-current`, vạch vàng, mục Library, số đếm, trạng thái hệ thống | — | Giữ nguyên |

Đây là thay đổi duy nhất mà trang Library thấy được.

### 5.10 Responsive

| Độ rộng cửa sổ | Thay đổi |
|---|---|
| ≥ 1440 px | Khung đoạn trích là cột phải 360 px |
| < 1440 px | Khung đoạn trích là ngăn kéo 380 px phủ lên nội dung |
| ≤ 900 px | Sidebar chỉ còn icon (như hiện nay); lề trang và lề thanh ô hỏi 18 px |
| ≤ 640 px | Tiêu đề trang 27 px; câu hỏi 18 px; ngăn kéo rộng hết màn hình; ô hỏi và câu hỏi gợi ý chiếm hết bề ngang |

Ở mọi độ rộng: không cuộn ngang trang; tên file dài và URL được ngắt dòng (`overflow-wrap: anywhere`).

### 5.11 Truy cập (accessibility)

- Một vùng `aria-live="polite"` ẩn thông báo ngắn: "Searching the regulations…", "Answer ready: quoted from 2 documents.", "No provision matched.", "No answer." Thân câu trả lời không bị đọc tự động (quá dài). Phiếu đang chờ có `aria-busy="true"`.
- Mỗi phiếu là `<article aria-labelledby>` trỏ tới câu hỏi (`<h2>`); "Sources" là `<h3>`.
- Sau khi gửi, focus ở lại ô hỏi (như hiện nay); khi có câu trả lời, focus không nhảy (M11).
- Phím: `/` đưa focus vào ô hỏi, trừ khi đang gõ trong một ô nhập hoặc modal đang mở (M12); Esc đóng khung đoạn trích.
- Số trích `[n]` có `aria-label="Source n: <tên văn bản>"`.
- Tương phản theo token của Library: mọi chữ ≥ 4.5:1; lề khối trích chỉ để trang trí. Vòng focus 2 px navy như Library.
- `prefers-reduced-motion`: vòng chờ đứng yên, tô nền khi nhảy tới nguồn không có hiệu ứng mờ dần.

---

## 6. Nội dung chữ (microcopy)

Nhãn giao diện bằng tiếng Anh (kế thừa D3 của Library). Câu hỏi, câu trả lời, tên văn bản, thông điệp của server giữ nguyên văn.

| Vị trí | Chữ |
|---|---|
| Sidebar | "Ask" (tooltip "Ask the Regulations") |
| Dòng tên trường | "International University · VNU-HCM" |
| Tiêu đề trang | "Ask the Regulations" |
| Câu giới thiệu | "Ask in Vietnamese. Each answer quotes the articles and clauses of the regulations in the Library, with the page they come from. Answers are quoted from the regulations, not generated." |
| Dòng phạm vi | "Searches {N} documents in {M} collections · Browse the Library" |
| Nút | "New enquiry" |
| Màn đầu | "Common questions"; "The search covers" |
| Ô hỏi | Chữ gợi ý "Ask about any IU or MOET regulation…"; nút "Ask"; dòng dưới "Enter to ask · Shift+Enter for a new line · Answers are quoted from the regulations, not generated." |
| Phiếu | "Enquiry {i} · {hh:mm}" |
| Khi chờ | "Searching the regulations… {s} s" |
| Nhãn câu trả lời | "Answer · quoted from {c} of {n} documents found · {t} s"; "Answer · quoted from {n} document(s) · {t} s"; "Answer · {n} documents found · {t} s" (có khối nguồn nhưng thân bài không có số trích); "Answer · no provision matched · {t} s" |
| Không tìm thấy | Thông điệp server nguyên văn, rồi "Try other words — the name of the regulation or an article number — or browse the Library." |
| Nguồn | "Sources"; "Cited in the answer"; "Also found by the search ({k})"; mô tả "{Type} · No. {number} · {issuer} · {year} · p. {page}" |
| Liên kết tên văn bản (tooltip) | "Open {file} at page {p}"; DOCX: "Download {file}" |
| Nút của phiếu | "Copy answer" → "Copied"; "Passages consulted ({n})" |
| Khung đoạn trích | "Passages consulted"; "Enquiry {i} · {n} passages, as ranked by the search"; "rerank {0.00}" (tooltip "Cross-encoder relevance score"); "Show full passage" / "Show less"; nút đóng `aria-label` "Close"; rỗng: "Ask a question to see the passages consulted." |
| Lỗi | Nhãn "No answer"; "The server returned an error (HTTP {status})."; "Could not reach the server."; "No answer after 3 minutes."; "The server's answer could not be read." (response không đọc được); "The answer could not be displayed." (lỗi khi trình bày câu trả lời); nút "Try again" |
| Thông báo cho trình đọc màn hình | "Searching the regulations…"; "Answer ready: quoted from {c} documents."; "No provision matched."; "No answer." |

Chữ bỏ đi: "Welcome to ARRS", đoạn giới thiệu cũ, "Ask a question about IU academic regulations…", "📋 References", "Send a question to see source documents.", "📋 Copy", "✓ Copied", "API error (…)", "Connection error: …", "(không có câu trả lời)". Nội dung của hai thông điệp lỗi cũ (mã HTTP, nội dung lỗi) vẫn được hiện ở phần chi tiết của lỗi.

---

## 7. Dữ liệu

### 7.1 `POST /chat` và D4

`chain.query()` trả mỗi nguồn dạng `{chunk_id, source, page, article, khoan, text, score_rrf, score_rerank, level_labels, file_type, language}`. `_doc_to_source` (`api/main.py`) lại đọc `_score_rrf` và `_score_rerank`, nên hai điểm luôn `null`. Sửa (D4):

```python
        score_rrf     = d.get("_score_rrf", d.get("score_rrf")),
        score_rerank  = d.get("_score_rerank", d.get("score_rerank")),
```

- `/chat`: hai điểm có giá trị; câu trả lời và các trường khác giữ nguyên.
- `/retrieve`, `/search`: dict của chúng mang khoá `_score_*`, nên giá trị không đổi (INV-3).
- `doc_type`, `doc_number`, `article_title`, `chapter` vẫn `null` ở `/chat`, vì chain không trả; trang Ask lấy loại, số hiệu, tên văn bản từ catalog (7.3). Muốn có tiêu đề Điều phải sửa `src/chat/rag_chain.py`: ngoài phạm vi.

### 7.2 Tách khối nguồn

1. Tìm lần xuất hiện **cuối** của `\n---\n**Tài liệu đính kèm:**\n`. Phần trước là thân bài; phần sau là các dòng nguồn.
2. Mỗi dòng nguồn khớp `^\[(\d+)\]: (.+) — trang (\S+)$` cho ra: số `n`, nhãn, trang (số, hoặc `?`).
3. Nhãn tách theo " – " (gạch ngang dài có khoảng trắng hai bên): phần cuối là tên file; phần trước (nếu có) là số hiệu mà server lấy từ JSONL. Cách này đúng cả khi tên file có khoảng trắng.
4. Dòng nào không khớp thì giữ nguyên văn, hiện ở cuối danh sách nguồn. Không có dòng nào khớp thì không tách, câu trả lời hiện như bây giờ (R1).

### 7.3 Catalog ở trang Ask

- Khi mở trang (Ask là trang mặc định), gọi `GET /groups` (`loadGroups()` có sẵn) và `GET /documents` **một lần**, lưu thành bảng tra `tên file → dòng registry` riêng của trang Ask. Không gọi `loadDocuments()` của Library; trạng thái của Library (`allDocs`, bộ lọc, poll) không bị đụng tới.
- Tên, số hiệu, cơ quan, năm, loại lấy bằng các hàm có sẵn của Library: `libTitle`, `libNumber`, `libIssuer`, `libYear`, `libTypeLabel`, `libNameFromFile`. Các hàm này chỉ đọc dữ liệu, không có tác dụng phụ. Dòng phạm vi và "The search covers" dùng `libGroupKey`, `libRank`, `libGroupLabel`.
- Khi một câu trả lời nhắc tới file chưa có trong bảng tra (văn bản vừa tải lên), gọi lại `GET /documents` một lần rồi trình bày lại phần nguồn của phiếu đó.
- `GET /documents` lỗi hoặc không có `catalog`: tên suy từ tên file; số hiệu lấy từ dòng nguồn của server; ẩn dòng phạm vi (INV-7).

### 7.4 Nguyên tắc

Dữ liệu trả về từ server không bị sửa. Mọi thay đổi chỉ nằm ở phần trình bày; nội dung bị trình bày lại (khối nguồn, tiêu đề là tên file) vẫn giữ đủ thông tin gốc ở chỗ khác trên trang (INV-5).

---

## 8. Thiết kế kỹ thuật

### 8.1 Hợp đồng DOM

| Id / điểm móc | Trạng thái | Dùng ở |
|---|---|---|
| `#screen-chat`, `data-screen="chat"` | Giữ | `showScreen()`, sidebar |
| `#chat-history` | Giữ: khung cuộn của trang | JS Ask |
| `#chat-welcome` | Giữ: màn đầu (Common questions, The search covers) | JS Ask |
| `#chat-messages` | Giữ: nơi chứa các phiếu | JS Ask |
| `#chat-input`, `#chat-send` | Giữ: ô hỏi, nút "Ask" | JS Ask |
| `#source-panel`, `#sp-body`, `#sp-title` | Giữ: khung đoạn trích | JS Ask |
| `#clear-chat` | Giữ: nút "New enquiry" | JS Ask |
| `window.sendSuggest` | Giữ: nút câu hỏi gợi ý gọi hàm này | HTML |
| `#toggle-sources`, `#chat-model-pill`, `.topbar` của Chat | Bỏ: nằm trong thanh bị ẩn, không ai thấy; chức năng chuyển sang nút của từng phiếu | — |
| Mới | `#ask-scope` (dòng phạm vi), `#ask-covers` (The search covers), `#ask-live` (thông báo ẩn), `enq-{i}` (phiếu), `enq-{i}-src-{n}` (mục nguồn) | JS Ask |

### 8.2 CSS

- Khối mới `/* ASK — the Chat screen (design: docs/design/chat-ask-redesign.md) */` thay cho khối `/* CHAT */` (dòng 90–161) và các quy tắc của Chat trong khối responsive (dòng 512–516 và 531–536). Lớp mới mang tiền tố `ask-`, đặt dưới `#screen-chat`.
- Dùng lại lớp của Library mà không sửa chúng: `lib-kicker`, `lib-h1`, `lib-lede`, `lib-summary`, `lib-btn`, `lib-btn-primary`, `lib-btn-quiet`, `lib-rule`, `lib-link`, `lib-i`, `lib-sr`, vòng trạng thái `lib-st .mk`. Các quy tắc Library gắn với `#screen-documents` (vòng focus, `mark`, spinner) có bản tương ứng dưới `#screen-chat`.
- Trước khi xoá một lớp cũ (`.msg`, `.avatar`, `.bubble…`, `.copy-btn`, `.src-chip`, `.typing-…`, `.source-panel`, `.sp-…`, `.sq-…`, `.chat-…`, `.send-btn`), `grep` để chắc chỉ Chat dùng; lớp nào còn nơi khác dùng thì giữ.
- CSS của Library (dòng 209–350), modal, toast, `.spinner` giữ nguyên từng byte (G3).

### 8.3 JavaScript

| Hàm | Thay đổi |
|---|---|
| `api`, `escapeHtml`, `toast`, `docUrl`, `renderMarkdown`, `linkifyFootnotePdfs`, `levelName`, `usesGenericLabels`, `loadGroups` | Giữ nguyên, dùng lại |
| `libTitle`, `libNumber`, `libIssuer`, `libYear`, `libTypeLabel`, `libNameFromFile`, `libGroupKey`, `libRank`, `libGroupLabel` | Giữ nguyên; trang Ask chỉ gọi để đọc |
| `sendChat()` | Cùng yêu cầu: `POST /chat`, header JSON, body `{question}`, `AbortSignal.timeout(180000)`. Thêm cờ chờ (M16). Tạo phiếu ở trạng thái chờ; trả lời được thì trình bày câu trả lời; lỗi HTTP hoặc lỗi mạng thì trình bày lỗi; cuối cùng mở khoá nút và đưa focus về ô hỏi (như hiện nay). |
| `appendMessage`, `showTyping`, `removeTyping` | Thay bằng `askAddEntry`, `askSetWaiting`, `askRenderAnswer`, `askRenderError` |
| `renderSourcesPanel` | Thay bằng `askOpenPassages(entry)` |
| `sendSuggest` | Giữ hành vi: lấy chữ của câu hỏi gợi ý, điền vào ô, gửi |
| Nút `#clear-chat` | Cùng tác dụng (xoá các phiếu, hiện lại màn đầu, xoá khung đoạn trích); thêm: đặt lại số thứ tự phiếu, đóng khung, dừng đồng hồ, huỷ yêu cầu đang chờ bằng `AbortController` (gộp với timeout 180 s qua `AbortSignal.any`) và bỏ qua mọi câu trả lời của phiếu đã xoá (M13) |
| Mới | `askSplitFooter`, `askParseSources`, `askDecorateBody`, `askRenderSources`, `askCopyText`, `askLoadCatalog`, `askRenderScope`; phím `/` và Esc; mở/đóng ngăn kéo |
| `lastChatSources` | Thay bằng dữ liệu lưu theo từng phiếu |

Chỉ đoạn JS của Chat (dòng 974–1192) và khối INIT (thêm lời gọi `askLoadCatalog`) thay đổi. JS của Library giữ nguyên.

### 8.4 Font, thư viện

Không thêm font hay thư viện. `marked` và `marked-footnote` giữ nguyên phiên bản và tuỳ chọn.

### 8.5 API

Chỉ 2 dòng ở 7.1. Không thêm endpoint. Version của API đổi khi phát hành (quyết định lúc đó).

---

## 9. Ma trận tác động tới các chức năng khác

| Chức năng | Có thay đổi? | Chi tiết | Được duyệt ở | Kiểm chứng |
|---|---|---|---|---|
| Trang Chat → Ask | Có | Toàn bộ giao diện; chức năng giữ nguyên | D1-a | G5 |
| Nguồn dưới câu trả lời | Có | Trình bày lại khối nguồn, thêm tên văn bản từ catalog | D1-b | G4 |
| Khung đoạn trích | Có (sửa lỗi) | Mở được, theo từng phiếu, trình bày lại, có điểm | D1-c, D3 | G5 |
| Xoá cuộc hỏi–đáp | Có (sửa lỗi) | Nút "New enquiry" bấm được | D1-d | G5 |
| Sidebar | Nhãn, tooltip, icon của mục Ask | Phần còn lại giữ nguyên | D2 | G3 |
| `POST /chat` | Điểm rerank có giá trị | Câu trả lời và các trường khác giữ nguyên | D4 | G2 |
| Endpoint khác | Không | — | — | G2 |
| `src/**`, `config.yaml`, dữ liệu, chỉ mục | Không | — | — | G1 |
| Trang Library | Không (trừ sidebar) | CSS, JS, DOM giữ nguyên | — | G3 |
| Modal tải lên, toast | Không | — | — | G3 |
| README | Có | Mục "1. Chat": tên mới, danh sách nguồn, khung đoạn trích | D1, D2 | Đọc lại |
| Test | Thêm | Unit test cho D4: `/chat` trả điểm khi chain trả khoá không gạch dưới | D4 | G6 |
| Docker, CI | Không (code) | Image mới chứa giao diện mới | — | G9 |
| Số liệu paper, GT v2 | Không | Suy ra từ INV-1 | — | G1, G2 |

---

## 10. Kiểm thử và cổng

### 10.1 Baseline (chụp trước khi sửa)

| ID | Nội dung |
|---|---|
| B1 | SHA-256 của `data/processed/*.jsonl` và `bm25.pkl`; `points_count` của `reg_chunks` |
| B2 | `POST /chat` cho 100 câu hỏi GT v2: response đầy đủ (câu trả lời, mọi trường của nguồn) |
| B3 | Snapshot `/documents`, `/groups`, `/stats`, `/sources`, `/health` (bỏ `timestamp`), `/index/health`, `/retrieve` và `/search` cho 5 câu hỏi |
| B4 | DOM `#screen-documents` sau khi tải; hash các vùng CSS, HTML, JS của Library, modal, toast |
| B5 | Unit test (83), E2E `ARRS_E2E=1` |
| B6 | Ảnh chụp trang Chat hiện tại (màn đầu, một câu trả lời) |

### 10.2 Cổng

| Cổng | Đạt khi |
|---|---|
| G1 | B1 trùng khớp; `git diff --stat src/ config.yaml data/` rỗng |
| G2 | `/chat`: 100/100 câu trả lời giống hệt B2; mọi trường của nguồn giống hệt, trừ `score_rrf` và `score_rerank` nay có giá trị ở mọi nguồn. Endpoint khác trùng từng byte với B3. |
| G3 | Vùng CSS, HTML, JS của Library, modal, toast trùng hash B4; DOM `#screen-documents` trùng B4 |
| G4 | Giữ nội dung, kiểm tự động trên 100 câu trả lời của B2 và 7 mẫu của bản mẫu, dựng bằng code mới: (a) mọi dòng khác rỗng của thân bài, bỏ ký hiệu Markdown, đều có trong chữ hiện ra; (b) mỗi dòng nguồn có đúng một mục nguồn, cùng tên file, trang và URL mà liên kết cũ tạo ra; (c) mỗi `[n]` trong thân bài có mục nguồn tương ứng đều thành liên kết tới mục đó; (d) câu trả lời không có khối nguồn hiện nguyên văn |
| G5 | Toàn bộ kịch bản 10.3 đạt; log network khớp Phụ lục A |
| G6 | Unit test cũ và mới, E2E đều qua |
| G7 | Rộng 1440, 1439, 1280, 1100, 900, 640, 375 px: không cuộn ngang, không chữ đè; khung đoạn trích chuyển cột ↔ ngăn kéo đúng ngưỡng |
| G8 | Tương phản (quét như A2 của Library), đường đi bằng bàn phím, thông báo `aria-live` |
| G9 | Docker: image dựng từ cây phát hành; `/chat` trả điểm; trang Ask hiện đúng |

### 10.3 Kịch bản chức năng

Chạy trên server riêng với bản sao dữ liệu, như khi kiểm thử Library. Lỗi mạng và lỗi server được giả lập bằng cách thay `fetch` trong trang kiểm thử, không đụng tới server.

| ID | Kịch bản |
|---|---|
| C1 | Màn đầu: masthead, số ở dòng phạm vi khớp dòng thống kê của Library, 4 câu hỏi gợi ý, dòng "The search covers" |
| C2 | Hỏi bằng Enter |
| C3 | Hỏi bằng nút "Ask" |
| C4 | Hỏi bằng câu hỏi gợi ý (`sendSuggest`) |
| C5 | Shift+Enter xuống dòng; câu hỏi nhiều dòng hiện đúng xuống dòng |
| C6 | Ô hỏi tự cao tới 160 px rồi cuộn bên trong |
| C7 | Câu hỏi rỗng hoặc chỉ có khoảng trắng: không gửi |
| C8 | Khi chờ: nút khoá, Enter không gửi thêm (M16), đồng hồ đếm giây, thông báo `aria-live` |
| C9 | Câu trả lời có trích dẫn: nhãn đếm đúng c/n; `[n]` nhảy tới nguồn và tô sáng; "Cited" và "Also found" đúng; URL tên văn bản đúng `docUrl(file, trang)`; DOCX tải về |
| C10 | Tiêu đề là tên file hiện tên văn bản và tên file (M4) |
| C11 | Câu liên quan nhất được tô nền (M5) |
| C12 | Ghi chú "Trích k/n phần" hiện đúng |
| C13 | Không tìm thấy: nguyên văn, dòng hướng dẫn, nút khung đoạn trích vẫn có |
| C14 | Server trả lỗi (giả lập HTTP 500): "No answer", mã lỗi, chi tiết; "Try again" gửi lại đúng câu hỏi |
| C15 | Lỗi mạng (giả lập): "Could not reach the server." |
| C16 | Hết thời gian (giả lập): "No answer after 3 minutes." |
| C17 | Không có `data/catalog.json`: tên suy từ tên file, số hiệu từ dòng nguồn |
| C18 | `GET /documents` lỗi: như C17, không lỗi console, dòng phạm vi ẩn |
| C19 | "Copy answer": nội dung chép gồm thân bài và nguồn có tên văn bản (M10) |
| C20 | Khung đoạn trích: mở từ phiếu 1 rồi phiếu 2 (nội dung đổi), đóng bằng ×, Esc, bấm ra ngoài (ngăn kéo); focus trở về nút; điểm rerank; "Show full passage" |
| C21 | "New enquiry": xoá phiếu, số thứ tự về 1, đóng khung, hiện màn đầu; bấm khi đang chờ thì yêu cầu bị huỷ, nút Ask mở ngay, không có câu trả lời nào đến muộn |
| C22 | Chuyển Ask ↔ Library: cuộc hỏi–đáp còn nguyên; Library hoạt động như cũ |
| C23 | Phím `/` ở Ask (không kích hoạt khi đang gõ); `/` ở Library vẫn như cũ |
| C24 | Chấm trạng thái hệ thống ở sidebar không đổi |
| C25 | Câu trả lời dài nhất (4.894 ký tự) đọc được, không tràn ngang |
| C26 | Tải lại trang: cuộc hỏi–đáp mất, như hiện nay (không lưu) |

---

## 11. Kế hoạch triển khai

| Bước | Nội dung | Checkpoint |
|---|---|---|
| P1 | Baseline B1–B6 | — |
| P2 | Sửa `_doc_to_source` (D4) và thêm unit test; chạy G2 | CP1: báo cáo G2 |
| P3 | Giao diện: CSS, HTML, JS của trang Ask; nhãn sidebar | CP2: tác giả xem trên trình duyệt |
| P4 | Chạy cổng G1–G9; cập nhật README mục "1. Chat" | CP3: báo cáo kết quả |
| P5 | Commit theo phần (API; giao diện; README và tài liệu). Phát hành (tag, đẩy GitHub, image) chỉ khi được đồng ý. | — |

---

## 12. Rủi ro và giả định

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Server đổi định dạng khối nguồn thì việc tách hỏng | Thấp | Không tách được thì hiện nguyên văn như bây giờ (R1); G4 chạy trên 100 câu thật |
| Tên văn bản (catalog) khác chữ server viết trong thân bài (tên file) | Trung bình | Luôn hiện tên file bằng chữ mono cạnh tên văn bản (M4, 5.7) |
| Người dùng quen bong bóng chat | Thấp | Ô hỏi vẫn ở cuối màn hình, Enter vẫn gửi; câu hỏi gợi ý vẫn một lần bấm |
| Ngăn kéo đoạn trích che nội dung khi cửa sổ hẹp | Thấp | Đóng bằng ×, Esc, bấm ra ngoài; cột đọc không đổi vị trí |
| Thêm `GET /documents` và `GET /groups` khi mở trang | Thấp | Gọi một lần; hai endpoint này nhẹ (34 dòng), Library cũng gọi |
| D4 đổi response `/chat` | Thấp | Chỉ hai trường điểm đổi từ `null` thành số; không client nào dựa vào `null` (UI cũ hiện "—") |
| Code trang Ask gọi hàm của Library | Thấp | Chỉ gọi hàm đọc dữ liệu, không trạng thái; ghi rõ ở 8.3 |
| Câu "Tôi không tìm thấy…" ở ngôi thứ nhất trái với P2 | Chắc chắn | Chữ của server (có thể đổi trong `config.yaml`); ghi ở Phụ lục C |

---

## Phụ lục A — Kiểm kê chức năng trang Chat (hợp đồng phải giữ)

Số dòng tham chiếu `ui/index.html` tại bản 2.1.0 (tag `v2.1.0`).

| # | Chức năng | Kích hoạt | Hàm | API |
|---|---|---|---|---|
| A1 | Gửi câu hỏi | Enter (không Shift) trong ô; nút gửi | `sendChat()` (1155) | `POST /chat`, body `{question}`, header JSON, `AbortSignal.timeout(180000)` |
| A2 | Xuống dòng | Shift+Enter | `keydown` (990) | — |
| A3 | Ô nhập tự cao | Gõ chữ | `input` (986), tối đa 120 px | — |
| A4 | Bỏ qua câu rỗng | `trim()` rỗng | `sendChat()` | — |
| A5 | Ẩn màn đầu | Khi gửi | `chatWelcome.classList.add('hidden')` | — |
| A6 | Khoá nút khi chờ, mở lại khi xong, focus về ô | Khi gửi | `chatSend.disabled`, `finally` | — |
| A7 | Chỉ báo chờ | Khi gửi | `showTyping()` / `removeTyping()` | — |
| A8 | Câu hỏi gợi ý | Bấm thẻ | `sendSuggest(card)` (1007) | Như A1 |
| A9 | Trình bày câu trả lời | Sau A1 | `appendMessage('ai')` → `renderMarkdown()` (`marked`: gfm, breaks; `marked-footnote`) → `linkifyFootnotePdfs()` | — |
| A10 | Mở văn bản từ câu trả lời | Bấm "… .pdf — trang N" | `docUrl(file, page)`: PDF → `/pdf/{file}#page=N`; loại khác → `/documents/{file}/file` | `GET /pdf/…`, `GET /documents/…/file` |
| A11 | Dòng meta | Sau A9 | Giờ (`vi-VN`), thời gian (`elapsed` của server hoặc đo ở trình duyệt), nút Copy | — |
| A12 | Chép câu trả lời | Nút Copy | `navigator.clipboard.writeText(bubble.innerText)`; "✓ Copied" trong 1.8 s | — |
| A13 | Lỗi HTTP | `!r.ok` | "API error (status): body" | — |
| A14 | Lỗi mạng, hết giờ | Exception | "Connection error: message" | — |
| A15 | Danh sách References | Sau A9 | `renderSourcesPanel()` (1122): số, file, trang, điểm, 260 ký tự đầu, thẻ nhóm/loại/Điều; bấm mở PDF (không có trang) hoặc tải DOCX. **Khung không hiện (B1).** | — |
| A16 | Bật/tắt References | `#toggle-sources` | **Không bấm được (B1)** | — |
| A17 | Xoá cuộc hỏi–đáp | `#clear-chat` | **Không bấm được (B2)** | — |
| A18 | Giữ cuộc hỏi–đáp khi chuyển trang | `showScreen()` | DOM giữ nguyên | — |
| A19 | Không lưu qua lần tải trang | — | — | — |
| A20 | Trạng thái hệ thống ở sidebar (dùng chung) | Mỗi 30 s | `checkHealth()` | `GET /health` |

---

## Phụ lục B — Bằng chứng khảo sát (2026-09-27 và 2026-09-28, chỉ đọc)

- Server cổng 8000 (bản 2.1.0), cửa sổ 1280×720, hỏi "Điều kiện xét học bổng khuyến khích học tập là gì?": trả lời sau 11.4 s; `#source-panel` mang lớp `hidden source-panel`, rộng 0 px, độ mờ 0; `#screen-chat > .topbar` có `display: none`; `#sp-title` là "📋 References (10)"; dòng meta "23:40 ⏱ 11.37s 📋 Copy".
- 7 response `/chat` đầy đủ (4 câu hỏi gợi ý và 3 câu GT): nguồn chỉ có `chunk_id`, `source`, `page`, `text`, `article`, `khoan`. 100 response ghi ngày 2026-09-27: `score_rerank` là `null` ở 953/953 nguồn.
- Định dạng câu trả lời trên 100 câu GT v2: số liệu ở 2.3.
- Ảnh chụp của tác giả (cửa sổ 1906 px): thanh ô nhập tràn hết chiều ngang; màn đầu trống phần lớn.
- Catalog: 34/34 văn bản có mục trong `data/catalog.json` (bản 2.1.0).

---

## Phụ lục C — Vấn đề quan sát được nhưng **không** sửa (cần duyệt riêng)

| # | Vấn đề | Vị trí | Ghi chú |
|---|---|---|---|
| C1 | Khối nguồn của server liệt kê mọi văn bản trong kết quả tra, kể cả văn bản không được trích (54/93 câu) | `build_citations_footer` (`src/chat/rag_chain.py`) | UI tách "Cited" và "Also found". Sửa ở server thì câu trả lời đổi. |
| C2 | Số hiệu trong khối nguồn lấy từ JSONL, có chỗ sai do OCR: `1524/QĐÐ-ĐHQG` (đúng là 1342/QĐ-ĐHQG), `953/QD-DHQG` (đúng là 953/QĐ-ĐHQG) | JSONL (O3 của Library) | UI hiện số hiệu của catalog. |
| C3 | Câu "Tôi không tìm thấy thông tin này trong tài liệu hiện có." ở ngôi thứ nhất | `not_found_message` (`rag_chain.py`, `config.yaml`) | Hiện nguyên văn (M14); tác giả có thể đổi câu trong cấu hình. |
| C4 | `/chat` không trả `doc_type`, `doc_number`, `article_title`, `chapter` | `chain.query()` | Khung đoạn trích hiện "Điều 2, Khoản 10" nhưng không có tên Điều. |
| C5 | Một số tiêu đề Điều có lỗi OCR ("Công nhận tot nghiệp va cấp bằng") | JSONL | Hiện nguyên văn. |
| C6 | `DOC_TYPE_LABELS`, `typeLabel()`, `groupLabel()` không còn được trang Ask dùng (loại văn bản lấy từ `libTypeLabel`) | `ui/index.html` | Giữ lại, không xoá; mã thô `appendix`, `university_decision`, `announcement` (O1 của Library) không còn hiện ra. |
| C7 | Chart.js được tải nhưng không dùng; CSS các màn hình đã bỏ | `ui/index.html` | O6 của Library. |

---

## Phụ lục D — Ví dụ chuyển đổi (câu trả lời thật → trình bày)

**D.1 Một Khoản — câu hỏi gợi ý 1**

Server trả:

```
**Điều 29, Khoản 6 – Sử dụng kết quả rèn luyện** [1]

> 6. Để xét cấp học bổng tài trợ của các tổ chức và doanh nghiệp và xét học bổng khuyến khích học kỳ sinh viên phải đạt ĐRL từ mức Tốt 80 điểm trở lên.

---
**Tài liệu đính kèm:**
[1]: Quy-che-CTSV-theo-QD-967-12.2022-Signed-2.pdf — trang 26
[2]: 16.-Quy-dinh-xet-cap-hoc-bong-tai-Truong-DHQT-Signed-4.pdf — trang 1
[3]: 43/2019/QH14 – 32.Luat-Giao-duc-2019.pdf — trang 33
[4]: 953/QD-DHQG – 20.-2019-Quy-che-CTSV-DHQG-HCM.pdf — trang 7
[5]: 9.-QD688-MGHP-2024.pdf — trang 8
```

Trang hiện:

```
ANSWER · QUOTED FROM 1 OF 5 DOCUMENTS FOUND · 12.9 s
Điều 29, Khoản 6 – Sử dụng kết quả rèn luyện [1]
┃ 6. Để xét cấp học bổng tài trợ của các tổ chức và doanh nghiệp và xét học bổng
┃    khuyến khích học kỳ sinh viên phải đạt ĐRL từ mức Tốt 80 điểm trở lên.
SOURCES
Cited in the answer
[1] Quy chế công tác sinh viên Trường Đại học Quốc tế
    Decision · No. 967/QĐ-ĐHQT · Trường Đại học Quốc tế · 2022 · p. 26
    Quy-che-CTSV-theo-QD-967-12.2022-Signed-2.pdf  PDF
▸ Also found by the search (4)
    [2] Quy định xét cấp học bổng khuyến khích học tập cho sinh viên trình độ đại học tại Trường Đại học Quốc tế …
    [3] Luật Giáo dục · Law · No. 43/2019/QH14 · Quốc hội · 2019 · p. 33
    [4] Quy chế công tác sinh viên · Decision · No. 953/QĐ-ĐHQG · ĐHQG-HCM · 2019 · p. 7
    [5] Quy định chế độ chính sách miễn, giảm học phí · … · p. 8
[Copy answer]  [Passages consulted (10)]
```

**D.2 Tiêu đề là tên file — câu hỏi gợi ý 2**

`**1. 3.-Phu-luc-1-30122022-Signed-2.pdf** [1]` hiện thành "1. Phụ lục I — Một số nội dung vi phạm và khung xử lý kỷ luật sinh viên [1]", dòng dưới là `3.-Phu-luc-1-30122022-Signed-2.pdf` (mono). Câu server in đậm trong khối trích ("Riêng đối với hành vi thi hộ, học hộ…") được tô nền vàng nhạt. Dòng nguồn `[4]: 1524/QĐÐ-ĐHQG – 25.-QD1342_…pdf — trang 21` hiện "Quy chế đào tạo trình độ đại học · Decision · No. 1342/QĐ-ĐHQG · … · p. 21".

**D.3 Không tìm thấy — câu GT 2**

Server trả `Tôi không tìm thấy thông tin này trong tài liệu hiện có.` Trang hiện nhãn "Answer · no provision matched · 13.1 s", câu của server nguyên văn (serif, ink-2), dòng hướng dẫn tiếng Anh và nút "Passages consulted (10)" để xem các đoạn gần nhất.

---

## Phụ lục E — File dự kiến thay đổi hoặc thêm mới

| File | Loại |
|---|---|
| `ui/index.html` | Sửa: CSS, HTML, JS của trang Chat → Ask; nhãn, tooltip, icon của mục Ask trên sidebar; một lời gọi thêm trong khối INIT; 6 chuỗi của trang Library (D6) |
| `api/main.py` | Sửa: 2 dòng trong `_doc_to_source` (D4); version 2.2.0 (D7) |
| `tests/test_chat_scores.py` | Mới: unit test cho D4 |
| `README.md` | Sửa: mục giao diện "1. Chat" → "1. Ask"; các chỗ khác nhắc tới Chat, ghi chú catalog, ví dụ response `/chat` (T-11) |
| `docker-compose.yml`, `CITATION.cff` | Sửa: phiên bản 2.2.0 (D7) |
| `docs/design/chat-ask-redesign.md` | Tài liệu này |
| `docs/design/chat-ask-mockup.html` | Bản mẫu giao diện |

---

## Phụ lục F — Kiểm tra bản mẫu (2026-09-28)

Bản mẫu chứa bản dựng thử của bộ trình bày (5.6–5.8), chạy trên 5 câu trả lời thật:

| Kiểm tra | Kết quả |
|---|---|
| Một Khoản (câu gợi ý 1) | Nhãn "quoted from 1 of 5 documents found"; một tiêu đề vị trí, một khối trích; [1] là liên kết; "Cited" có 1 mục mang tên catalog; "Also found by the search (4)" thu gọn |
| Tiêu đề là tên file (câu gợi ý 2) | "1. Phụ lục I — Một số nội dung vi phạm và khung xử lý kỷ luật sinh viên [1]", tên file mono bên dưới; 3 nhãn Khoản; 2 câu tô sáng; 4 nguồn được trích |
| Nhiều Điều (câu gợi ý 3) | 4 tiêu đề vị trí, 4 nhãn Khoản, 3 câu tô sáng, 2 nguồn được trích; khung đoạn trích 9 đoạn, dòng đầu "Decision · No. 719/QĐ-ĐHQT · Điều 2, Khoản 10 · p. 5 · [1]", "rerank 0.25" |
| Không tìm thấy (câu GT 2) | Nhãn "no provision matched"; câu của server nguyên văn; dòng hướng dẫn |
| Khối nguồn thô | Không còn chuỗi "Tài liệu đính kèm" trong thân bài ở cả 5 câu |
| Tương tác (1280 px) | Khung đoạn trích mở thành ngăn kéo 380 px (`role="dialog"`, focus vào tiêu đề); Esc đóng và trả focus; `[1]` nhảy tới nguồn và tô sáng; Enter khi đang chờ bị chặn, chữ giữ trong ô; "New enquiry" xoá sạch |
| Độ rộng | 1600 px: khung đoạn trích là cột dính; 1280 px: ngăn kéo; 375 px: ngăn kéo hết bề ngang, tiêu đề 27 px, câu hỏi 18 px, sidebar 68 px. Không cuộn ngang ở cả ba. Không lỗi console. |

Bản mẫu chưa thay cho kiểm thử cổng G1–G9: những cổng đó chạy trên code thật khi triển khai.

---

## Phụ lục G — Điều chỉnh khi triển khai (2026-09-28)

| # | Điều chỉnh | Lý do |
|---|---|---|
| T-1 | Khi nạp `GET /documents`, trang Ask chép `group` sang `doc_group` cho từng dòng trước khi dùng các hàm của Library (`libGroupKey`, `libRank`, `libGroupLabel`) | Các hàm đó đọc `doc_group`. Thiếu bước này, dòng phạm vi báo 1 collection và "The search covers" chỉ có "Other 34" |
| T-2 | `renderMarkdown`, phần đăng ký footnote của `marked` và `linkifyFootnotePdfs` được giữ nguyên văn, chuyển sang khối JS của trang Ask | Ba hàm này nằm trong khối JS của trang Chat cũ; thay khối đó làm mất chúng (lỗi "renderMarkdown is not defined" khi chạy thử) |
| T-3 | Tách bốn loại lỗi: không tới được server; server trả mã lỗi; response không đọc được; lỗi khi trình bày. Thêm hai thông điệp "The server's answer could not be read." và "The answer could not be displayed." (mục 6) | Nếu không tách, lỗi xảy ra sau khi đã có response sẽ hiện "Could not reach the server.", sai với lỗi thật |
| T-4 | M4 chỉ thay tiêu đề là tên file khi văn bản có tên thật (từ catalog hoặc tên nhập khi upload). Không có tên thật thì tiêu đề giữ như server viết | Tên suy từ tên file không rõ hơn chính tên file, thay vào chỉ thêm một dòng trùng |
| T-5 | `outline:none` chỉ áp cho ô nhập `#chat-input` (khung ô hỏi đổi màu viền khi focus); nút "Ask" và các nút khác giữ vòng focus 2 px màu navy | Bản dựng đầu bỏ vòng focus trên cả khung ô hỏi, nút "Ask" mất dấu focus khi đi bằng Tab |
| T-6 | Dòng phạm vi và "The search covers" chỉ đếm văn bản có trạng thái `indexed`, cùng cách đếm với dòng "… available in Ask" của Library | Chỉ những văn bản này tra cứu được |
| T-7 | Enter trong lúc bộ gõ đang ghép chữ (`isComposing`) không gửi câu hỏi | Tránh gửi câu hỏi dở dang khi gõ tiếng Việt bằng bộ gõ có khung ghép chữ |
| T-8 | D6: thông báo sau khi upload đổi từ "… in Chat now." thành "… on the Ask page now." | "in Ask now" đọc không tự nhiên |
| T-9 | `typeLabel` và `DOC_TYPE_LABELS` (nhãn loại văn bản tiếng Việt) không còn nơi gọi sau khi bỏ khung References cũ. Giữ lại, không xoá; chú thích cạnh `LIB_TYPE_LABELS` ghi rõ điều này | Nằm ngoài khối của trang Ask; xoá là thay đổi ngoài phạm vi |
| T-10 | Khối responsive chung: bỏ quy tắc `.source-panel` ở 1100 px và `.chat-history` ở 640 px (của trang Chat cũ); quy tắc `.search-body` giữ nguyên | Các lớp đó không còn trong trang |
| T-11 | README: ngoài mục "1. Ask", ba chỗ nhắc "Chat" đổi thành "Ask" hoặc `/chat`; ghi chú catalog nay nói trang Ask cũng đọc `data/catalog.json` qua `GET /documents`; ví dụ response `/chat` dùng tên trường thật `score_rrf`, `score_rerank` (trước đó ghi `rrf_score`, `rerank_score`) | Tài liệu khớp với D4 và D6 |

---

## Phụ lục H — Kết quả kiểm thử (2026-09-28)

Code mới chạy trên một server riêng (cổng 8011) với **bản sao** thư mục `data/` và một Qdrant riêng nạp từ `data/qdrant_seed`. Baseline là code của bản 2.1.0 chạy trên cùng bản sao. Kho thật không bị ghi. Lỗi server, lỗi mạng và hết thời gian được giả lập bằng cách thay `fetch` trong trang kiểm thử. Kiểm thử Docker dùng image build từ đúng cây mã được phát hành.

### H.1 Cổng

| Cổng | Kết quả |
|---|---|
| G1 | Đạt. 34 file JSONL và `bm25.pkl` của kho thật trùng SHA-256 với baseline; collection `reg_chunks` vẫn có 4.224 điểm; `git diff --stat src/ config.yaml data/` rỗng. |
| G2 | Đạt. 100/100 câu hỏi GT v2 có câu trả lời giống hệt baseline. 953 nguồn giống hệt ở mọi trường, trừ `score_rrf` và `score_rerank`: baseline là `null` ở 953/953 nguồn, nay có giá trị ở 953/953 (`score_rerank` từ 0.0000 đến 0.9999, `score_rrf` từ 0.0137 đến 0.0328). 23 snapshot còn lại trùng từng byte: `/documents`, `/groups`, `/stats`, `/sources`, `/health` (bỏ `timestamp`), `/index/health`, `/jobs`, `/reindex/status`, và `/retrieve` cùng `/search` (4 chế độ) cho 3 câu hỏi. |
| G3 | Đạt. Các vùng CSS, HTML, JS của Library, modal, toast trùng hash baseline, trừ hai vùng chứa chuỗi của D6; so từng dòng, hai vùng này chỉ khác đúng 6 chuỗi đó. DOM `#screen-documents` sau khi tải (64.797 ký tự) giống hệt baseline sau khi đổi "Chat" → "Ask" ở các chuỗi D6 (1 dòng thống kê, 1 tooltip của *Sync index*, 34 tooltip trạng thái). |
| G4 | Đạt. 100 câu trả lời của G2 được dựng lại bằng code mới. (a) Ở 100/100 câu, chữ hiện ra của thân bài giống hệt chữ mà code cũ dựng từ cùng Markdown, sau khi hoàn lại 22 tiêu đề đã thay theo M4. (b) 93 câu có khối nguồn; 393/393 dòng nguồn thành đúng một mục, cùng file, trang và URL với liên kết cũ; không dòng nào bị bỏ. (c) 159/159 số trích `[n]` thành liên kết tới đúng mục nguồn. (d) 7 câu không có khối nguồn hiện nguyên văn. Không câu nào còn chuỗi "Tài liệu đính kèm" trong thân bài; 54 câu có "Also found by the search". Khung đoạn trích: 953/953 đoạn đúng chữ, đúng URL, có "rerank x.xx" khớp `score_rerank`. |
| G5 | Đạt (H.2). |
| G6 | Đạt (H.3). |
| G7 | Đạt. Rộng 1600, 1440, 1439, 1280, 1100, 900, 640, 375 px: không cuộn ngang, không chữ đè. Từ 1440 px trở lên khung đoạn trích là cột dính bên phải; từ 1439 px trở xuống là ngăn kéo; từ 640 px trở xuống ngăn kéo rộng hết màn hình. |
| G8 | Đạt. Quét trang Ask và sidebar khi có một câu trả lời, danh sách nguồn mở hết và ngăn kéo đoạn trích mở (rộng 800 px): 114 phần tử chữ, không phần tử nào dưới 4.5:1 (thấp nhất 4.64:1, dòng tên trường 11 px), trừ 44 dấu "·" phân cách (trang trí, 3.35–3.65:1), như A2 của Library. `aria-live` báo "Searching the regulations…", "Answer ready: quoted from 4 documents.", "No provision matched.". Đi bằng Tab: nút "Ask" và các nút khác có vòng focus 2 px màu navy (T-5). |
| G9 | Đạt (H.4). |

### H.2 Kịch bản chức năng (10.3)

| ID | Kết quả |
|---|---|
| C1 | Masthead đúng chữ ở mục 6; dòng phạm vi "Searches 34 documents in 7 collections · Browse the Library", khớp dòng thống kê của Library ("34 documents in 7 collections · 4,224 indexed passages · all 34 available in Ask"); 4 câu hỏi gợi ý; "The search covers" liệt kê 7 collection theo thứ bậc của Library, tổng 34 văn bản. |
| C2 | Gõ câu hỏi rồi Enter: đúng một `POST /chat` với body `{question}`; phiếu "Enquiry 2 · hh:mm"; ô hỏi được xoá. Câu "Mục đích của quy định MGHP là gì?" nhận "no provision matched", đúng như code cũ (điểm rerank cao nhất 0.0331 < 0.05, Tier 4). |
| C3 | Bấm nút "Ask": gửi đúng câu đang có trong ô; "Answer · quoted from 1 of 7 documents found · 7.3 s". |
| C4 | Bấm câu hỏi gợi ý: "Answer · quoted from 4 documents · 11.2 s"; 4 nguồn mang tên, loại, số hiệu, cơ quan, năm từ catalog, trong đó số hiệu đúng 1342/QĐ-ĐHQG thay cho số trích sai trong dòng nguồn của server; 4 liên kết `[n]`; "Passages consulted (9)". |
| C5–C7 | Shift+Enter xuống dòng và câu hỏi nhiều dòng hiện đúng xuống dòng; ô hỏi cao tối đa 160 px rồi cuộn bên trong; câu rỗng hoặc chỉ có khoảng trắng không được gửi. |
| C8 | Khi chờ: nút "Ask" khoá; Enter không gửi thêm (M16): 3 lần hỏi tạo đúng 3 `POST /chat`, chữ gõ thêm vẫn giữ trong ô; "Searching the regulations… n s" đếm giây; `aria-busy` trên phiếu; `aria-live` báo. Xong thì nút mở lại và focus trở về ô hỏi. |
| C9 | Nhãn đếm đúng c/n; bấm `[n]` cuộn tới mục nguồn và tô sáng 2 s; "Cited in the answer" và "Also found by the search (k)" đúng; URL tên văn bản đúng `docUrl(file, trang)`; DOCX mở `/documents/{tên}/file`. |
| C10 | Tiêu đề là tên file hiện tên văn bản, tên file đặt ngay dưới (22 tiêu đề trong 100 câu); văn bản không có tên thật giữ tiêu đề của server (T-4). |
| C11 | Câu server in đậm trong đoạn trích hiện như tô bút dạ `--lib-hl` (M5). |
| C12 | Ghi chú "(Trích k/n phần)" hiện chữ nhỏ màu xám. |
| C13 | Không tìm thấy: câu của server nguyên văn ("Tôi không tìm thấy thông tin này trong tài liệu hiện có."), dòng hướng dẫn, nút "Passages consulted (10)" vẫn có; `aria-live` báo "No provision matched.". |
| C14–C16 | HTTP 500 (giả lập): "No answer", "The server returned an error (HTTP 500).", phần chi tiết; "Try again" gửi lại đúng câu hỏi vào cùng phiếu. Lỗi mạng: "Could not reach the server.". Hết thời gian: "No answer after 3 minutes.". |
| C17–C18 | Không có catalog hoặc `GET /documents` lỗi: tên văn bản suy từ tên file, số hiệu lấy từ dòng nguồn; dòng phạm vi chỉ còn "Browse the Library", "The search covers" ẩn; không lỗi console. |
| C19 | "Copy answer" chép thân bài và danh sách nguồn có tên văn bản, số hiệu, file, trang (M10); nút đổi thành "Copied" rồi trở lại. |
| C20 | Mở khung đoạn trích từ phiếu 1 rồi phiếu 2: nội dung đổi theo phiếu; đóng bằng ×, Esc, bấm ra ngoài (ngăn kéo); focus trở về nút đã mở; "rerank x.xx"; "Show full passage" / "Show less". |
| C21 | "New enquiry": xoá phiếu, số thứ tự về 1, đóng khung, hiện màn đầu. Bấm khi đang chờ: yêu cầu bị huỷ, nút "Ask" mở ngay, không câu trả lời nào đến muộn. |
| C22–C24 | Chuyển Ask ↔ Library: cuộc hỏi–đáp còn nguyên, Library hoạt động như cũ. Phím `/` ở Ask đưa focus vào ô hỏi (không kích hoạt khi đang gõ); `/` ở Library vẫn vào ô tra cứu của Library. Chấm trạng thái hệ thống ở sidebar không đổi. |
| C25 | Câu trả lời dài nhất (4.894 ký tự) đọc được, không tràn ngang. |
| C26 | Tải lại trang: cuộc hỏi–đáp mất, màn đầu hiện lại, không lỗi console. |
| Mạng | Tải trang: `GET /health`, `GET /documents`, `GET /groups`, `GET /stats`. Mỗi câu hỏi: một `POST /chat`; nếu câu trả lời nhắc tới file chưa có trong danh sách đã nạp thì đọc lại `GET /documents` một lần (7.3). |

### H.3 Unit test và E2E

| Nơi chạy | Kết quả |
|---|---|
| Windows, `python -m unittest discover -s tests -t .` | 86 test đạt (83 cũ, 3 mới trong `tests/test_chat_scores.py`); test E2E tự bỏ qua khi chạy chung. |
| Windows, `ARRS_E2E=1 … tests.test_api_e2e` (Qdrant riêng) | Đạt khi máy rảnh (1 test, 189 s). Lần chạy trước, cùng lúc với kiểm thử trên trình duyệt, báo `KeyError: 'status'` ở bước chờ job xoá tài liệu: đúng lỗi O11 đã ghi ở tài liệu Library (`GET /jobs/{id}` trả 404 trong tích tắc file job đang được thay), nằm ở phần không bị sửa. |
| Container, unit | 86 test đạt (1 bỏ qua như trên). |
| Container, E2E | Đạt (1 test, 130 s). |

### H.4 Docker (G9)

Image build từ cây mã được phát hành: các lớp cài thư viện dùng lại cache, chỉ lớp mã nguồn đổi. Stack Compose riêng, volume mới; volume model được chép từ một stack có sẵn để khỏi tải lại.

- Sẵn sàng sau 30 s: nạp 4.224 vector từ `data/qdrant_seed`; hai model đúng revision đã khoá.
- `/chat` với câu hỏi mẫu của CI: có câu trả lời, 10 nguồn, cả 10 có `score_rrf` và `score_rerank` (cao nhất 0.552). API báo version 2.2.0.
- `/documents`: 34/34 dòng có `catalog` và ở trạng thái `indexed`.
- Trang Ask do container phục vụ: dòng phạm vi "Searches 34 documents in 7 collections"; câu hỏi gợi ý 1 nhận "Answer · quoted from 1 of 5 documents found · 13.3 s"; khung đoạn trích 10 đoạn, "rerank 0.77", "rerank 0.08", … như khi chạy trên Windows.
