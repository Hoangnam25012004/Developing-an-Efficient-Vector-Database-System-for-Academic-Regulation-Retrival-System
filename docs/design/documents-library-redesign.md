# Detail Design — Trang Documents thành "Regulation Library"

| Mục | Nội dung |
|---|---|
| Trạng thái | **Đã triển khai** (2026-09-27, bản 2.1.0). Điều chỉnh khi triển khai: Phụ lục F. Kết quả kiểm thử: Phụ lục G. |
| Ngày | 2026-09-27 |
| Phạm vi code | `ui/index.html` (trang Documents, sidebar dùng chung, modal tải lên, toast); `api/main.py` (chỉ `GET /documents`: thêm khoá `catalog`); mới: `data/catalog.json`, `tests/test_catalog.py`; `README.md` (mục giao diện, mục catalog); phát hành 2.1.0: `docker-compose.yml` (image mặc định), `CITATION.cff` và version của API (F-11) |
| Không đụng tới | `src/**` (parser, ingest, retrieval, chat), `config.yaml`, JSONL/Qdrant/BM25, mọi endpoint khác, nội dung trang Chat (chỉ sidebar dùng chung đổi), `eval/`, `Dockerfile`, CI |
| Bản mẫu | [`documents-library-mockup.html`](documents-library-mockup.html) — mở trực tiếp bằng trình duyệt. Dữ liệu thật của 34 văn bản; tên, số hiệu, năm lấy từ catalog (Phụ lục D). Nút "Show sample states" hiện các trạng thái hiếm (đang xử lý, lỗi, dữ liệu chỉ mục còn sót). Bản mẫu không gọi API. |

---

## 0. Tóm tắt

**Vấn đề.** Trang Documents đang dùng bố cục của một dashboard SaaS mẫu: bốn thẻ KPI có ô icon emoji nền pastel, tab dạng viên thuốc (pill) kèm số đếm và nút ✕, pill màu ở mọi cột (loại, nhóm, trạng thái "✅ Đã index"), nút chính xanh sáng, nhãn trộn Anh–Việt, mã nội bộ lộ ra ngoài (`appendix`, `university_decision`, `announcement`), cột kỹ thuật "Chunks" đứng ngang hàng thông tin văn bản. Người xem chỉ nhận ra văn bản qua **tên file không dấu**: cả 34 văn bản đều chưa có tiêu đề (`title` rỗng 34/34) và 21/34 không có số hiệu.

**Đề xuất.** Biến trang thành một **thư viện văn bản quy chế** ("Regulation Library"), theo quy ước của cơ sở dữ liệu văn bản pháp luật và thư viện đại học:

1. **Đầu trang kiểu thư viện:** dòng tên trường, tiêu đề serif, một câu giới thiệu, một dòng thống kê thay 4 thẻ KPI.
2. **Ô tra cứu lớn**, không phân biệt dấu (`hoc bong` tìm ra `học bổng`), tô sáng phần khớp.
3. **Cột "Refine"** lọc theo Collection (nhóm), Issued by (cơ quan ban hành), Document type, Availability (chỉ hiện khi có văn bản chưa sẵn sàng).
4. **Sổ văn bản** kẻ theo kiểu bảng học thuật (đường kẻ đậm trên và dưới, không thẻ, không bóng đổ). Mặc định **nhóm theo Collection, xếp theo thứ bậc văn bản**: Luật → Thông tư Bộ → Quyết định ĐHQG-HCM → Quy chế Trường → Nội quy/QĐ ngắn → Phụ lục → Thông báo. Mỗi dòng là một **phiếu thư mục**: tên văn bản (serif), dòng mô tả "Loại · Số hiệu · Cơ quan", tên file (monospace) như số hiệu kho.
5. **Trạng thái bằng chữ**, màu trầm ("Available", "Processing · Embedding", "Failed" kèm "Retry indexing").
6. **Thao tác quản trị gom một chỗ:** "Add documents", "Sync index", "Reload" ở đầu trang; menu "⋯" ở từng dòng (Open, Retry, Delete…); "Delete collection…" nằm trong menu "⋯" của collection, không còn nút ✕ trên tab.
7. **Tên văn bản chuẩn** lấy từ file mới `data/catalog.json` (D2). `GET /documents` gắn thêm khoá `catalog`, **không đổi khoá cũ**.
8. **Sidebar dùng chung** bỏ emoji, dùng icon nét, mục "Library", số đếm dạng chữ, vạch vàng đồng cho mục đang chọn. **Modal tải lên** đổi màu, chữ, icon; luồng hai bước giữ nguyên.
9. **Toàn bộ nhãn tiếng Anh** (D3). Dữ liệu (tên văn bản, tên nhóm, cơ quan) giữ nguyên văn tiếng Việt.

**Cam kết không hồi quy.** Không sửa parser, ingest, retrieval, chat, nên JSONL (G1) và câu trả lời Chat (G3) không thể đổi. `GET /documents` chỉ thêm một khoá; mọi endpoint khác giữ nguyên từng byte. Mọi chức năng của trang giữ đúng lời gọi API và điều kiện hỏi xác nhận (Phụ lục A). Mỗi bất biến có cổng kiểm thử (mục 10).

---

## 1. Mục tiêu, phạm vi, quyết định

### 1.1 Mục tiêu

| ID | Mục tiêu |
|---|---|
| MT-1 | Người xem nhận ra văn bản bằng **tên chính thức, số hiệu, cơ quan ban hành, năm**, như khi tra cứu ở thư viện hay cơ sở dữ liệu văn bản pháp luật, thay vì tên file và số chunk. |
| MT-2 | Bỏ các dấu hiệu "dashboard mẫu": emoji, thẻ KPI, pill nhiều màu, gradient, bóng đổ, nhãn trộn ngôn ngữ, mã nội bộ lộ ra ngoài. |
| MT-3 | Giữ **100% chức năng** của trang (Phụ lục A): cùng API, cùng tham số, cùng điều kiện hỏi xác nhận, cùng cơ chế theo dõi job. |
| MT-4 | Không hồi quy chức năng khác (1.5). Trang Chat chỉ đổi phần sidebar dùng chung (D1). |

### 1.2 Quyết định đã chốt (2026-09-27)

| # | Chủ đề | Quyết định | Hệ quả trong design |
|---|---|---|---|
| D1 | Vùng giao diện được đổi | (a) nội dung trang Documents; (b) vị trí nút quản trị; (c) giao diện modal Tải lên; (d) sidebar dùng chung | Chat chỉ thấy sidebar mới; khung chat, câu trả lời, References giữ nguyên. Luồng tải lên, xoá, đồng bộ giữ nguyên. |
| D2 | Tên văn bản | `data/catalog.json`; `GET /documents` trả thêm dữ liệu từ danh mục | Thêm 1 file dữ liệu và 1 khoá trong response. Không đụng JSONL, Qdrant, BM25, Chat. |
| D3 | Ngôn ngữ nhãn | Tiếng Anh | Mọi chữ do UI tạo ra bằng tiếng Anh. Dữ liệu (tên văn bản, tên nhóm, cơ quan, thông điệp lỗi từ server) giữ nguyên. |
| D4 | Nội dung catalog (khi triển khai) | Đối chiếu từng giá trị với ảnh trang 1 của PDF gốc; chỉ ghi giá trị đọc được trên văn bản | Phụ lục D |
| D5 | Phát hành (khi triển khai) | Tag `v2.1.0`; `docker-compose.yml` mặc định image `v2.1.0`; version API `2.1.0` | Mục 11 |

### 1.3 Mặc định do thiết kế chọn (có thể đổi khi duyệt)

| # | Mặc định | Lý do | Phương án khác |
|---|---|---|---|
| M1 | Mục sidebar "Library"; tiêu đề trang "Regulation Library" | Ngắn, đúng tinh thần thư viện | "Documents", "Document Library" |
| M2 | Hộp xác nhận và toast do UI tạo ra **dịch sang tiếng Anh**, giữ nguyên nội dung, điều kiện hiển thị và mức cảnh báo | Theo D3; tránh trang nửa Anh nửa Việt | Giữ nguyên văn tiếng Việt |
| M3 | Thông điệp do **server** trả về (lý do từ chối file, lỗi job, xung đột tên) hiển thị nguyên văn tiếng Việt | Không sửa API ngoài D2 | Thêm bảng ánh xạ mã lỗi sang tiếng Anh ở UI |
| M4 | Mặc định nhóm theo Collection, xếp theo thứ bậc văn bản; trong nhóm: năm giảm dần, rồi tên | Quen thuộc với cơ sở dữ liệu văn bản pháp luật | Giữ thứ tự theo số lượng như hiện tại |
| M5 | Thêm font **Source Serif 4** (có tiếng Việt) cho tiêu đề; chữ giao diện giữ Inter, mã giữ JetBrains Mono | Chỉ thêm 1 họ font | Noto Serif |
| M6 | Giá trị gợi ý "Document type" trong modal **giữ tiếng Việt** (Luật, Thông tư…) và hiển thị kèm nhãn tiếng Anh | Giá trị này được lưu vào dữ liệu và hiện ở References của Chat | Đổi sang tiếng Anh |
| M7 | Mục đang chọn ở sidebar có vạch vàng đồng `#C8A24A` | Dấu hiệu học thuật, khác màu xanh sáng thường gặp ở dashboard | Vạch trắng |
| M8 | Dữ liệu catalog chỉ dùng trên trang Library | Theo D2 | — |
| M9 | Tìm kiếm không phân biệt dấu, khớp **mọi từ** trên mọi trường hiển thị | Người dùng hay gõ không dấu; kết quả luôn bao trùm kết quả của cách tìm cũ (8.3) | Giữ cách tìm cũ |

### 1.4 Ngoài phạm vi

- Nội dung trang Chat: màn chào (🎓), thẻ gợi ý có emoji, bong bóng xanh, avatar gradient, panel References. Chưa được duyệt; nên làm một đợt riêng để hai trang cùng phong cách.
- Sửa dữ liệu trích xuất trong JSONL (số hiệu sai, lỗi OCR). Catalog chỉ sửa **phần hiển thị**.
- Tình trạng hiệu lực của văn bản (còn hay hết hiệu lực, đã bị sửa đổi): cần thẩm định pháp lý, không suy đoán.
- Sửa catalog ngay trên giao diện: chưa có API ghi; catalog được sửa bằng tay trong file.
- Dark mode, bản in.
- Dọn CSS và thư viện của các màn hình đã bỏ (Search, Analytics, Settings): ghi ở Phụ lục C.

### 1.5 Bất biến không hồi quy

| ID | Bất biến | Kiểm chứng |
|---|---|---|
| INV-1 | `data/processed/*.jsonl`, `bm25.pkl` và collection Qdrant không đổi (không sửa code nào ghi chúng) | SHA-256 trước/sau; `git diff --stat src/` rỗng |
| INV-2 | Câu trả lời Chat giống hệt cho 100 câu hỏi GT v2 (answer + tier + sources) | Cổng G3 (mục 10) |
| INV-3 | Mọi endpoint trừ `GET /documents` trả response giống hệt từng byte | Snapshot trước/sau |
| INV-4 | `GET /documents`: bỏ khoá `catalog` đi thì response giống hệt trước | Snapshot và so sánh |
| INV-5 | Mọi chức năng ở Phụ lục A chạy như cũ: cùng method, URL, body; cùng điều kiện hiện hộp xác nhận | Kiểm thử giao diện (10.4) + log network |
| INV-6 | Trang Chat: DOM và hành vi của `#screen-chat` không đổi; chỉ sidebar khác | So sánh DOM `#screen-chat` + 3 câu hỏi mẫu |
| INV-7 | 73 unit test hiện có vẫn qua; E2E `ARRS_E2E=1` vẫn qua | Chạy test |
| INV-8 | Ứng dụng vẫn chạy khi **không có** `data/catalog.json` (volume Docker cũ, bản clone thiếu file) | Test tự động (10.3) |

---

## 2. Hiện trạng

### 2.1 Vì sao trang trông như "dashboard mẫu"

| Thành phần | Hiện tại (`ui/index.html`) | Vì sao trông như template | Hướng xử lý |
|---|---|---|---|
| Thẻ thống kê | 4 thẻ, ô icon emoji 📄 🧩 📂 ✅ nền pastel (dòng 578–595) | Mẫu KPI của mọi dashboard SaaS; số liệu kỹ thuật thành nhân vật chính | Một dòng thống kê dưới tiêu đề (5.2) |
| Tab nhóm | Pill bo tròn, số đếm trong pill, nút ✕ xoá nhóm ngay trên tab (dòng 1187–1193) | Pill + badge là dấu hiệu của UI dựng sẵn; ✕ sát nhãn dễ bấm nhầm và xoá cả nhóm | Danh sách Collection ở cột Refine; xoá nhóm trong menu ⋯ (5.5, 5.7) |
| Cột "Type" | Pill 7 màu theo `typeSlug`; `appendix`, `university_decision`, `announcement` hiện **mã thô** vì thiếu trong `DOC_TYPE_LABELS` | Màu không mang nghĩa; mã nội bộ lộ ra | Chữ thường trong dòng mô tả, nhãn tiếng Anh đầy đủ (6.2) |
| Cột "Group" | Pill xanh ngọc | Lặp lại thông tin của tab | Thành tiêu đề phân đoạn của sổ |
| Trạng thái | "✅ Đã index" trong pill xanh | Emoji + pill | Chấm tròn + chữ màu trầm (4.5) |
| Nút chính | "📥 Update Documents" xanh `#2563EB` | Xanh sáng mặc định của dashboard + emoji | Nút navy IU, icon nét, "Add documents" |
| Ô tìm | 210 px trong topbar, placeholder có 🔍 | Tra cứu là việc chính của thư viện nhưng bị đẩy vào góc | Ô tra cứu lớn dưới tiêu đề (5.4) |
| Tiêu đề trang | "📁 Documents" | Emoji | Masthead "International University · VNU-HCM" / "Regulation Library" |
| Nhãn | "File name, Type, Doc. number…" lẫn "Ngôn ngữ, Trạng thái, Hành động"; "Tất cả" cạnh "Update Documents" | Trộn ngôn ngữ | Toàn bộ tiếng Anh (D3) |
| Nhận diện văn bản | Tên file `26.Luat-BHYT-2008.pdf` là thông tin chính | Không có tên văn bản | Catalog: "Luật Bảo hiểm y tế" (mục 7) |
| Cột "Chunks" | Số in đậm, cạnh "Pages" | Chỉ số kỹ thuật chiếm chỗ nổi bật | Cột "Passages" chữ thường, căn phải, có chú giải |
| Sidebar | Emoji 💬 📁, nhãn nhóm "MAIN/DATA", badge xanh sáng, chấm trạng thái có quầng | Mẫu sidebar dashboard | Icon nét, bỏ nhãn nhóm, số đếm dạng chữ (5.8) |

