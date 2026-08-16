# M4.1 planning and implementation record

> **Implementation update — 2026-08-17:** M4.1 đã implement phần được giao:
> native Exness/MT5 CSV/TSV ingestion, strict UTC/data-quality validation,
> point-in-time multi-TF replay qua đúng production M2+M3, audit event dataset,
> near-miss dataset, basic summary statistics và JSONL export. Parameter tuning,
> chart review và MFE/MAE thuộc M4.2, chỉ bắt đầu sau khi replay/data thật được
> review.

## `cached main` là gì?

`main` là **tên branch**, nó luôn trỏ tới commit mới nhất.

Ví dụ đúng lúc đó:

```text
main
  ↓
9b704d6
```

Nhưng khi tôi mở qua web/raw GitHub, lớp cache/search ở giữa đôi khi vẫn trả nội dung của commit cũ:

```text
main → đáng lẽ 9b704d6
        nhưng response cache → bản M3.2 cũ
```

Trong khi URL theo SHA:

```text
.../9b704d6/.../m3.py
```

là **immutable**: SHA đó luôn là đúng source của commit đó, không thay đổi.

Vậy từ giờ review code:

```text
commit SHA cụ thể > main
```

Nếu Codex nói vừa push, tốt nhất review đúng SHA mới nhất.

---

# M4 giờ làm gì?

M3 đã trả lời:

> **“Market này có tạo ra candidate ICT setup theo definition của mình không?”**

M4 bắt đầu trả lời:

> **“Trên XAUUSD thật, detector này có bắt đủ setup không, parameter có ngu không, và những setup nó bắt có đặc điểm/kết quả như thế nào?”**

M4 **chưa cần LLM trade**.

Flow:

```text
Exness XAUUSD historical data
        ↓
point-in-time replay
        ↓
M2 facts
        ↓
M3 setup lifecycle
        ↓
audit dataset
        ↓
chart review + statistics
        ↓
calibrate detector
```

---

# M4 task table

| Task                                 | Hiểu đơn giản                                          | Cách dev chi tiết                                                                                                    | Confidence | Need review            |
| ------------------------------------ | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- | ---------: | ---------------------- |
| **1. Exness XAUUSD historical data** | Có data thật để test                                   | Lấy M1/M5 và/hoặc native TF từ Exness MT5; giữ UTC, bid/ask/spread nếu lấy được; validate gaps/duplicates            |    **98%** | Không                  |
| **2. Multi-TF replay engine**        | Chạy quá khứ giống live, không nhìn tương lai          | Feed bar theo timestamp; H1 chỉ available khi H1 đóng; M15/M5 tương tự; dùng chính M2+M3 production path             |    **98%** | Không                  |
| **3. Detection audit dataset**       | Lưu mọi thứ detector nhìn thấy                         | Mỗi raid/shift/FVG/reaction/setup lưu raw facts, TF, swing ranks, penetration, windows, session, reasons             |    **98%** | Không                  |
| **4. Near-miss dataset**             | Quan trọng để biết mình đang bỏ sót gì                 | Lưu `late_reclaim`, `late_shift`, `late_fvg`, `late_reaction`, expired setup và khoảng cách vượt threshold bao nhiêu |    **99%** | **Có**                 |
| **5. Parameter sensitivity**         | Test xem các số 3/8/16 bars có tệ không                | Replay nhiều configs nhưng **không đổi concept definition**; so recall, setup counts, outcome distribution           |    **95%** | **Có, rất quan trọng** |
| **6. Chart review samples**          | Bạn nhìn trực tiếp xem detector có hiểu ICT đúng không | Export các setup representative + near-miss ra chart có raid/structure/FVG/target annotations                        |    **95%** | **Có, bắt buộc**       |
| **7. Setup outcome labeling**        | Biết sau setup market thực sự chạy thế nào             | Không cần giả định TP cố định ngay; trước tiên tính future MFE/MAE, direction return, time-to-target, adverse move   |    **93%** | Có                     |
| **8. Target candidate generation**   | Biết lúc setup xuất hiện có những liquidity target nào | Local swing, session H/L, PDH/PDL, external liquidity; snapshot đúng `as_of`                                         |    **92%** | **Có**                 |
| **9. Session/TF breakdown**          | Xem setup hoạt động khác nhau thế nào                  | Breakdown Asia/London/NY, H1/M15 setup, M5 entry, same-bar vs multi-bar raid, swing rank                             |    **95%** | Có                     |
| **10. Regression fixtures**          | Sau này sửa code không làm mất setup tốt               | Chọn các case XAU thực tế đã review → biến thành deterministic tests                                                 |    **99%** | Không                  |
| **11. Data quality report**          | Tránh backtest sai vì data                             | Missing bars, weekend/rollover gaps, duplicated bars, abnormal spreads, DST/session alignment                        |    **97%** | Không                  |
| **12. M4 report**                    | Chốt detector trước M5                                 | Tổng hợp detected/missed/near-miss, parameter recommendations, concept issues mới                                    |    **95%** | **Có**                 |