### 2.2 Dữ liệu hiện có (`GET /documents`, đo 2026-09-27)

Mỗi dòng có các khoá: `source, group, group_label, file_type, size, status, status_detail, language, title, doc_type, doc_number, issuing_body, chunk_count, max_page, uploaded_at, indexed_at, original_filename, warnings, managed`.

| Trường | Tình trạng trên 34 văn bản |
|---|---|
| `title` | Rỗng 34/34. Chỉ văn bản tải lên qua v2 mới có (ô "Tiêu đề hiển thị"). |
| `language` | Rỗng 34/34. UI hiện "VI" do mặc định. |
| `doc_number` | Rỗng 21/34. Sai hoặc nhiễu OCR ở 5 văn bản: `51/2010/QH12` (văn bản 13, thật ra là số của Luật Người khuyết tật được trích dẫn), `4/QĐÐ-ĐHQG` và `1524/QĐÐ-ĐHQG` (ký tự `Ð` do OCR), `586 /QĐ-ĐHQT`, `491/ QĐ-ĐHQT` (thừa khoảng trắng). |
| `doc_type` | 7 mã: `law, circular, university_decision, school_decision_full, school_decision_brief, appendix, announcement`. Ba mã `university_decision`, `appendix`, `announcement` không có nhãn nên hiện nguyên mã. |
| `issuing_body` | Lấy từ nhóm; rỗng ở 2 phụ lục. |
| Năm ban hành | Không có trường riêng. Chỉ suy được từ số hiệu dạng `số/năm/ký hiệu` (8 văn bản, trong đó 1 là số trích sai). |

### 2.3 Kiểm kê chức năng của trang

Toàn bộ chức năng (nút, hàm JS, API, điều kiện xác nhận) được liệt kê ở **Phụ lục A**. Đây là "hợp đồng" mà bản thiết kế lại phải giữ.

---

## 3. Định hướng thiết kế

### 3.1 Ý tưởng: sổ đăng ký văn bản + phiếu thư mục

Trang mượn quy ước của ba loại "thư viện" mà người dùng học thuật đã quen:

- **Cơ sở dữ liệu văn bản pháp luật:** văn bản được nhận diện bằng *số hiệu, ngày/năm ban hành, trích yếu, cơ quan ban hành*; lọc theo *loại văn bản, cơ quan, năm*; sắp theo thứ bậc hiệu lực pháp lý.
- **Thư viện đại học (OPAC):** ô tìm kiếm là trung tâm; cột lọc bên trái có số đếm; mỗi kết quả là một *phiếu thư mục* gồm nhan đề và dòng mô tả.
- **Bảng trong bài báo khoa học** (kiểu *booktabs*): chỉ có đường kẻ ngang, kẻ đậm ở đầu và cuối bảng, không tô màu ô, số căn phải.

### 3.2 Nguyên tắc

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| P1 | Văn bản là nhân vật chính, số liệu là chú thích | Bỏ thẻ KPI; tên văn bản là chữ lớn nhất trong bảng |
| P2 | Chữ tạo thứ bậc, không phải màu | Serif cho tên văn bản; chữ nhỏ cho mô tả; monospace cho tên file |
| P3 | Một màu nhấn (navy IU) | Màu trạng thái chỉ xuất hiện khi cần chú ý |
| P4 | Đường kẻ thay cho thẻ | Không bóng đổ, trừ lớp nổi (menu, modal); bo góc 3–4 px |
| P5 | Từ vựng thư viện | Collection, Issued by, Number, Year, Available, Register |
| P6 | Quản trị có mặt nhưng lùi về sau | Nút quản trị gom một chỗ; thao tác phá huỷ nằm sau menu và hộp xác nhận |
| P7 | Không emoji | Icon nét SVG tự vẽ, 16 px, nét 1.6, theo `currentColor` |

### 3.3 Bỏ gì, thay bằng gì

| Bỏ | Thay bằng |
|---|---|
| 4 thẻ KPI | 1 dòng thống kê |
| Tab pill + ✕ | Danh sách Collection có số đếm; "Delete collection…" trong menu ⋯ |
| Pill loại / nhóm / trạng thái | Chữ; nhóm thành tiêu đề phân đoạn; trạng thái có chấm tròn |
| Emoji ở nút, tiêu đề, trạng thái, modal | Icon nét, hoặc không icon |
| Nút 🗑 và ↻ luôn hiện ở mỗi dòng | Menu ⋯; riêng dòng lỗi có liên kết "Retry indexing" ngay dưới trạng thái |
| Hover nền xanh nhạt `#F8FAFF` | Nền giấy `#FBFAF6` |
| Mũi tên ↕ ở mọi cột | Chỉ cột đang sắp có ▲/▼; `aria-sort` |

---

## 4. Hệ thống thị giác

Token mới đặt tên `--lib-*` trong `:root`, **không** sửa token Chat đang dùng (`--navy`, `--blue`, `--bg`, `--text`…).

### 4.1 Màu

| Token | Giá trị | Dùng cho | Tương phản |
|---|---|---|---|
| `--lib-paper` | `#F7F5F0` | Nền trang, hàng tiêu đề phân đoạn | — |
| `--lib-sheet` | `#FFFFFF` | Nền bảng, ô nhập, menu, modal | — |
| `--lib-hover` | `#FBFAF6` | Dòng đang trỏ chuột | — |
| `--lib-ink` | `#1F2430` | Chữ chính, đường kẻ đậm | 15.5:1 trên trắng |
| `--lib-ink-2` | `#4A5060` | Chữ phụ | 8.1:1 |
| `--lib-ink-3` | `#6A6F78` | Chú thích, số thứ tự, tên file | 5.1:1 trên trắng; 4.6:1 trên nền giấy |
| `--lib-rule` | `#E4DED2` | Đường kẻ giữa các dòng | trang trí |
| `--lib-rule-strong` | `#CFC7B8` | Đường kẻ phân đoạn | trang trí |
| `--lib-rule-ui` | `#8C8579` | Viền ô nhập, nút phụ (WCAG 1.4.11 cần ≥ 3:1) | 3.65:1 |
| `--lib-navy` | `#1B3A6B` | Màu nhấn: nút chính, liên kết, mục lọc đang chọn, sidebar | 11.3:1 |
| `--lib-navy-2` | `#13294D` | Nút chính khi trỏ chuột | — |
| `--lib-gold` | `#C8A24A` | Vạch mục đang chọn ở sidebar | 4.7:1 trên navy |
| `--lib-ok` | `#2F6B3B` | "Available" | 6.4:1 |
| `--lib-busy` | `#8A5A00` | Queued, Processing, Removing, No text found | 5.9:1 |
| `--lib-bad` | `#A12A2A` | Failed, thao tác xoá | 7.3:1 |
| `--lib-hl` | `#FFF1B8` | Tô sáng phần khớp khi tìm | chữ ink 13.7:1 |

### 4.2 Chữ

| Vai trò | Font | Cỡ / độ đậm |
|---|---|---|
| Tiêu đề trang | Source Serif 4 | 34 px / 600 (27 px khi ≤ 640 px) |
| Dòng tên trường (kicker) | Inter | 11 px / 600, in hoa, giãn chữ .14em |
| Tên văn bản trong sổ | Source Serif 4 | 16 px / 600 |
| Tiêu đề phân đoạn, "Refine", tiêu đề modal | Source Serif 4 | 17–21 px / 600 |
| Chữ giao diện, dòng mô tả | Inter | 12.5–15 px / 400–500 |
| Nhãn nhóm lọc | Inter | 11 px / 600, in hoa, giãn .1em |
| Tên file, kích thước file | JetBrains Mono | 11.5 px |
| Số | Inter, `tabular-nums` | căn phải |

Fallback: `'Source Serif 4','Noto Serif','Times New Roman',serif` — cả ba đều đủ dấu tiếng Việt. Font nạp qua link Google Fonts đang có (thêm một `family=`).

### 4.3 Khoảng cách, đường kẻ, bo góc

- Lưới 4 px. Lề trang 40 px (18 px khi ≤ 900 px). Nội dung rộng tối đa 1280 px, căn giữa.
- Bảng: kẻ `2px ink` ở đầu và cuối; `1px ink` dưới hàng tiêu đề cột; `1px rule` giữa các dòng; `1px rule-strong` dưới tiêu đề phân đoạn.
- Masthead ngăn với phần dưới bằng **đường kẻ đôi** (1px ink, khe 4px, 1px rule-strong), gợi trang bìa công báo.
- Bo góc 3 px (nút, ô nhập, menu), 4 px (modal). Bóng đổ chỉ dùng cho menu và modal.

### 4.4 Icon

Bộ icon nét tự vẽ trên lưới 24: search, plus, sync, reload, more, book, chat, open, trash, upload, info, x, check, warn. Nhúng inline SVG; không dùng thư viện icon ngoài; không emoji. Mã SVG có sẵn trong bản mẫu.

### 4.5 Trạng thái văn bản

| `status` | Nhãn | Dấu | Màu | Gợi ý khi trỏ chuột | Thao tác |
|---|---|---|---|---|---|
| `indexed` | Available | ● đặc | ok | "Searchable and citable in Chat." | Open · Delete |
| `queued` | Queued | ○ rỗng | busy | — | Open · Delete |
| `processing` | Processing · {bước} | ◌ xoay | busy | — | Open · Delete |
| `removing` | Removing | ◌ xoay | busy | — | không có menu (như hiện tại không có nút xoá) |
| `failed` | Failed | ● đặc | bad | `status_detail.message` | Open · **Retry indexing** · Delete |
| `empty` | No text found | ● đặc | busy | "No text could be extracted from this document." | Open · Retry indexing · Delete |
| `not_indexed` | Not indexed | ○ rỗng | ink-3 | — | Open · Retry indexing · Delete |
| `orphan` | Index leftovers | ◌ nét đứt | ink-3 | "The file is gone but its passages are still in the index. Use the ⋯ menu to remove them." | **Remove index leftovers…** |

Bước xử lý (`STEP_LABELS`): `parse` → Reading, `embed` → Embedding, `bm25` → Indexing, `cleanup` → Finishing, `qdrant` → Removing vectors, `chunks` → Removing passages, `queued` → Waiting. Điều kiện hiện Retry và Delete **giữ đúng như code hiện tại** (dòng 1256–1262). Với `failed` và `empty`, thông điệp lỗi hiện ngay dưới nhãn, kèm liên kết "Retry indexing".

---

## 5. Bố cục và thành phần

Hình minh hoạ đầy đủ: bản mẫu `documents-library-mockup.html`.

### 5.1 Khung trang (≥ 1280 px)

```
┌──────────────┬──────────────────────────────────────────────────────────────────────┐
│ [logo] ARRS  │ INTERNATIONAL UNIVERSITY · VNU-HCM                                    │
│ IU ACADEMIC  │ Regulation Library                 [+ Add documents] [Sync index] Reload│
│ ASSISTANT    │ Laws, circulars, decisions and university rules that the assistant …  │
│──────────────│ 34 documents in 7 collections · 4,224 indexed passages · all 34 …     │
│  Chat        │ ════════════════════════════════════════════════════════════════════ │
│▌ Library  34 │ [ Search by title, number, issuing body or file name           ( / )] │
│              │ Accents are optional: hoc bong finds học bổng.                         │
│              │                                                                        │
│              │ Refine             Showing 34 of 34 documents   Order [By collection ▾]│
│              │ ─────────────      ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│              │ COLLECTION         No.  Title                 Year Pages Passages Status│
│              │ ▌All          34   ───────────────────────────────────────────────────  │
│              │  Luật quốc gia  9  Luật quốc gia  Quốc hội · 9 documents               │
│              │  Thông tư Bộ…   3    1  Luật Cư trú           2020   23   145 ● Available ⋯│
│              │  …                      Law · No. 68/2020/QH14 · Quốc hội              │
│              │ ISSUED BY               34.Luat-Cu-tru-2020.pdf  PDF                   │
│              │ DOCUMENT TYPE        …                                                  │
│ ● System     │ (AVAILABILITY)     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│   online     │                    Titles, numbers and years come from the catalogue… │
└──────────────┴──────────────────────────────────────────────────────────────────────┘
```

- Toàn bộ vùng nội dung cuộn chung một khối (`.lib-scroll`). Hàng tiêu đề cột dính khi cuộn (`position: sticky`); cột Refine dính bên trái khi ≥ 1280 px.
- Khác hiện tại: bỏ `.topbar` của trang Documents (masthead thay thế); danh sách không còn cuộn trong một khung riêng mà cuộn cùng trang.

### 5.2 Masthead

- Kicker: "International University · VNU-HCM".
- Tiêu đề: "Regulation Library".
- Câu giới thiệu: "Laws, circulars, decisions and university rules that the assistant searches and cites when it answers questions."
- Dòng thống kê thay 4 thẻ KPI, **giữ các id** `stat-docs`, `stat-groups`, `stat-chunks`, `stat-indexed`:
  `34 documents in 7 collections · 4,224 indexed passages · all 34 available in Chat`
  - Khi có văn bản chưa sẵn sàng: `… · 32 of 34 available in Chat · 1 in progress · 1 need attention`. Hai cụm cuối là liên kết; bấm vào sẽ bật bộ lọc Availability tương ứng.
  - Số định dạng `en-US` (`4,224`) theo D3; hiện tại là `vi-VN` (`4.224`).
  - Nguồn số liệu **không đổi**: `summary` của `GET /documents`, `total_chunks` của `GET /stats`, số nhóm tính như dòng 1175–1176.

### 5.3 Thanh công cụ quản trị

| Nút | id (giữ) | Nhãn mới | Kiểu | Hành vi (giữ nguyên) |
|---|---|---|---|---|
| Tải lên | `reindex-docs` | ＋ Add documents | chính, navy | Mở modal. Khi job index chạy, nhãn thành "◌ Embedding 2/3…" (`trackJob`) |
| Đồng bộ | `sync-index` | ⟳ Sync index | phụ, có viền | Hỏi xác nhận → `POST /reindex {"mode":"sync"}` → poll `/reindex/status` mỗi 2.5 s; nhãn "◌ Reading 12/34…" |
| Tải lại | `refresh-docs` | ↻ Reload | chữ | `loadDocuments()` + toast "Reloading the list…" |

`UPLOAD_BTN_LABEL` và `SYNC_BTN_LABEL` (dòng 1129–1130) đổi thành chuỗi HTML có icon nét. Mọi chỗ đang gán `btn.innerHTML = …LABEL` vẫn chạy đúng.

### 5.4 Ô tra cứu

- `id="doc-search"` giữ nguyên. Cao 46 px, rộng hết cột nội dung, có icon kính lúp nét và gợi ý phím tắt `/`.
- Placeholder: "Search by title, number, issuing body or file name". Dòng gợi ý: "Accents are optional: *hoc bong* finds *học bổng*."
- Phím `/` đưa con trỏ vào ô tìm (khi không đang gõ trong ô khác).
- Cách khớp (M9): chuẩn hoá NFD, bỏ dấu, `đ → d`, chữ thường; tách từ theo khoảng trắng. Văn bản khớp khi **mọi từ** nằm trong chuỗi gộp của: tên hiển thị, `source`, số hiệu hiển thị, `doc_number` gốc, cơ quan, tên collection, loại (tiếng Anh), năm, `original_filename`.
- Tô sáng: các từ từ 2 ký tự trở lên được bọc `<mark>` trong tên, dòng mô tả và tên file. Vị trí được ánh xạ từ chuỗi đã bỏ dấu về chuỗi gốc theo từng ký tự, nên "hoc" tô đúng chữ "học".

### 5.5 Cột "Refine"

| Nhóm lọc | Giá trị | Thứ tự | Ghi chú |
|---|---|---|---|
| Collection | `group` (khoá "Khác" hiện là "Other") | Thứ bậc (7.4), rồi tên | Mỗi mục có menu ⋯ → "Delete collection…"; "Other" không có menu (như hiện tại không xoá được) |
| Issued by | Cơ quan hiển thị (7.3); rỗng → "Not specified" | Thứ bậc nhỏ nhất của các văn bản thuộc cơ quan đó | |
| Document type | Loại tiếng Anh (6.2) | Như trên | |
| Availability | `status` | `indexed, queued, processing, removing, failed, empty, not_indexed, orphan` | **Chỉ hiện khi có ít nhất 2 trạng thái khác nhau** (bình thường cả 34 văn bản đều Available) |

- Mỗi nhóm chọn một giá trị (mặc định "All"). Các nhóm kết hợp theo AND, và AND với ô tìm.
- Số đếm trong một nhóm tính trên tập đã lọc bởi **các nhóm khác** và ô tìm (cách làm thông dụng của thư viện số). Giá trị có số đếm 0 bị làm mờ, không bấm được.
- "Clear all filters" hiện khi có bộ lọc. Bộ lọc đang bật cũng hiện ở dòng kết quả, ví dụ "Collection: **Luật quốc gia** remove".
- Biến `currentGroup` hiện có trở thành bộ lọc Collection. Nếu collection đang chọn biến mất sau khi tải lại thì về "All" (giữ logic dòng 1186).
- Dưới 1280 px, cột Refine chuyển lên trên sổ thành khối gập được "Refine +/−", mặc định đóng; tiêu đề ghi "2 filters on" khi có lọc. Từ 1280 px trở lên, khối luôn mở.

### 5.6 Sổ văn bản

**Cột**

| Cột | Nội dung | Căn | Rộng (px) | Sắp xếp |
|---|---|---|---|---|
| No. | Số thứ tự trong danh sách đang xem | phải | 40 | — |
| Title | Phiếu thư mục (xem dưới) | trái | phần còn lại | có |
| Year | Năm hiển thị (7.3) hoặc "—" | trái | 56 | có (mặc định giảm dần) |
| Pages | `max_page` | phải | 72 | có (giảm dần) |
| Passages | `chunk_count`; chú giải "Indexed text passages the search engine can return" | phải | 72 | có (giảm dần) |
| Status | Mục 4.5 | trái | 136 | có |
| (⋯) | Nút menu | phải | 40 | — |

**Phiếu thư mục (ô Title)**

```
Luật Giáo dục                           ← Source Serif 16/600; liên kết mở văn bản
Law · No. 43/2019/QH14 · Quốc hội       ← Inter 12.5, ink-2
32.Luat-Giao-duc-2019.pdf   PDF         ← JetBrains Mono 11.5, ink-3 (thêm " · EN" nếu tiếng Anh)
```

- Bấm vào tên, hoặc bất kỳ chỗ nào trên dòng, mở văn bản bằng `docUrl(d.source)` ở tab mới (giữ hành vi dòng 1254). Dòng `orphan` không mở được (như hiện tại).
- Nếu `original_filename` khác `source` (tên đã được chuẩn hoá lúc tải lên), tên gốc hiện trong tooltip của tên file.

**Nhóm và thứ tự**

- Mặc định ("By collection (hierarchy)"): mỗi collection là một phân đoạn có hàng tiêu đề `Luật quốc gia · Quốc hội · 9 documents` (nền giấy, serif). Phân đoạn xếp theo 7.4; trong phân đoạn: năm giảm dần, rồi tên (`localeCompare(…, 'vi')`). Khi bộ lọc chỉ còn một collection thì bỏ hàng tiêu đề.
- Ô "Order": By collection (hierarchy), Title, Year, Number, Issued by, Document type, Pages, Passages, Status, File name, kèm nút đảo chiều. Bấm tiêu đề cột Title/Year/Pages/Passages/Status cũng sắp; bấm lần hai đảo chiều (giữ hành vi hiện tại). Khi sắp theo tiêu chí khác "collection", sổ hiện phẳng, không phân đoạn.
- Mọi khoá sắp xếp hiện có (`source, doc_type, doc_number, issuing_body, doc_group, status, chunk_count, max_page`) vẫn dùng được qua ô Order. Riêng `language` không có cột riêng vì cả 34 văn bản đều "vi"; văn bản tiếng Anh có nhãn " · EN" và tìm được bằng ô tìm.

**Trạng thái rỗng, đang tải, lỗi**

| Tình huống | Hiển thị |
|---|---|
| Đang tải lần đầu | Một dòng "Loading the register…" kèm vòng xoay nhỏ |
| Không có kết quả | "No documents match “xyz”." + liên kết "Clear the search and filters" |
| Kho rỗng | "The library is empty." + liên kết "Add documents" (mở modal) |
| Lỗi tải | "Could not load the register (HTTP error)." + liên kết "Try again" (gọi `loadDocuments()`) |

Chú thích nguồn dưới sổ: "Titles, numbers and years come from the library catalogue (`data/catalog.json`) and from the details given at upload; a document with neither is listed under a name derived from its file name." Câu này không nêu thứ tự ưu tiên, vì thứ tự khác nhau theo từng trường (7.3).

### 5.7 Menu dòng và menu collection

| Mục | Điều kiện | Gọi |
|---|---|---|
| Open the PDF / Download the original (.docx) | `status ≠ orphan` | `window.open(docUrl(d.source), '_blank')` |
| Retry indexing | `status ∈ {failed, empty, not_indexed}` | `window.retryDoc(name)` |
| Delete document… (đỏ) | `status ∉ {orphan, removing}` | `window.deleteDoc(name)` |
| Remove index leftovers… (đỏ) | `status = orphan` | `window.deleteDoc(name)` |
| Delete collection… (đỏ, menu của collection) | collection ≠ "Other" | `window.deleteGroup(id, label)` |

- Dòng `removing` không có menu (như hiện tại không có nút xoá).
- Menu dùng `role="menu"`/`menuitem`; mở bằng click, Enter hoặc Space; di chuyển bằng ↑ ↓; đóng bằng Esc (trả focus về nút), click ra ngoài, Tab, hoặc cuộn trang.
- `deleteDoc`, `deleteGroup`, `retryDoc`, `trackJob` giữ nguyên logic; chỉ chữ trong `confirm()` và toast đổi (M2).

### 5.8 Sidebar dùng chung (D1-d)

| Phần | Hiện tại | Mới |
|---|---|---|
| Nền | Navy `#1B3A6B` + bóng đổ | Navy phẳng, không bóng |
| Logo | Ô trắng bo 10 px | Ô trắng bo 3 px; ảnh lỗi thì hiện chữ "IU" serif |
| Wordmark | "ARRS" Inter 17/700; "IU Academic Assistant" | "ARRS" Source Serif 21/600; dòng phụ giữ nguyên chữ |
| Nhãn nhóm | "MAIN", "DATA" | Bỏ (chỉ có 2 mục) |
| Mục menu | Emoji 💬 📁; nền trắng mờ; vạch xanh trời khi chọn | Icon nét chat/book; vạch trái vàng đồng 2 px khi chọn |
| Nhãn | Chat, Documents | Chat, **Library** (M1) |
| Số đếm | Badge xanh sáng | Số thường, trắng 62% |
| Trạng thái | Chấm xanh có quầng + "System online" | Chấm không quầng; chữ giữ nguyên ("System online", "Qdrant offline", "API offline") |
| Thu gọn | ≤ 900 px chỉ còn icon | Giữ nguyên hành vi |

`data-screen="chat|documents"`, `showScreen()`, `#sb-doc-count`, `#sb-status-dot`, `#sb-status-text` giữ nguyên, nên điều hướng và `checkHealth()` không đổi.

### 5.9 Modal "Add documents" (D1-c)

Luồng, id, kiểm tra phía client và lời gọi API giữ nguyên (Phụ lục A, A.4). Chỉ đổi:

| Phần | Mới |
|---|---|
| Tiêu đề | "Add documents to the library" (serif), viền trên navy 3 px |
| Bước | "1 Select files · 2 Check · 3 Index" dạng chữ; bước hiện tại in đậm |
| Vùng thả | Viền nét đứt 1 px, nền giấy, icon upload nét; "Drag PDF or DOCX files here" / "Files from this computer only · up to 20 files, 50 MB each" |
| Chọn nhóm | "Collection for these files"; mục cuối "New collection…" |
| Nhóm mới | "Collection name *", "Document type", "Issuing body / author", "Default language" (Vietnamese / English; giá trị `vi`/`en` giữ nguyên) |
| Gợi ý | Đoạn chữ thường có icon (i); bỏ khung nền xanh ngọc |
| Bước 2 | Mỗi file có vạch trái theo kết quả (xanh / nâu / đỏ) thay nền đỏ/vàng; ✓ ⚠ ✗ thành icon nét; "Edit record details (title · number · type · issuing body)" |
| Nút | "Cancel", "Back", "Check files" → "Upload & index N files" |

Thông điệp từ server (lý do từ chối, xung đột tên) hiện nguyên văn (M3). Cảnh báo OCR do client tạo (`WARNING_TEXT`) được dịch sang tiếng Anh.

### 5.10 Toast

Nền mực `#1F2430`, chữ trắng, vạch trái 3 px theo loại (xanh lá / đỏ / xanh xám), bo 3 px, hiện mờ dần 150 ms thay vì trượt ngang. Hàm `toast()` giữ nguyên chữ ký. Đã kiểm tra: chỉ trang Documents và modal gọi `toast()`.

### 5.11 Responsive

| Độ rộng | Thay đổi |
|---|---|
| ≥ 1280 px | Refine là cột trái dính, rộng 220 px |
| < 1280 px | Refine lên trên sổ, gập được, mặc định đóng |
| ≤ 900 px | Sidebar chỉ còn icon (như hiện tại); ẩn cột Passages; lề 18 px |
| ≤ 640 px | Ẩn hàng tiêu đề cột và các cột số; mỗi dòng thành một phiếu: tên, mô tả, tên file, rồi "2019 · 45 pp · Available"; sắp xếp qua ô Order |

### 5.12 Truy cập (accessibility)

- Tương phản chữ ≥ 4.5:1; viền điều khiển ≥ 3:1 (4.1).
- Nút lọc dùng `aria-pressed`; tiêu đề cột là `<button>` và có `aria-sort`; hàng phân đoạn dùng `<th scope="rowgroup">`; dòng đếm kết quả có `aria-live="polite"`.
- Mọi nút chỉ có icon đều có `aria-label` (ví dụ "Actions for Luật Giáo dục").
- Vòng focus 2 px navy. Không chức năng nào chỉ hiện khi trỏ chuột: nút ⋯ của collection hiện khi trỏ chuột **hoặc** khi có focus, và luôn hiện trên màn hình cảm ứng.
- `prefers-reduced-motion`: tắt vòng xoay.
- Modal có `role="dialog"`, `aria-modal`, `aria-labelledby` (bản cũ chưa có).

---

## 6. Nội dung chữ (microcopy)