---

# M4 không nên làm gì trước

Chưa nên:

```text
❌ optimize win rate
❌ optimize RR
❌ train ML
❌ tune LLM prompt
❌ build live execution
❌ thêm OB / SMT / OTE / 20 concept khác
```

Vì trước tiên phải chắc:

> **Dataset setup mà ta đưa cho LLM có đủ và đúng không?**

Nếu detector bỏ mất 40% valid MSS/FVG thì LLM giỏi tới đâu cũng không cứu được.

---

# M4 nên đo gì?

Tôi muốn có **2 nhóm metrics riêng**.

### A. Detection / semantics metrics

Đây là ưu tiên đầu tiên:

```text
# liquidity raids
# same-bar sweeps
# multi-bar reclaims

# shifts
# late shifts

# linked FVG
# failed FVG
# reactions
# late reactions

# READY_FOR_LLM
# INVALIDATED
# EXPIRED
```

Breakdown theo:

```text
session
setup TF
reference TF
swing rank
raid type
shift lag
FVG penetration
reaction lag
```

Đây giúp biết detector đang hoạt động thế nào.

---

### B. Outcome metrics

Sau mỗi setup, không vội gọi `win/loss`.

Trước tiên lưu:

```text
future MFE
future MAE

max favorable move in ATR
max adverse move in ATR

time to +1R
time to +2R
time to candidate DOL

whether DOL eventually taken
```

Ví dụ:

```text
Setup A:
MFE = +4.2R
MAE = -0.35R

Setup B:
MFE = +0.4R
MAE = -2.1R
```

Sau này M5 có thể học phân biệt A/B.

---

# Parameter calibration quan trọng nhất

Các số hiện tại như:

```text
multi-bar reclaim <= 3 bars

raid → shift:
M5 12
M15 8
H1 4

FVG reaction window = 3

entry-zone expiry:
M5 24
M15 16
H1 6
```

**không được optimize theo PnL ngay.**

Đầu tiên phải xem distribution.

Ví dụ:

```text
reclaim delay:

0 bar    55%
1 bar    22%
2 bars   11%
3 bars    5%
4 bars    3%
5+ bars   4%
```

Nếu vậy `<=3` capture 93% candidate behavior.

Nhưng nếu:

```text
0–3 bars = 60%
4–6 bars = 30%
```

thì current parameter đang quá strict.

Tương tự raid→shift.

---

# Chart review sẽ là phần bạn quan trọng nhất

Tôi đề xuất Codex tự tạo sample buckets:

```text
20 canonical good detections
20 same-bar raid+shift
20 multi-bar raids
20 late-shift near misses
20 FVG failures
20 expired setups
20 random READY_FOR_LLM
```

Bạn review mỗi case:

```text
✅ đúng concept
🟡 technically đúng nhưng irrelevant
❌ detect sai
❌ missed important structure
```

Sau đó lưu label này.

Đây còn có thể trở thành dataset cho:

```text
future semantic classifier / ML
```

---

# M4 Definition of Done

Tôi sẽ chưa coi M4 xong chỉ vì “backtester chạy được”.

M4 PASS khi:

```text
1. Exness XAU replay point-in-time chạy ổn
2. M2/M3 outputs được audit đầy đủ
3. Near-misses không bị mất
4. Có chart review trên sample thực
5. Các parameter 3/12/8/4/24/16/6 có evidence
6. Có outcome MFE/MAE dataset
7. Phát hiện và fix các semantic detector bug nghiêm trọng
8. Một số setup thực tế được freeze thành regression tests
```

Sau đó mới sang **M5: LLM semantic decision**.

---

## Việc giao Codex ngay bây giờ

Tôi sẽ giao **M4.1 trước**, không build cả M4 một phát:

```text
Exness XAU data ingestion
→ point-in-time multi-TF replay
→ audit/event dataset
→ near-miss dataset
→ basic summary statistics
```

**Chưa tune parameter.**

Khi Codex push M4.1, tôi review data/replay correctness trước. Sau đó M4.2 mới làm chart review + parameter sensitivity + outcome labeling. Đây là cách ít rủi ro nhất để không optimize trên một replay engine đang sai.