### 6.1 Nhãn, trạng thái, thông báo

**Nhãn cố định**

| Vị trí | Hiện tại | Mới |
|---|---|---|
| `<title>` trang | ARRS — IU Academic Regulation Assistant | Giữ nguyên |
| Sidebar | Main · Data · Documents | (bỏ) · (bỏ) · Library |
| Tiêu đề trang | 📁 Documents | Regulation Library |
| Thẻ KPI | Total documents · Chunks · Document groups · Indexed | "{n} documents in {g} collections · {c} indexed passages · all {n} available in Chat" |
| Tab đầu | Tất cả | All |
| Ô tìm | 🔍 Search file name… | Search by title, number, issuing body or file name |
| Cột | File name · Type · Doc. number · Issuing body · Group · Ngôn ngữ · Trạng thái · Chunks · Pages · Hành động | No. · Title · Year · Pages · Passages · Status · (⋯) |
| Nút | 📥 Update Documents · ⟳ Đồng bộ chỉ mục · ↻ Refresh | Add documents · Sync index · Reload |
| Nút khi đang chạy | đang chờ index… · {Bước} 2/3… · đang đồng bộ… | Waiting to index… · {Step} 2/3… · Syncing… |
| Trạng thái | ✅ Đã index · ⏳ Đang chờ · ⚙ Đang xử lý · 🗑 Đang xoá · ❌ Lỗi · ⚠ Rỗng · ○ Chưa index · ◌ Còn trong chỉ mục | Available · Queued · Processing · Removing · Failed · No text found · Not indexed · Index leftovers |
| Gợi ý orphan | File không còn nhưng dữ liệu vẫn nằm trong chỉ mục — bấm 🗑 để dọn. | The file is gone but its passages are still in the index. Use the ⋯ menu to remove them. |
| Gợi ý empty | Không trích được nội dung từ tài liệu. | No text could be extracted from this document. |
| Đang tải | Loading documents… | Loading the register… |
| Không kết quả | No matching documents | No documents match “{q}”. |
| Lỗi tải | Error: {message} | Could not load the register ({message}). Try again |

**Hộp xác nhận (M2)** — điều kiện hiện giữ nguyên; chỉ đổi chữ.

| Hộp | Hiện tại | Mới |
|---|---|---|
| Xoá tài liệu | Xóa tài liệu "{name}"? / File và toàn bộ dữ liệu chỉ mục của tài liệu này sẽ bị xoá. | Delete “{name}”? / The file and all of its index data will be removed. |
| Dọn orphan | Dọn dữ liệu chỉ mục còn sót của "{name}"? | Remove the leftover index data of “{name}”? |
| Xoá nhóm | Xóa nhóm "{label}" và TẤT CẢ tài liệu bên trong? / File và dữ liệu chỉ mục sẽ bị xoá. Hành động này không thể hoàn tác. | Delete the collection “{label}” and ALL documents in it? / The files and their index data will be removed. This cannot be undone. |
| Đồng bộ | Đồng bộ lại toàn bộ chỉ mục? / Hệ thống parse lại mọi tài liệu và cập nhật chỉ mục tại chỗ — Chat vẫn hoạt động trong lúc chạy. / Lần đầu có thể mất 10–15 phút vì phải tạo lại vector cho toàn bộ tài liệu. | Sync the whole index? / Every document is read again and the index is updated in place — Chat keeps working meanwhile. / The first run can take 10–15 minutes because every document is embedded again. |

**Toast**

| Hiện tại | Mới |
|---|---|
| Refreshing… | Reloading the list… |
| Đã index xong {ok} tài liệu — có thể hỏi ngay trong Chat. | Indexed {ok} documents — you can ask about them in Chat now. |
| Đã cập nhật chỉ mục sau khi xoá. | The index is up to date after the deletion. |
| Index xong {ok}/{n} tài liệu. Lỗi: {msg} | Indexed {ok} of {n} documents. Error: {msg} |
| Đang dọn "{name}" khỏi chỉ mục… | Removing “{name}” from the index… |
| Đã xóa "{name}" — đang cập nhật chỉ mục… | Deleted “{name}” — updating the index… |
| Xóa thất bại: {e} | Could not delete: {e} |
| Đã xóa nhóm "{label}" ({n} file) — đang cập nhật chỉ mục… | Deleted the collection “{label}” ({n} files) — updating the index… |
| Xóa nhóm thất bại: {e} | Could not delete the collection: {e} |
| Đã xếp "{name}" vào hàng đợi index. | “{name}” is queued for indexing. |
| Không thể index lại: {e} | Could not queue it for indexing: {e} |
| Đang đồng bộ chỉ mục ở chế độ nền. Chat vẫn dùng được. | Syncing the index in the background. Chat keeps working. |
| Đồng bộ chỉ mục hoàn tất. | Index sync complete. |
| Đồng bộ thất bại: {e} | Index sync failed: {e} |
| Không thể đồng bộ: {e} | Could not start the sync: {e} |
| Status check failed: {e} | Could not read the sync status: {e} |
| Mỗi lần tối đa {max} file — {k} file bị bỏ qua | At most {max} files at a time — {k} skipped |
| Bỏ qua {k} file không phải PDF/DOCX | Skipped {k} files that are not PDF or DOCX |
| Bỏ qua {k} file lớn hơn {mb} MB | Skipped {k} files larger than {mb} MB |
| Vui lòng nhập tên nhóm mới | Enter a name for the new collection |
| Vui lòng chọn nhóm cho tài liệu | Choose a collection for these files |
| Đã lưu {n} file vào nhóm "{g}" — đang index… | Saved {n} files in “{g}” — indexing… |
| Tải lên thất bại: {e} | Upload failed: {e} |

**Modal tải lên**

| Hiện tại | Mới |
|---|---|
| Tải tài liệu lên hệ thống | Add documents to the library |
| ① Chọn file › ② Kiểm tra › ③ Xử lý | 1 Select files · 2 Check · 3 Index |
| Kéo thả file PDF/DOCX vào đây | Drag PDF or DOCX files here |
| Chỉ chấp nhận file từ máy tính của bạn · tối đa {n} file, mỗi file ≤ {mb} MB | Files from this computer only · up to {n} files, {mb} MB each |
| Nhóm (group) cho các file này | Collection for these files |
| ➕ Tạo nhóm mới… | New collection… |
| Tên nhóm mới * · Loại tài liệu · Đơn vị ban hành / tác giả · Ngôn ngữ mặc định | Collection name * · Document type · Issuing body / author · Default language |
| Tiếng Việt / English | Vietnamese / English |
| Đoạn gợi ý (chọn nhóm có sẵn hoặc tạo nhóm mới…) | The collection sets the document type and issuing body used for these files — nothing is guessed from their content. **Check files** reads every file and reports problems before anything is stored. |
| File đã chọn | Selected files |
| Bỏ file này | Remove this file |
| Đang tải {n} file lên để kiểm tra… | Uploading {n} files for checking… |
| Đang đọc và kiểm tra nội dung từng file… | Reading and checking each file… |
| Đã kiểm tra {n} file: {ok} hợp lệ, {bad} bị từ chối. Xem lại rồi bấm xác nhận. | Checked {n} files: {ok} accepted, {bad} rejected. Review them, then confirm. |
| Kiểm tra thất bại: {e} | The check failed: {e} |
| Tên được chuẩn hoá: {a} → {b} | File name normalised: {a} → {b} |
| Thay thế bản cũ · Đổi tên (thêm -2) · Bỏ qua | Replace the old version · Rename (add “-2”) · Skip |
| Gợi ý ngôn ngữ: tiếng Anh (có thể đổi). | Suggested language: English (you can change it). |
| `WARNING_TEXT.needs_ocr` | {p} scanned pages need OCR (about {s} seconds). |
| `WARNING_TEXT.ocr_unavailable` | Some pages are scanned but OCR (Tesseract) is not available — those pages may come out empty. |
| `WARNING_TEXT.broken_text_layer` | The text layer looks broken (no Vietnamese accents) — those pages will be read again with OCR. |
| ▸ Tuỳ chỉnh thông tin (tiêu đề · số hiệu · loại · đơn vị) | Edit record details (title · number · type · issuing body) |
| Tiêu đề hiển thị · Số hiệu (để trống = tự trích) · Loại (để trống = theo nhóm) · Đơn vị ban hành (để trống = theo nhóm) | Display title · Number (blank = extract automatically) · Type (blank = from the collection) · Issuing body (blank = from the collection) |
| Hủy · ← Quay lại · Kiểm tra · Đang kiểm tra… · Đang lưu… | Cancel · Back · Check files · Checking… · Saving… |
| Tải lên & index {n} file · Không có file để tải lên | Upload & index {n} files · No files to upload |
| Không kết nối được máy chủ | Could not reach the server |

Giá trị trong `datalist` gợi ý loại tài liệu **giữ tiếng Việt** (M6) và có thêm thuộc tính `label` tiếng Anh, ví dụ `<option value="Luật" label="Law">`.

### 6.2 Nhãn loại văn bản (chỉ trang Library)

| `doc_type` | Nhãn |
|---|---|
| `law` | Law |
| `decree` | Decree |
| `circular` | Circular |
| `university_decision`, `school_decision`, `school_decision_full` | Decision |
| `school_decision_brief` | Decision (short form) |
| `regulation` | Regulation |
| `guideline` | Guideline |
| `notice`, `announcement` | Notice |
| `dispatch` | Official letter |
| `appendix` | Appendix |
| Chuỗi tự do tiếng Việt (nhập khi tải lên) | Theo từ khoá: luật → Law, nghị định → Decree, thông tư → Circular, quyết định → Decision, quy chế / quy định → Regulation, hướng dẫn → Guideline, thông báo → Notice, công văn → Official letter, phụ lục → Appendix, chính sách → Policy, hợp đồng → Contract, báo cáo → Report, tài liệu kỹ thuật → Technical document, khác → Other. Không khớp thì giữ nguyên chuỗi. |

Bảng này là hằng mới `LIB_TYPE_LABELS`. `DOC_TYPE_LABELS` và `typeLabel()` (dùng cho References trong Chat) **không sửa**, nên nhãn loại ở Chat vẫn tiếng Việt và vẫn hiện mã thô cho 3 loại thiếu nhãn (ghi ở Phụ lục C, O1).

---

## 7. Dữ liệu: `data/catalog.json` và `GET /documents` (D2)

### 7.1 Định dạng

```json
{
  "version": 1,
  "documents": {
    "26.Luat-BHYT-2008.pdf": {
      "title": "Luật Bảo hiểm y tế",
      "doc_number": "25/2008/QH12",
      "year": 2008,
      "size": 272731,
      "note": "Số hiệu và năm đọc từ trang 1."
    },
    "13.-Quy-dinh-chinh-sach-ho-tro-nguoi-hoc-khuyet-tat.-Signed-4.pdf": {
      "title": "Quy định chính sách hỗ trợ người học khuyết tật tại Trường Đại học Quốc tế",
      "doc_number": "",
      "note": "Ẩn số hiệu trích sai 51/2010/QH12 (số của Luật Người khuyết tật)."
    }
  }
}
```

| Khoá | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `title` | chuỗi, ≤ 300 ký tự | không | Tên chính thức (trích yếu), tiếng Việt có dấu |
| `title_en` | chuỗi, ≤ 300 | không | Tên tiếng Anh nếu có; hiện trong tooltip của tên |
| `doc_number` | chuỗi, ≤ 80 | không | Số hiệu hiển thị. **Có khoá là ghi đè, kể cả chuỗi rỗng** (dùng để ẩn số trích sai) |
| `year` | số nguyên 1900–2100 | không | Năm ban hành |
| `issued` | `YYYY-MM-DD` | không | Ngày ban hành; hiện trong tooltip của cột Year |
| `issuing_body` | chuỗi, ≤ 120 | không | Cơ quan hiển thị. Có khoá là ghi đè |
| `size` | số nguyên | không | Kích thước (byte) của file mà mục này mô tả. Có và **khác** kích thước file hiện tại thì bỏ qua cả mục (file đã bị thay bằng bản khác cùng tên) |
| `note` | chuỗi | không | Ghi chú của người biên mục; **không** gửi ra API |

Khoá mục là `source` (tên file). Khoá ngoài bảng bị bỏ qua. Chuỗi được cắt khoảng trắng hai đầu. Giá trị sai kiểu hoặc ngoài giới hạn chỉ làm bỏ khoá đó, không bỏ cả mục.

### 7.2 Hợp nhất ở API (`api/main.py`)

- Hàm mới `_catalog_path()` trả `Path(cfg["data"]["raw_dir"]) / "catalog.json"`, cùng thư mục với `groups.json`. Không thêm khoá vào `config.yaml`.
- Hàm mới `_load_catalog()` trả dict, cache theo `(mtime_ns, size)` của file, để lần poll 3 giây không đọc lại file.
  - Không có file → `{}`, không log.
  - JSON hỏng hoặc sai cấu trúc → `{}`, log một dòng `[catalog] ignored: …` cho mỗi lần file đổi; `/documents` vẫn trả 200 (INV-8).
- Trong `list_documents()`, ngay sau vòng lặp gán `group_label`: mỗi dòng nhận `r["catalog"]` = các khoá hiển thị đã kiểm tra (`title, title_en, doc_number, year, issued, issuing_body`), hoặc `None` khi không có mục, mục rỗng, hay `size` lệch.
- **Không** đổi khoá nào đang có (`title`, `doc_number`, `issuing_body`…) và không đổi `summary`. `build_registry()` và mọi endpoint khác giữ nguyên.
- Log theo kiểu `print(f"[…] …")` mà `api/main.py` đang dùng.

Phác thảo (khoảng 40 dòng):

```python
_catalog_cache: dict = {"key": None, "data": {}}
_CATALOG_STR = {"title": 300, "title_en": 300, "doc_number": 80, "issuing_body": 120}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _catalog_path() -> Path:
    return Path(_load_cfg()["data"]["raw_dir"]) / "catalog.json"


def _load_catalog() -> dict:
    """data/catalog.json → {source: entry}. Missing or broken file → {}."""
    p = _catalog_path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    if _catalog_cache["key"] != key:
        data: dict = {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            docs = raw.get("documents") if isinstance(raw, dict) else None
            if not isinstance(docs, dict):
                raise ValueError("no 'documents' object")
            data = {k: v for k, v in docs.items() if isinstance(v, dict)}
        except (OSError, ValueError) as exc:
            print(f"[catalog] ignored: {exc}")
        _catalog_cache.update(key=key, data=data)
    return _catalog_cache["data"]


def _catalog_entry(entry: Optional[dict], size: int) -> Optional[dict]:
    """The display fields of one entry; None if absent or written for another file."""
    if not entry:
        return None
    if type(entry.get("size")) is int and entry["size"] != size:
        return None
    out: dict = {}
    for k, limit in _CATALOG_STR.items():
        v = entry.get(k)
        if isinstance(v, str) and len(v.strip()) <= limit:
            out[k] = v.strip()
    year = entry.get("year")
    if type(year) is int and 1900 <= year <= 2100:
        out["year"] = year
    issued = entry.get("issued")
    if isinstance(issued, str) and _ISO_DATE.match(issued.strip()):
        out["issued"] = issued.strip()
    return out or None

# in list_documents(), after group_label:
#     cat = _load_catalog()
#     for r in rows:
#         r["catalog"] = _catalog_entry(cat.get(r["source"]), r.get("size", 0))
```

Dòng `orphan` (file đã mất) có `size = 0`; mục có khoá `size` sẽ bị bỏ qua ở dòng này, mục không có `size` vẫn áp dụng — cả hai đều chấp nhận được vì dòng orphan chỉ để dọn.

### 7.3 Quy tắc hiển thị (UI)

| Trường hiển thị | Thứ tự ưu tiên |
|---|---|
| Tên (Title) | `title` (nhập lúc tải lên, lưu trong sidecar) → `catalog.title` → tên suy từ file (bỏ số thứ tự đầu và đuôi file; `-`, `_` thành khoảng trắng) |
| Số hiệu | `catalog.doc_number` nếu **có khoá** → `doc_number` |
| Cơ quan | `catalog.issuing_body` nếu có khoá → `issuing_body` |
| Năm | `catalog.year` → năm trong số hiệu dạng `…/YYYY/…` → "—" |
| Loại | `LIB_TYPE_LABELS` (6.2) theo `doc_type` |

Tên do người tải lên nhập đứng trước catalog, vì catalog chỉ mô tả kho ban đầu.

### 7.4 Thứ bậc collection

| Hạng | `doc_type` của collection trong `groups.json` | Collection hiện có |
|---|---|---|
| 1 | `law` | Luật quốc gia |
| 2 | `decree` | — |
| 3 | `circular` | Thông tư Bộ GD&ĐT |
| 4 | `university_decision` | Quyết định ĐHQG-HCM |
| 5 | `school_decision_full`, `school_decision`, `regulation` | Quy chế trường ĐHQT |
| 6 | `school_decision_brief` | Nội quy / quyết định ngắn ĐHQT |
| 7 | `guideline`, `dispatch` | — |
| 8 | `appendix` | Phụ lục |
| 9 | `notice`, `announcement` | Thông báo |
| 10 | Còn lại (collection tạo khi tải lên, loại nhập tự do): thử ánh xạ từ khoá ở 6.2 trước; không được thì hạng 10, xếp theo tên | — |

Thứ bậc chỉ dùng để **sắp xếp hiển thị**, không ghi vào đâu.

### 7.5 Nội dung catalog

34 mục ở **Phụ lục D**. Bản nháp đầu đọc từ JSONL và tên file; khi triển khai, mọi giá trị được đối chiếu trên ảnh trang 1 của bản PDF gốc (D4), giá trị không đọc được trên văn bản thì để trống. `size` được điền tự động từ file trên đĩa.

### 7.6 Lưu ý Docker

`data/` nằm trong image, và được chép vào volume `arrs_data` **chỉ ở lần khởi động đầu** (khi volume còn rỗng). Stack đang chạy sẽ không tự có `catalog.json` sau khi nâng image; cần chép tay:

```bash
docker compose cp data/catalog.json app:/app/data/catalog.json
```

Nếu không chép, trang vẫn chạy và hiện tên suy từ file (INV-8). README ghi thêm điều này.

---

## 8. Thiết kế kỹ thuật

### 8.1 Hợp đồng DOM

| id | Giữ? | Ghi chú |
|---|---|---|
| `screen-documents`, `data-screen="documents"` | Giữ | Điều hướng `showScreen()` không đổi |
| `reindex-docs`, `sync-index`, `refresh-docs` | Giữ | Nút quản trị; handler giữ nguyên |
| `doc-search` | Giữ | Handler đổi cách lọc (M9) |
| `doc-table`, `doc-tbody` | Giữ | Nội dung render mới |
| `stat-docs`, `stat-chunks`, `stat-groups`, `stat-indexed` | Giữ | Thành `<b>`/`<span>` trong dòng thống kê |
| `sb-doc-count`, `sb-status-dot`, `sb-status-text` | Giữ | Sidebar |
| `doc-tabs` | Bỏ | Thay bằng `facet-collection`; mọi tham chiếu JS được sửa cùng lúc |
| Mọi id trong `#upload-modal` | Giữ | `up-step1`, `up-step2`, `upload-dropzone`, `upload-group-select`, `upload-newgroup-*`, `up-progress-bar`, `up-progress-lbl`, `up-report`, `upload-file-list`, `upload-list-title`, `up-limit-files`, `up-limit-mb`, `upload-cancel`, `upload-back`, `upload-confirm` |
| id mới | — | `lib-summary`, `facet-collection`, `facet-issuer`, `facet-type`, `facet-status`, `facet-status-wrap`, `facet-clear`, `refine-box`, `refine-state`, `lib-count`, `lib-filters`, `lib-sort`, `lib-dir` |

### 8.2 CSS

- Khối mới `/* LIBRARY */`, lớp tiền tố `lib-`, token `--lib-*`.
- Sửa lớp sidebar `.sb-*`, `.status-dot` (D1-d); lớp modal `.modal*`, `.dropzone*`, `.up-*`, `.modal-file*` (D1-c); `.toast*`.
- Xoá lớp chỉ trang Documents cũ dùng (đã grep, không nơi nào khác dùng): `.doc-body`, `.stat-row`, `.stat-card`, `.stat-icon*`, `.stat-info`, `.doc-tabs`, `.doc-tab*`, `.doc-del-btn`, `.doc-table*`, `.doc-type-badge*`, `.st-badge*`, `.doc-act-btn`.
- Giữ nguyên lớp dùng chung: `.topbar`, `.btn*` (`.btn-icon` dùng ở panel References của Chat), `.pill*` (pill "Hybrid RRF + Rerank" ở topbar ẩn của Chat), `.spinner`, `.hidden`, `.text-muted`, `.text-sm`.
- Không đổi token có sẵn trong `:root`, vì Chat dùng chúng.
- Luật `@media` cũ cho `.stat-*`, `.doc-*` thay bằng khối responsive mới (5.11); luật cho Chat giữ nguyên.

### 8.3 JavaScript

| Hàm / hằng | Thay đổi |
|---|---|
| `loadDocuments(silent)` | **Giữ** thứ tự `loadGroups()` → `Promise.all([GET /documents, GET /stats])`, cách map `doc_group`, cập nhật `sb-doc-count`, logic poll 3 s. Đổi phần render: dòng thống kê, facet thay tab, rồi gọi `renderDocs()` |
| `renderDocs()` | Viết lại: lọc (ô tìm + facet), nhóm hoặc sắp, render phiếu thư mục, trạng thái, nút ⋯ |
| `statusBadge()`, `STATUS_LABELS`, `STEP_LABELS` | Nhãn tiếng Anh, bỏ emoji, markup `lib-st` |
| `langLabel()`, `typeSlug()` | Hết dùng (grep: chỉ trang Documents gọi) → xoá |
| `deleteDoc`, `deleteGroup`, `retryDoc`, `trackJob`, `pollReindexStatus`, handler `sync-index` | Giữ logic; chỉ đổi chuỗi (M2) và nhãn nút |
| Luồng tải lên (`addFilesToQueue` → `commitUpload`) | Giữ logic; đổi chuỗi và icon |
| Hàm mới | `libFold`, `libHighlight`, `libTitle`, `libNumber`, `libIssuer`, `libYear`, `libTypeLabel`, `libRank`, `renderLibSummary`, `renderFacets`, `openLibMenu`, `closeLibMenu` |
| **Không đổi** | `api`, `toast` (chữ ký), `escapeHtml`, `typeLabel`, `groupLabel`, `DOC_TYPE_LABELS`, `DOC_GROUP_LABELS`, `docUrl`, `loadGroups`, `showScreen`, `checkHealth`, mọi hàm của Chat |

- Menu ⋯ dùng event delegation trên `#doc-tbody` và cột Refine, thay cho handler inline `onclick="…"` hiện tại (bớt việc escape tên file trong chuỗi JS).
- Kết quả tìm kiếm luôn **bao trùm** cách cũ: cách cũ yêu cầu cả chuỗi tìm nằm trong một trường; cách mới bỏ dấu theo từng ký tự rồi đòi từng từ nằm trong chuỗi gộp các trường. Chuỗi cũ khớp thì mọi từ của nó cũng khớp.
- Không thêm thư viện JS. Không đổi `API_BASE`.

### 8.4 Font

Thêm `family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700` vào link Google Fonts đang có (vẫn một request).

---

## 9. Ma trận tác động tới các chức năng khác

| Chức năng | Có thay đổi? | Chi tiết | Được duyệt ở | Kiểm chứng |
|---|---|---|---|---|
| Trang Documents | Có | Toàn bộ giao diện; chức năng giữ nguyên | D1-a | 10.4 |
| Vị trí nút xoá, index lại, xoá nhóm | Có | Vào menu ⋯; hàm và hộp xác nhận giữ nguyên | D1-b | 10.4 (L6–L9) |
| Modal tải lên | Giao diện + chữ | Luồng, kiểm tra, API giữ nguyên | D1-c | 10.4 (U1–U9) |
| Sidebar (hiện cả ở Chat) | Giao diện + nhãn "Library" | Điều hướng, health check giữ nguyên | D1-d | 10.5 |
| Nội dung Chat, References | Không | — | — | G3, G8 |
| `GET /documents` | Thêm khoá `catalog` | Không đổi khoá cũ, không đổi `summary` | D2 | INV-4, 10.3 |
| Endpoint khác (`/chat`, `/retrieve`, `/search`, `/stats`, `/sources`, `/pdf`, `/groups`, `/uploads`, `/jobs`, `/reindex`, `/config`, `/index/health`, `/evaluate`) | Không | — | — | INV-3 |
| Parser, ingest, index, retrieval, `clause_assembler` | Không | — | — | INV-1, G3 |
| `config.yaml` | Không | — | — | diff rỗng |
| Docker, CI | Không (code) | Image mới sẽ chứa UI mới và `data/catalog.json`; volume cũ cần chép catalog (7.6) | — | 10.6 |
| README | Có | Mục "2. Documents" (tên nút, cột, trạng thái); mục "Thêm tài liệu mới" (tên nút); thêm mục catalog; bảng API ghi khoá `catalog` | D1, D2 | Đọc lại |
| Test | Thêm | `tests/test_catalog.py` | D2 | 10.3 |
| Số liệu paper, GT v2 | Không | Suy ra từ INV-1, INV-2 | — | G1, G3 |

---

## 10. Kiểm thử và cổng

### 10.1 Baseline (chụp trước khi sửa)

| ID | Nội dung |
|---|---|
| B1 | SHA-256 của `data/processed/*.jsonl` và `bm25.pkl`; `points_count` của collection `reg_chunks` |
| B2 | Snapshot JSON: `/documents`, `/groups`, `/stats`, `/sources`, `/health` (bỏ `timestamp`), `/index/health` |
| B3 | Câu trả lời Chat cho 100 câu hỏi GT v2 (answer + tier + sources) |
| B4 | DOM `#screen-chat` sau khi tải trang; ảnh chụp trang Chat |
| B5 | `python -m unittest discover -s tests -t .` (73 test) |

### 10.2 Cổng

| Cổng | Đạt khi |
|---|---|
| G1 | B1 trùng khớp |
| G3 | 100/100 câu trả lời giống hệt B3 |
| G5 | Endpoint khác trùng từng byte với B2; `/documents` trùng B2 sau khi bỏ khoá `catalog` |
| G6 | Unit test cũ và mới đều qua; E2E `ARRS_E2E=1 python -m unittest tests.test_api_e2e` qua |
| G7 | Toàn bộ kịch bản 10.4 đạt; log network khớp Phụ lục A |
| G8 | DOM `#screen-chat` trùng B4 |

### 10.3 Unit test mới: `tests/test_catalog.py` (dùng `unittest` như các test hiện có)

- Không có file → mọi dòng có `catalog` là `None`; các khoá còn lại trùng kết quả khi không có tính năng catalog.
- Có mục khớp → `catalog` chứa đúng các khoá hiển thị; không có `note`, `size`.
- `size` lệch → bỏ mục.
- `doc_number: ""` → giữ chuỗi rỗng (để UI ẩn số trích sai).
- JSON hỏng, thiếu `documents`, mục không phải object → `/documents` vẫn trả 200, `catalog` là `None`.
- Sai kiểu hoặc ngoài giới hạn (`"year": "2019"`, `"year": 3000`, `"issued": "5/4/2016"`) → bỏ khoá đó, giữ khoá khác.
- Cache: sửa file (mtime đổi) → lần gọi sau đọc nội dung mới.

### 10.4 Kiểm thử chức năng trên giao diện (server cô lập)

Thao tác phá huỷ (xoá, tải lên) chỉ chạy trên **bản sao dữ liệu + collection Qdrant tạm**, như cách đã kiểm thử Upload v2; không chạy trên kho thật. Mỗi kịch bản đối chiếu method, URL, body với Phụ lục A.

| ID | Kịch bản |
|---|---|
| L1 | Mở trang: 34 văn bản, 7 phân đoạn đúng thứ bậc, dòng thống kê đúng, sidebar hiện 34 |
| L2 | Tìm "hoc bong", "10/2016", "quoc hoi", "Luat-BHYT" (tên file) → đúng kết quả, tô sáng đúng chỗ |
| L3 | Từng nhóm lọc, kết hợp các nhóm, "Clear all filters"; số đếm đổi đúng |
| L4 | Sắp theo từng khoá, đảo chiều; bấm tiêu đề cột |
| L5 | Bấm tên hoặc dòng → mở PDF; DOCX → tải về |
| L6 | Retry (từ liên kết và từ menu) → `POST /documents/{name}/retry`, theo dõi job, toast |
| L7 | Delete document → hộp xác nhận → `DELETE /documents/{name}` → "Removing" → dòng biến mất |
| L8 | Dòng orphan → "Remove index leftovers…" |
| L9 | Delete collection → hộp xác nhận → `DELETE /groups/{id}` → bộ lọc về "All" |
| L10 | Sync index → hộp xác nhận → `POST /reindex {"mode":"sync"}` → nhãn tiến độ → toast |
| L11 | Reload |
| L12 | Poll 3 s khi có việc đang chạy; dừng khi xong |
| U1–U9 | Modal: thả file hợp lệ, sai loại, quá cỡ, quá số lượng; chọn nhóm có sẵn; nhóm mới (thiếu tên → báo lỗi); Check files → báo cáo; xung đột tên (replace / rename / skip); đổi ngôn ngữ; nhập thông tin; Back; Cancel (gọi `DELETE /uploads/{id}`); Upload & index → job → toast |
| R1 | Rộng 1440, 1280, 1100, 900, 640, 375 px: không cuộn ngang trang, không chữ đè |
| A1 | Chỉ dùng bàn phím: `/`, Tab qua nút lọc, sắp xếp, menu ⋯ (↑ ↓ Esc), modal |
| A2 | Tương phản như 4.1; `prefers-reduced-motion` tắt vòng xoay |

### 10.5 Chat

G8 (DOM `#screen-chat`) và 3 câu hỏi mẫu: câu trả lời và References giống B3. Sidebar hiển thị đúng; chuyển qua lại Chat ↔ Library.

### 10.6 Docker

Build image từ nhánh làm việc; `docker compose up` với volume mới → trang có tên văn bản từ catalog. Với volume cũ không có catalog → hiện tên suy từ file, không lỗi.

---

## 11. Kế hoạch triển khai

| Bước | Nội dung | Checkpoint |
|---|---|---|
| P1 | Baseline B1–B5 | — |
| P2 | Catalog: sinh `data/catalog.json` từ Phụ lục D (điền `size` tự động), thêm code API và `tests/test_catalog.py` | CP1, thay bằng D4: đối chiếu từng giá trị trên ảnh trang 1 của bản gốc |
| P3 | Giao diện: CSS, HTML, JS của trang Library, sidebar, modal, toast | CP2: tác giả xem trên trình duyệt |
| P4 | Chạy cổng G1–G8; cập nhật README | CP3: báo cáo kết quả |
| P5 | Commit theo phần (catalog + API; giao diện; phát hành 2.1.0; tài liệu). Phát hành theo D5: đẩy lên GitHub, tag `v2.1.0`; CI build image lên GHCR. | — |

---

## 12. Rủi ro và giả định

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Catalog sai (số hiệu, năm) khiến người dùng tin nhầm | Trung bình | Đối chiếu từng giá trị trên ảnh trang 1 của bản gốc (D4), không đọc được thì để trống; `note` ghi nguồn; chú thích nguồn dưới sổ |
| File bị thay bằng file khác cùng tên → catalog mô tả sai | Thấp | Khoá `size` |
| Chat và Library khác phong cách (Chat vẫn bong bóng xanh) | Chắc chắn | Ghi ngoài phạm vi; đề xuất đợt sau (Phụ lục C, O7) |
| Google Fonts không tải được (offline) | Thấp | Fallback Noto Serif / Times New Roman, đều đủ dấu tiếng Việt |
| Người dùng quen nút 🗑 / ↻ luôn hiện | Thấp | Dòng lỗi có liên kết "Retry indexing" ngay dưới trạng thái; menu ⋯ ở cùng vị trí trên mọi dòng |
| Hộp xác nhận tiếng Anh với người dùng quen tiếng Việt | Thấp | M2 có thể đảo khi duyệt |
| Poll 3 s đọc catalog nhiều lần | Thấp | Cache theo mtime |
| Thông điệp server tiếng Việt nằm giữa giao diện tiếng Anh | Chắc chắn | M3; có thể thêm bảng mã lỗi sau |

---

## Phụ lục A — Kiểm kê chức năng trang Documents (hợp đồng phải giữ)

Số dòng tham chiếu `ui/index.html` tại commit `c9dc34f`.

### A.1 Tải danh sách

| # | Chức năng | Kích hoạt | Hàm | API |
|---|---|---|---|---|
| A1 | Nạp trang lần đầu | Chọn Documents trên sidebar (lần đầu) | `showScreen('documents')` → `loadDocuments()` | `GET /groups`, rồi song song `GET /documents` và `GET /stats` |
| A2 | Tự cập nhật khi có việc đang chạy | Có dòng queued / processing / removing | `setInterval(() => loadDocuments(true), 3000)`; dừng khi hết việc | Như A1 |
| A3 | Tải lại | Nút Refresh | `loadDocuments()` + toast | Như A1 |
| A4 | Số đếm ở sidebar | Khi mở trang | Khối khởi tạo cuối file; cập nhật lại trong `loadDocuments` | `GET /stats` (`total_documents`), rồi `summary.total` |
| A5 | Thống kê | Sau A1 | `loadDocuments` | `summary` của `/documents`, `total_chunks` của `/stats` |

### A.2 Duyệt

| # | Chức năng | Kích hoạt | Hàm | API |
|---|---|---|---|---|
| A6 | Lọc theo nhóm | Bấm tab | `currentGroup` → `renderDocs()` | — |
| A7 | Tìm theo tên file, số hiệu, cơ quan, tiêu đề, tên gốc | Gõ ô tìm | `docFilter` → `renderDocs()` | — |
| A8 | Sắp theo cột, bấm lại để đảo chiều | Bấm tiêu đề cột | `sortCol`, `sortAsc` | — |
| A9 | Mở văn bản | Bấm dòng (trừ orphan) | `window.open(docUrl(source))` | `GET /pdf/{name}` (PDF) hoặc `GET /documents/{name}/file` (DOCX) |

### A.3 Quản trị

| # | Chức năng | Kích hoạt | Hàm | API |
|---|---|---|---|---|
| A10 | Index lại | ↻ trên dòng failed / empty / not_indexed | `retryDoc` | `POST /documents/{name}/retry` → `trackJob` poll `GET /jobs/{id}` mỗi 2 s |
| A11 | Xoá tài liệu | 🗑 (trừ removing) + hộp xác nhận | `deleteDoc` | `DELETE /documents/{name}` → `trackJob(id, 'remove')` |
| A12 | Dọn dữ liệu chỉ mục còn sót | 🗑 trên dòng orphan + hộp xác nhận | `deleteDoc` | Như A11 |
| A13 | Xoá nhóm | ✕ trên tab (trừ "Khác") + hộp xác nhận | `deleteGroup` | `DELETE /groups/{id}` → `trackJob(id, 'remove')`; bộ lọc về "Tất cả" |
| A14 | Đồng bộ chỉ mục | Nút + hộp xác nhận | Handler của `sync-index` | `POST /reindex {"mode":"sync"}` → poll `GET /reindex/status` mỗi 2.5 s |
| A15 | Tiến độ trên nút | Trong A10, A13, A14, U9 | `trackJob`, `pollReindexStatus` | — |

### A.4 Tải lên (modal)

| # | Chức năng | Kích hoạt | Hàm | API |
|---|---|---|---|---|
| U1 | Mở modal | Nút Update Documents | Handler của `reindex-docs` | `DELETE /uploads/{id}` nếu còn phiên kiểm tra cũ; `GET /groups` |
| U2 | Thêm file | Kéo thả vào vùng thả | `addFilesToQueue`: chỉ `.pdf`/`.docx`, mỗi file ≤ 50 MB, tối đa 20 file, bỏ trùng tên + cỡ | — |
| U3 | Bỏ file khỏi danh sách | ✕ | `renderUploadList` | — |
| U4 | Chọn nhóm có sẵn hoặc tạo nhóm mới | Ô chọn | `_toggleNewGroupInput`, `_selectedGroup` | — |
| U5 | Kiểm tra | Nút Kiểm tra | `runUploadCheck` | `POST /uploads` (multipart: `files`, `group`) qua XHR có tiến độ; chờ 201 |
| U6 | Báo cáo từng file (ngôn ngữ, xung đột tên, cảnh báo OCR, thông tin tuỳ chỉnh) | Sau U5 | `renderUploadReport`, `_updateCommitButton` | — |
| U7 | Quay lại | Nút Quay lại | `discardUploadSession` | `DELETE /uploads/{id}` |
| U8 | Huỷ | Nút Hủy | Như U7, rồi đóng modal | `DELETE /uploads/{id}` |
| U9 | Xác nhận | Nút "Tải lên & index N file" | `commitUpload` | `POST /uploads/{id}/commit` với `{group, files:[{file_key, action, language, metadata}], index_now:true}` → `trackJob` |

### A.5 Điều kiện đặc biệt phải giữ

- Dòng `orphan` không mở được; nút xoá của nó là "dọn".
- Dòng `removing` không có nút xoá.
- Nhóm "Khác" (văn bản không có nhóm) không xoá được.
- Nhóm đang chọn biến mất sau khi tải lại thì bộ lọc về "Tất cả".
- Toast sau commit dùng tên vừa nhập khi nhóm mới chưa có trong `groupLabelMap`.
- Sau commit, hiện tối đa 3 toast lỗi.
- Modal chặn thả file ra ngoài vùng thả (tránh trình duyệt mở file).

---

## Phụ lục B — Bằng chứng khảo sát (2026-09-27, chỉ đọc)

- Registry (gọi `build_registry` trực tiếp, cùng nguồn với `GET /documents`): 34 dòng; `title` rỗng 34, `language` rỗng 34, `doc_number` rỗng 21.
- Kiểm tra bản mẫu trên trình duyệt: 34 dòng, 7 phân đoạn đúng thứ bậc, font serif nạp được, không lỗi console; tìm "hoc bong" ra đúng văn bản học bổng và tô sáng "học bổng"; menu ⋯, trạng thái lỗi, modal hai bước hiển thị đúng ở 800, 1200, 1366 px.

---

## Phụ lục C — Vấn đề quan sát được nhưng **không** sửa (cần duyệt riêng)

| # | Vấn đề | Vị trí | Ghi chú |
|---|---|---|---|
| O1 | References trong Chat hiện mã thô `appendix`, `university_decision`, `announcement` | `DOC_TYPE_LABELS` (`ui/index.html:724`), dùng chung với Chat | Sửa 3 dòng là xong, nhưng đổi hiển thị của Chat nên cần duyệt |
| O2 | Văn bản `24.-2013-Quy-che-CTSV-noi-tru.pdf` nằm trong nhóm "Thông tư Bộ GD&ĐT" (cơ quan "Bộ Giáo dục và Đào tạo"), nhưng trang 1 là **quyết định của Giám đốc ĐHQG-HCM** (tháng 10/2013) | `data/Thong-tu-quy-che-cap-bo-gddt/` | Catalog có thể ghi đè cơ quan hiển thị. Chuyển nhóm sẽ đổi `doc_group` trong chỉ mục, ảnh hưởng mô tả corpus của paper → tác giả quyết |
| O3 | `doc_number` trích sai hoặc nhiễu ở 5 văn bản (2.2) | JSONL | Catalog chỉ sửa hiển thị; sửa tận gốc là việc của parser (đổi JSONL, vi phạm G1) |
| O4 | Số hiệu mâu thuẫn: văn bản 25 (OCR đọc 1524, tên file ghi QD1342); văn bản 6 (OCR đọc 491, tên file ghi QD-191) | — | **Đã giải quyết** khi đối chiếu ảnh trang 1 (D4): số đúng là 1342/QĐ-ĐHQG và 191/QĐ-ĐHQT-CTSV; 1524 và 491 do OCR đọc sai. Catalog ghi số đúng (Phụ lục D); JSONL giữ nguyên (O3) |
| O5 | README mục "3. Dashboard / Evaluation" mô tả màn hình không còn trong UI | `README.md:603` | Tài liệu lỗi thời |
| O6 | CSS của các màn hình đã bỏ (`#screen-search`, `#screen-analytics`, `#screen-settings`) vẫn còn; Chart.js vẫn được tải nhưng không nơi nào dùng | `ui/index.html:13`, `ui/index.html:165–209`, `252–323` | Dọn được, nhưng ngoài phạm vi |
| O7 | Trang Chat vẫn theo kiểu chatbot mẫu (🎓, thẻ gợi ý có emoji, avatar gradient, bong bóng xanh) | `#screen-chat` | Đề xuất một đợt thiết kế sau để đồng bộ với Library |
| O8 | Tải lại trang khi đang sync chỉ mục: nút *Sync index* về nhãn mặc định, không hiện tiến độ; bấm lại thì server trả 409 "A reindex is already running." | `pollReindexStatus` chỉ chạy sau khi bấm nút | Có từ bản cũ, giữ nguyên |
| O9 | Thông điệp server tiếng Việt nằm giữa giao diện tiếng Anh; thông báo trùng tên nhắc "Thay thế, Đổi tên hoặc Bỏ qua" trong khi lựa chọn hiện là *Replace*, *Rename*, *Skip* | `src/ingest` (mã `NAME_CONFLICT`) | Theo M3 (hiện nguyên văn). Sửa được bằng bảng dịch theo mã lỗi |
| O10 | Thả cùng lúc hai file trùng tên và cỡ vào vùng thả: modal không gộp chúng (chỉ gộp với file đã có trong danh sách) | `addFilesToQueue` | Có từ bản cũ; server vẫn từ chối file trùng nội dung ở bước *Check files* |
| O11 | Windows: `GET /jobs/{id}` có thể trả 404 trong tích tắc file job đang được thay (`os.replace`), vì `read_json` trả `None` khi gặp `OSError`. Test E2E không thử lại nên có thể báo `KeyError: 'status'` khi máy tải nặng | `src/ingest/registry.py`, `src/ingest/jobs.py` | Có từ trước, không liên quan tới thay đổi này (G.3) |

---

## Phụ lục D — Catalog 34 văn bản (`data/catalog.json`)

Mọi giá trị được đọc trên **ảnh trang 1 của bản PDF gốc** (2026-09-27), không lấy từ OCR hay tên file. Số hiệu của các văn bản ký số được đóng dưới dạng ảnh nên OCR đọc sai hoặc không đọc được; các số viết tay được phóng to ở 300 dpi để đọc. `size` (không in ở bảng) là kích thước file tương ứng.

| # | File | Tên (`title`) | Số hiệu | Năm | Ngày ban hành | Ghi chú (`note`) |
|---|---|---|---|---|---|---|
| 1 | `26.Luat-BHYT-2008.pdf` | Luật Bảo hiểm y tế | 25/2008/QH12 | 2008 | — | Số hiệu đọc ở trang 1. |
| 2 | `27.Luat-Giao-duc-dai-hoc-2012.pdf` | Luật Giáo dục đại học | 08/2012/QH13 | 2012 | — | Số hiệu đọc ở trang 1. |
| 3 | `28.Luat-SDBS-cua-Luat-BHYT_2014.pdf` | Luật sửa đổi, bổ sung một số điều của Luật Bảo hiểm y tế | 46/2014/QH13 | 2014 | — | Số hiệu đọc trên ảnh trang 1. |
| 4 | `29.Luat-Nghia-vu-quan-su-2015-QH13.pdf` | Luật Nghĩa vụ quân sự | 78/2015/QH13 | 2015 | — | Số hiệu đọc ở trang 1. |
| 5 | `30.Luat-An-ninh-mang-2018.pdf` | Luật An ninh mạng | 24/2018/QH14 | 2018 | — | Số hiệu đọc ở trang 1. |
| 6 | `31.Luat-Giao-duc-dai-hoc-sua-doi-bo-sung-2018.pdf` | Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học | 34/2018/QH14 | 2018 | — | Số hiệu đọc ở trang 1. |
| 7 | `32.Luat-Giao-duc-2019.pdf` | Luật Giáo dục | 43/2019/QH14 | 2019 | — | Số hiệu đọc ở trang 1. |
| 8 | `33.Luat-Thanh-nien-2020.pdf` | Luật Thanh niên | 57/2020/QH14 | 2020 | — | Số hiệu đọc trên ảnh trang 1. |
| 9 | `34.Luat-Cu-tru-2020.pdf` | Luật Cư trú | 68/2020/QH14 | 2020 | — | Số hiệu đọc trên ảnh trang 1. |
| 10 | `19.-2016-Quy-che-CTSV-Bo-GD-DT.pdf` | Quy chế công tác sinh viên đối với chương trình đào tạo đại học hệ chính quy | 10/2016/TT-BGDĐT | 2016 | 2016-04-05 | Thông tư ban hành quy chế; số và ngày đọc ở trang 1. |
| 11 | `21.-2015-Quy-che-DGKQRL-Bo-GD-DT.pdf` | Quy chế đánh giá kết quả rèn luyện của người học được đào tạo trình độ đại học hệ chính quy | 16/2015/TT-BGDĐT | 2015 | 2015-08-12 | Thông tư ban hành quy chế; số và ngày đọc ở trang 1. |
| 12 | `24.-2013-Quy-che-CTSV-noi-tru.pdf` | Quy chế công tác sinh viên nội trú | 1178/QĐ-ĐHQG-CTSV | 2013 | 2013-10-16 | Số viết tay đọc trên ảnh trang 1. Trang 1 là quyết định của Giám đốc ĐHQG-HCM, dù văn bản đang nằm trong nhóm Thông tư Bộ GD&ĐT. |
| 13 | `20.-2019-Quy-che-CTSV-DHQG-HCM.pdf` | Quy chế công tác sinh viên | 953/QĐ-ĐHQG | 2019 | 2019-07-15 | Số và ngày đọc trên ảnh trang 1. |
| 14 | `22.-2019-Quy-che-DGKQRL-DHQG-HCM.pdf` | Quy chế đánh giá kết quả rèn luyện sinh viên tại Đại học Quốc gia Thành phố Hồ Chí Minh | 964/QĐ-ĐHQG | 2019 | 2019-07-19 | Số viết tay đọc trên ảnh trang 1 (OCR chỉ đọc được chữ số cuối). |
| 15 | `23.-QD1133_22815_VNU_ban-hanh-Quy-dinh-khen-thuong-HSSV-1.pdf` | Quy định công tác khen thưởng học sinh, sinh viên tại Đại học Quốc gia Thành phố Hồ Chí Minh | 1133/QĐ-ĐHQG | 2022 | 2022-08-15 | Số và ngày đọc trên ảnh trang 1. |
| 16 | `25.-QD1342_220930_QD-ban-hanh-Quy-che-dao-tao-trinh-odo-DH-upload.pdf` | Quy chế đào tạo trình độ đại học | 1342/QĐ-ĐHQG | 2022 | 2022-09-30 | Số và ngày đọc trên ảnh trang 1 (OCR đọc nhầm thành 1524). |
| 17 | `11.QD-586-QD-DHQT-_-Quy-tac-ung-xu-nguoi-hoc.pdf` | Quy tắc ứng xử của người học tại Trường Đại học Quốc tế | 586/QĐ-ĐHQT | 2019 | — | Số và năm đọc trên ảnh trang 1. |
| 18 | `12.Quy-che-to-chuc-hoat-dong-cua-P.CTSV-theo-QD-515-ngay-8.9.2022.pdf` | Quy chế tổ chức và hoạt động của Phòng Công tác Sinh viên Trường Đại học Quốc tế | 515/QĐ-ĐHQT | 2022 | 2022-09-08 | Số và ngày đọc trên ảnh trang 1. |
| 19 | `13.-Quy-dinh-chinh-sach-ho-tro-nguoi-hoc-khuyet-tat.-Signed-4.pdf` | Quy định chính sách hỗ trợ người học khuyết tật tại Trường Đại học Quốc tế | 912/QĐ-ĐHQT | 2024 | 2024-12-09 | Số và ngày đọc trên ảnh trang 1 (số trích tự động 51/2010/QH12 là số của luật được viện dẫn). |
| 20 | `14.-Quy-dinh-doi-voi-Ban-can-su-lop-nam-2025-final-Signed-4.pdf` | Quy định đối với Ban cán sự lớp tại Trường Đại học Quốc tế | 151/QĐ-ĐHQT | 2025 | 2025-03-21 | Số và ngày đọc trên ảnh trang 1. |
| 21 | `15.-QD-719-6.12.2021_Quy-che-Hoc-vu-DH-Quoc-te-2021.signed-1.signed.signed.signed.signed-2.pdf` | Quy chế đào tạo trình độ đại học theo hệ thống tín chỉ tại Trường Đại học Quốc tế | 719/QĐ-ĐHQT | 2021 | 2021-12-06 | Số và ngày đọc trên ảnh trang 1. |
| 22 | `16.-Quy-dinh-xet-cap-hoc-bong-tai-Truong-DHQT-Signed-4.pdf` | Quy định xét cấp học bổng khuyến khích học tập cho sinh viên trình độ đại học tại Trường Đại học Quốc tế | 884/QĐ-ĐHQT | 2024 | 2024-08-19 | Số và ngày đọc trên ảnh trang 1. |
| 23 | `17.-Quy-che_To-chuc-thi-HK_Tr-Tiep.ban-hanh_05.01.25-Signed-8.pdf` | Quy chế tổ chức thi học kỳ trình độ đại học tại Trường Đại học Quốc tế | 07/QĐ-ĐHQT | 2025 | 2025-01-08 | Số và ngày đọc trên ảnh trang 1. |
| 24 | `18.-QD-ban-hanh-Quy-dinh-Co-van-hoc-tap.signed.signed.signed.signed.signed.pdf` | Quy định công tác cố vấn học tập tại Trường Đại học Quốc tế | 748/QĐ-ĐHQT | 2022 | 2022-09-08 | Số và ngày đọc trên ảnh trang 1. |
| 25 | `5.-Quyet-dinh-ban-hanh-_-Quy-dinh-cong-tac-khen-thuong-sinh-vien-truong-DHQT-Signed-4.pdf` | Quy định công tác khen thưởng sinh viên tại Trường Đại học Quốc tế | 808/QĐ-ĐHQT | 2024 | 2024-10-30 | Số và ngày đọc trên ảnh trang 1. |
| 26 | `9.-QD688-MGHP-2024.pdf` | Quy định chế độ chính sách miễn, giảm học phí | 668/QĐ-ĐHQT | 2024 | 2024-09-16 | Số và ngày đọc trên ảnh trang 1; tên file ghi 688. |
| 27 | `Quy-che-CTSV-theo-QD-967-12.2022-Signed-2.pdf` | Quy chế công tác sinh viên Trường Đại học Quốc tế | 967/QĐ-ĐHQT | 2022 | 2022-12-26 | Quy chế kèm theo quyết định; số và ngày đọc trên ảnh trang 1. |
| 28 | `10.-Noi-Quy-SV-truong-DHQT-cap-nhat-08-2009.pdf` | Nội quy sinh viên | 276/QĐ-ĐHQT/ĐT | 2008 | 2008-09-10 | Ban hành theo quyết định có số viết tay, đọc là 276 (OCR: 716); ngày 10/9/2008. |
| 29 | `2.-Quyet-dinh-dieu-chinh-Quy-che-CTSV-09_1_25-Signed-4.pdf` | Sửa đổi, bổ sung Quy chế công tác sinh viên Trường Đại học Quốc tế | 41/QĐ-ĐHQT | 2025 | 2025-01-17 | Số và ngày đọc trên ảnh trang 1. |
| 30 | `272-Phu-luc-Quy-dinh-doi-voi-BCSL.pdf` | Bổ sung Quy định đối với Ban cán sự lớp tại Trường Đại học Quốc tế | 272/QĐ-ĐHQT | 2025 | 2025-05-13 | Số và ngày đọc trên ảnh trang 1. |
| 31 | `6.-QD-191_2011_Quy-dinh-che-do-chinh-sach.pdf` | Quy định chế độ chính sách | 191/QĐ-ĐHQT-CTSV | 2011 | 2011-03-31 | Số viết tay đọc trên ảnh trang 1 (OCR đọc nhầm thành 491). |
| 32 | `3.-Phu-luc-1-30122022-Signed-2.pdf` | Phụ lục I — Một số nội dung vi phạm và khung xử lý kỷ luật sinh viên | 967/QĐ-ĐHQT | 2022 | 2022-12-26 | Số hiệu là của quyết định mà phụ lục đi kèm (đọc trên ảnh trang 1).; `issuing_body`: Trường Đại học Quốc tế |
| 33 | `4.-Phu-luc-II-Tieu-chi-va-Khung-DRL-Signed-3.pdf` | Phụ lục II — Tiêu chí và khung điểm đánh giá kết quả rèn luyện sinh viên | 41/QĐ-ĐHQT | 2025 | 2025-01-17 | Số hiệu là của quyết định mà phụ lục đi kèm (đọc trên ảnh trang 1).; `issuing_body`: Trường Đại học Quốc tế |
| 34 | `7.-TB.486-MGHP-04112014-thong-bao-P.CTSV_.pdf` | Hướng dẫn thực hiện chính sách miễn, giảm học phí mới áp dụng từ học kỳ II năm học 2014–2015 | 486-TB/ĐHQT-CTSV | 2014 | 2014-11-04 | Số viết tay và ngày đọc trên ảnh trang 1. |

**Khác với bản nháp của design (đã sửa theo văn bản gốc):**

- `25.-QD1342…`: số **1342**/QĐ-ĐHQG (OCR đọc nhầm thành 1524); ngày 30/9/2022.
- `23.-QD1133…`: năm **2022** (15/8/2022), không phải 2015 như tên file gợi ý.
- `9.-QD688-MGHP-2024.pdf`: số **668**/QĐ-ĐHQT (tên file ghi 688).
- `6.-QD-191_2011…`: số **191**/QĐ-ĐHQT-CTSV (OCR đọc nhầm thành 491).
- `10.-Noi-Quy…`: ban hành theo quyết định số **276**/QĐ-ĐHQT/ĐT ngày 10/9/2008 (số viết tay; OCR đọc thành 716). Đây là giá trị kém chắc chắn nhất của catalog.
- Số hiệu trước đây để trống, nay đọc được trên văn bản: 964/QĐ-ĐHQG, 1178/QĐ-ĐHQG-CTSV, 515, 912, 884, 07, 748, 808, 41, 272/QĐ-ĐHQT, 486-TB/ĐHQT-CTSV; hai phụ lục ghi số của quyết định đi kèm (967 và 41/QĐ-ĐHQT).
- `24.-2013-Quy-che-CTSV-noi-tru.pdf` là quyết định của Giám đốc ĐHQG-HCM (số 1178/QĐ-ĐHQG-CTSV) nhưng nằm trong nhóm "Thông tư Bộ GD&ĐT". Catalog **không** ghi đè cơ quan hay loại văn bản của nó; xem Phụ lục C, O2.

## Phụ lục E — File thay đổi hoặc thêm mới

| File | — | Loại |
|---|---|---|
| `ui/index.html` | — | Sửa: trang Documents, sidebar, modal, toast, link font |
| `api/main.py` | — | Sửa: `list_documents()` + 3 hàm catalog |
| `data/catalog.json` | — | Mới |
| `tests/test_catalog.py` | — | Mới |
| `README.md` | — | Sửa: mục giao diện, mục thêm tài liệu, mục catalog, bảng API, ghi chú Docker; tên thư mục trong cây thư mục và Quick start |
| `docker-compose.yml`, `CITATION.cff` | — | Sửa: phiên bản 2.1.0 (F-11) |
| `docs/design/documents-library-redesign.md` | — | Tài liệu này |
| `docs/design/documents-library-mockup.html` | — | Bản mẫu giao diện |

---

## Phụ lục F — Điều chỉnh khi triển khai (so với bản thiết kế)

| # | Điều chỉnh | Lý do |
|---|---|---|
| F-1 | Catalog được đối chiếu trên **ảnh trang 1** của từng PDF gốc (D4). Năm giá trị của bản nháp sai và đã được sửa; mọi số hiệu đọc được đều được ghi (Phụ lục D). | Số hiệu của văn bản ký số được đóng dạng ảnh hoặc viết tay; OCR và tên file đều có chỗ sai. |
| F-2 | Văn bản `24.-2013-Quy-che-CTSV-noi-tru.pdf` **không** được ghi đè cơ quan hay loại văn bản trong catalog. | Nó là quyết định của ĐHQG-HCM nằm trong nhóm Thông tư; sửa một nửa (chỉ cơ quan) làm bản ghi tự mâu thuẫn. Để tác giả quyết (O2). |
| F-3 | Khi sắp xếp, chuỗi được so theo thứ tự số tự nhiên (`localeCompare(…, 'vi', { numeric: true })`): tên file 2 < 10 < 34, số hiệu 07 < 151 < 1133. | Thứ tự chữ đặt "10" trước "2". |
| F-4 | `STEP_LABELS` thêm `running → Syncing`. | `/reindex/status` trả giai đoạn `running` trước khi có bước cụ thể; bản cũ hiện nguyên chữ `running`. |
| F-5 | Xoá `typeSlug()` (tiện ích) và `langLabel()`. `DOC_TYPE_LABELS`, `typeLabel()` giữ nguyên. | Chỉ bảng Documents cũ dùng hai hàm này; hai hàm còn lại phục vụ References của Chat. |
| F-6 | Liên kết "N in progress" / "N need attention" trên dòng thống kê bật bộ lọc Availability theo trạng thái đầu tiên đang có (queued, processing, removing; hoặc failed, empty). `not_indexed` không tính là "need attention". | Khớp cách API tính `summary.processing` và `summary.failed`. |
| F-7 | Tên văn bản là thẻ `<a href>` thật (mở được bằng chuột giữa, Ctrl+click); click vào phần còn lại của dòng vẫn gọi `window.open` như cũ. | Truy cập bằng bàn phím và thói quen trình duyệt. |
| F-8 | Thân hàm `toast()` giữ nguyên, nên khi tắt toast vẫn trượt sang phải như cũ; chỉ màu và hiệu ứng xuất hiện đổi bằng CSS. | Không sửa hàm dùng chung khi không cần. |
| F-9 | Trạng thái rỗng có nút "Clear the search and filters"; kho rỗng có liên kết "Add documents"; lỗi tải có "Try again". | Luôn có một lối ra. |
| F-10 | Menu ⋯ tự đóng khi cuộn, đổi kích thước cửa sổ, click ra ngoài, Tab hoặc Esc; Esc trả focus về nút mở menu. | Menu đặt `position: fixed`, không bám theo dòng khi cuộn. |
| F-11 | Phát hành 2.1.0 (D5): `docker-compose.yml` mặc định image `v2.1.0`, `CITATION.cff` và version của API là `2.1.0`. | Image `v2.0.1` không có giao diện mới. |
| F-12 | README: mục Library, mục catalog, bảng API, ghi chú volume Docker tạo trước 2.1.0 (chưa có catalog), ghi chú đường dẫn dài khi clone trên Windows. | Tài liệu khớp giao diện và cách triển khai. |
| F-13 | `prefers-reduced-motion` tắt cả vòng xoay `.spinner` trong trang Library (dòng đang tải, nút Add documents và Sync index), không chỉ dấu trạng thái. Quy tắc giới hạn trong `#screen-documents`. | Kiểm thử A2 phát hiện bản đầu chỉ tắt dấu trạng thái, chưa đúng 5.12. |
| F-14 | Chú thích nguồn dưới sổ (5.6) đổi thành câu không nêu thứ tự ưu tiên. | Câu cũ nói catalog đứng trước chi tiết nhập lúc upload, trái với 7.3 (tiêu đề nhập lúc upload đứng trước catalog). |

---

## Phụ lục G — Kết quả kiểm thử (2026-09-27)

Code mới chạy trên một server riêng (cổng 8011) với **bản sao** thư mục `data/` và một Qdrant riêng nạp từ `data/qdrant_seed`. Baseline là code tại `c9dc34f` chạy trên cùng bản sao. Mọi thao tác xoá, tải lên, đồng bộ chỉ chạy trên bản sao; kho thật không bị ghi. Kiểm thử Docker dùng image build từ đúng cây mã được phát hành, chạy thành một stack Compose riêng.

### G.1 Cổng

| Cổng | Kết quả |
|---|---|
| G1 | Đạt. 34 file JSONL và `bm25.pkl` của kho thật trùng SHA-256 với baseline; collection `reg_chunks` vẫn có 4.224 điểm. |
| G3 | Đạt. 100/100 câu hỏi GT v2 có câu trả lời và danh sách nguồn (chunk, file, trang) giống hệt baseline; điểm rerank bằng nhau trong sai số 1e-4. |
| G5 | Đạt. `/groups`, `/stats`, `/sources`, `/health` (bỏ `timestamp`), `/index/health`, `/jobs`, `/reindex/status` trùng từng byte; `/documents` trùng sau khi bỏ khoá `catalog` (có ở 34/34 dòng). |
| G6 | Đạt (G.3). |
| G7 | Đạt (G.2). |
| G8 | Đạt. DOM `#screen-chat` sau khi tải trang giống hệt bản `c9dc34f` (2.742 ký tự, so trực tiếp trên trình duyệt); CSS, HTML và JS của Chat trong `ui/index.html` không đổi byte nào. Chat trả lời được trên giao diện mới (5 liên kết PDF, References (10)). |

### G.2 Kịch bản giao diện (10.4)

| ID | Kết quả |
|---|---|
| L1 | 34 văn bản, 7 phân đoạn theo thứ bậc 7.4; dòng thống kê "34 documents in 7 collections · 4,224 indexed passages · all 34 available in Chat"; sidebar hiện 34. |
| L2 | "hoc bong", "10/2016", "quoc hoi", "Luat-BHYT" ra đúng văn bản và tô sáng đúng chỗ. Kết quả tìm mới luôn chứa kết quả của cách tìm cũ: 0 cặp bị mất trên 5.916 cặp truy vấn–văn bản. |
| L3 | Lọc từng nhóm (ví dụ Type: Law → 9 văn bản, Appendix → 2), kết hợp nhiều nhóm, bỏ từng bộ lọc, "Clear all filters"; số đếm và nhãn "1 filter on" đúng. |
| L4 | Sắp theo từng khoá và đảo chiều, bằng ô Order và bằng tiêu đề cột (`aria-sort` đổi theo); thứ tự số tự nhiên (F-3); quay lại chế độ nhóm theo collection. |
| L5 | Tên văn bản, click dòng và mục "Open" mở PDF qua `/pdf/{tên}` (200, `application/pdf`). DOCX: click dòng và "Download the original" mở `/documents/{tên}/file` (200, tải về dạng file đính kèm). Dòng orphan không mở được. |
| L6 | Retry từ liên kết "Retry indexing" dưới trạng thái và từ menu ⋯ → `POST /documents/{tên}/retry` → processing → Available; toast "“…” is queued for indexing." rồi "Indexed 1 document — you can ask about it in Chat now." |
| L7 | Delete document → hộp xác nhận → `DELETE /documents/{tên}` → "Removing" → dòng biến mất. |
| L8 | Dòng orphan (file bị xoá khỏi đĩa): trạng thái "Index leftovers", không mở được; menu chỉ có "Remove index leftovers…" → hộp xác nhận → dòng biến mất. |
| L9 | "Delete collection…" trong menu ⋯ của collection (nhóm "Other" không có menu) → hộp xác nhận → `DELETE /groups/{id}` → bộ lọc về All, còn 34 văn bản. |
| L10 | Hộp xác nhận đúng chữ; nút "Syncing…" → "Reading n/34…" → "Embedding n/4224…" → "Indexing…" → trở lại "Sync index"; toast "Index sync complete."; danh sách tải lại (34 văn bản, 4.224 đoạn). Bấm lại khi đang chạy: server trả 409, toast "Could not start the sync: A reindex is already running.", nút trở lại. Bản đầu hiện nguyên chữ `running` ở giai đoạn chờ, đã sửa (F-4). |
| L11 | Reload: toast "Reloading the list…", danh sách tải lại. |
| L12 | Có dòng queued / processing / removing thì trang tự tải lại mỗi 3 s; hết việc thì dừng. |
| U1–U9 | Mở modal (`GET /groups`); chọn collection có sẵn hoặc tạo mới (thiếu tên → "Enter a name for the new collection", không gửi request); thả file: `.txt` bị loại, file > 50 MB bị loại, thả 21 file thì giữ 20, file đã có trong danh sách không bị thêm lần hai; nút ✕ bỏ từng file. Check files → báo cáo từng file ("Checked 3 files: 2 accepted, 1 rejected"), thông điệp server hiện nguyên văn; trùng nội dung → từ chối (`DUPLICATE_CONTENT`); trùng tên → Replace / Rename / Skip (chọn Skip hết → "No files to upload", nút khoá). Đổi ngôn ngữ, nhập tiêu đề và số hiệu; Back và Cancel gọi `DELETE /uploads/{id}`; Upload & index → job → hai văn bản "Available" mang tiêu đề và số hiệu đã nhập. |
| R1 | 1440, 1280, 1279, 1100, 900, 822, 640, 375 px: không cuộn ngang. ≥ 1280 px: Refine là cột dính rộng 220 px; < 1280 px: Refine nằm trên sổ, tải trang thì đóng, bấm để mở. ≤ 900 px: sidebar chỉ còn icon, ẩn cột Passages. ≤ 640 px: ẩn hàng tiêu đề cột, mỗi dòng thành phiếu ("2020 · 23 pp · Available"). |
| A1 | `/` đưa focus vào ô tra cứu (chỉ trên trang Library; ô nhập của Chat không bị ảnh hưởng); menu ⋯ mở bằng Enter, ↑ ↓ di chuyển, Esc đóng và trả focus về nút mở. |
| A2 | Tỉ lệ tương phản đo trên trang khớp bảng 4.1. Quét 463 phần tử chữ của trang Library, 6 của sidebar, 18 của modal: không phần tử nào dưới 4.5:1, trừ dấu "·" phân cách (trang trí, 3.65:1). Viền điều khiển: 3.65:1 trên trắng, 3.35:1 trên nền giấy. `prefers-reduced-motion` dừng mọi vòng xoay (F-13). Modal có `role="dialog"`, `aria-modal`, `aria-labelledby`. |

### G.3 Unit test và E2E

| Nơi chạy | Kết quả |
|---|---|
| Windows, `python -m unittest discover -s tests -t .` | 83 test đạt (73 cũ, 10 mới trong `tests/test_catalog.py`); test E2E tự bỏ qua khi chạy chung. |
| Windows, `ARRS_E2E=1 … tests.test_api_e2e` | Đạt khi máy rảnh (1 test, 204 s). Lần chạy trước, song song với job sync của server kiểm thử trên cùng máy, báo `KeyError: 'status'`: `GET /jobs/{id}` trả 404 trong tích tắc file job đang được thay (O11). Lỗi nằm ở phần không bị sửa (`src/ingest`, endpoint `/jobs`) và có từ trước. |
| Container, unit | 83 test đạt (1 bỏ qua như trên). |
| Container, E2E | Đạt (1 test, 343 s). |

### G.4 Docker (10.6)

Image build từ cây mã được phát hành: các lớp cài thư viện dùng lại cache, chỉ lớp mã nguồn đổi. Stack Compose riêng, volume mới; volume model được chép từ một stack có sẵn để khỏi tải lại.

- Sẵn sàng sau 92 s: nạp 4.224 vector từ `data/qdrant_seed`; hai model đúng revision đã khoá.
- `/chat` với câu hỏi mẫu của CI: có câu trả lời, 10 nguồn, không nguồn nào trùng. API báo version 2.1.0.
- `/documents`: 34/34 dòng có `catalog`, giống hệt bản chạy trên Windows. Trang Library hiện đúng tên văn bản và 7 phân đoạn.
- Giả lập volume tạo trước 2.1.0 (xoá `data/catalog.json` trong container): `/documents` trả `catalog: null` ở cả 34 dòng; trang vẫn chạy, hiện tên suy từ tên file, không lỗi. Chép lại bằng lệnh trong README (`docker compose cp data/catalog.json app:/app/data/catalog.json`): 34/34 dòng có lại `catalog`, không cần khởi động lại.
