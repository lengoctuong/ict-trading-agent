# Dev update — M4.1.1

Đã xử lý feedback trên nền SHA `4b690523792a0ea99cc74d27ab4ece2ade208ac8`.

| Status | Hạng mục | Kết quả implementation |
| --- | --- | --- |
| ✅ DONE | Exness XAU closure calendar | Thêm preset versioned theo giờ XAU chính thức: daily/weekend break, UTC, US-DST aware. Holiday/maintenance bất thường phải truyền interval chính xác; unknown gap vẫn strict error. |
| ✅ DONE | Warmup/study window | Bắt buộc `replay_start < analysis_start`, hỗ trợ `analysis_end`; warmup chạy nguyên production M2/M3 nhưng event/setup cohort warmup không vào main summary. Audit vẫn giữ đầy đủ `study_phase` và `included_in_analysis`. |
| ✅ DONE | Experiment manifest/fingerprint | Run ID hash toàn manifest: raw-data SHA-256, Git SHA, M2/M3/M4 version, MT5 metadata, candle/displacement/M3 config, adjacency/calendar, reference/context/target policy và study window. |
| ✅ DONE | Causal references infrastructure | Native Exness D1 sinh PDH/PDL lúc D1 close; completed session H/L và TDO có builder causal nhưng chỉ bật bằng policy/session clock tường minh. Developing session range không bị xuất thành completed fact. |
| ✅ DONE | Dynamic context infrastructure | `SessionContextProvider` chuyển UTC sang `America/New_York` bằng IANA DST và annotate từng timestamp. Không filter setup. Không có default session windows tự đoán. |
| ✅ DONE | MT5 symbol metadata | Engine không còn nhận tick size rời; bắt buộc snapshot `digits`, `point`, `trade_tick_size`, hỗ trợ adapter trực tiếp từ `mt5.symbol_info()`, và ghi vào manifest. |
| ✅ DONE (local-only) | MT5 connection env | Đã copy đúng nhóm `BROKER` + `MT5_*` từ `copy-trading-bot` sang `.env.mt5.local`; file bị Git ignore và không được push. |
| 🟠 OPEN — cần planner/user review | Exact ICT session windows | Cần chốt giờ Asia/London/NY AM/NY PM trước khi tạo `SessionSchedule` dùng cho real run. Infrastructure đã sẵn sàng, không invent default. |
| 🟠 OPEN — cần source schedule | Holiday/exceptional XAU closures | Danh sách ngày holiday không đủ chứng minh giờ XAU đóng/mở. Mỗi real-data range cần snapshot exact Exness schedule rồi truyền `MarketClosure`; không auto-whitelist cả ngày. |
| 🟠 OPEN — cần planner/user review | TDO policy | Builder hỗ trợ clock/timeframe tường minh, nhưng chưa freeze `00:00 New York` thành project default trước khi review ICT relevance và data granularity. |
| ⏭️ NEXT | M4.2 real replay | Lấy native Exness bars + live MT5 symbol metadata, dùng 40–60 D1 warmup, chạy audit rồi chart-review trước parameter distribution/tuning. |

Test mới cover DST transition, weekend/daily closure, unknown-gap fail-closed,
warmup exclusion, full manifest identity, NY DST context, causal D1 references,
và metadata mismatch. `docs/m4_replay.md` đã cập nhật usage/contract M4.1.1.

---

Review đúng SHA **`4b690523792a0ea99cc74d27ab4ece2ade208ac8`**. M4.1 nhìn chung **PASS về skeleton replay/audit**, nhưng chưa nên chạy M4.2 parameter tuning ngay. Có vài prerequisite mới lộ ra khi chuẩn bị dùng **Exness XAUUSD thật**. Commit đúng là thêm CSV ingestion, close-time MTF replay, audit/near-miss dataset và summary. ([GitHub][1])

## Review M4.1

| Status                      | Task / vấn đề                            | Hiểu đơn giản                                                          | Review / cách dev                                                                                                                                                                      | Confidence | Need review                           |
| --------------------------- | ---------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------: | ------------------------------------- |
| ✅ **DONE**                  | Exness CSV ingestion                     | Đọc được data MT5                                                      | Hỗ trợ CSV/TSV MT5, OHLC, tick volume, spread, extra columns; timestamp UTC.                                                                                                           |        97% | Không                                 |
| ✅ **DONE**                  | Point-in-time replay                     | Quá khứ chạy như live                                                  | Bar chỉ append khi close; H1 chưa close thì chưa được dùng.                                                                                                                            |        98% | Không                                 |
| ✅ **DONE**                  | MTF same-close ordering                  | M5 evidence có thể được M15 thấy khi cùng đóng                         | Order `M1→M5→M15→H1→H4→D1→W1`; test có cover M5 trước M15.                                                                                                                             |        96% | Không                                 |
| ✅ **DONE**                  | Production path                          | Không tạo detector riêng cho backtest                                  | Replay gọi trực tiếp M2 rồi M3 hiện tại.                                                                                                                                               |        98% | Không                                 |
| ✅ **DONE**                  | Audit dataset                            | Có thể trace từng setup về evidence                                    | Bar/fact/candidate/raid/update/setup/transition/READY đều được lưu raw payload.                                                                                                        |        98% | Không                                 |
| ✅ **DONE**                  | Near-miss dataset                        | Không chỉ lưu setup pass                                               | Có late reclaim, late shift, late FVG/reaction, expiry + threshold/excess.                                                                                                             |        97% | Không                                 |
| 🟠 **P0 trước real run**    | **Exness closure calendar**              | Daily break của vàng không phải missing data                           | Strict loader hiện sẽ coi gap là error nếu không truyền closure calendar. Exness XAUUSD thực sự có daily rollover closure và DST thay đổi. Cần preset Exness XAU calendar.             |    **98%** | Không                                 |
| 🟠 **P0 trước real run**    | **Warm-up period**                       | Không được bắt đầu research với detector “mất trí nhớ”                 | Engine hiện chờ đủ candle baseline rồi mới chạy M2/M3; default baseline dài sẽ làm mất events đầu sample. Cần `warmup_start` và `analysis_start`.                                      |    **98%** | Không                                 |
| 🟠 **HIGH**                 | **Run reproducibility ID chưa đủ**       | Đổi detector config nhưng có thể vẫn ra cùng run ID                    | Hash hiện chỉ gồm data + M3 policy + initial facts; thiếu tick size, displacement config, adjacency policy, target/context, **code/SHA/version**. Phải bổ sung trước parameter sweep.  |    **99%** | Không                                 |
| 🟠 **HIGH cho M4.2**        | **Session context chưa dynamic**         | Replay nhiều ngày nhưng không biết từng setup thuộc Asia/London/NY nào | `context` hiện truyền một lần vào engine; summary đọc session từ READY payload. Cần derive session theo timestamp cho từng event/setup.                                                |    **97%** | **Có khi review session definitions** |
| 🟠 **HIGH cho M4.2**        | **PDH/PDL/session levels chưa tự build** | Raw CSV thôi chưa đủ liquidity references quan trọng                   | Docs hiện yêu cầu reference facts được supply từ ngoài. Cần causal `ReferenceLevelBuilder`: PDH/PDL Exness D1 + session H/L + TDO etc.                                                 |    **97%** | **Có — ICT relevant**                 |
| 🟡 **Before empirical run** | Tick size / symbol metadata              | Đừng đoán precision của XAU                                            | `tick_size` hiện caller nhập tay. Với real Exness run nên lấy từ MT5 symbol metadata và log `digits/point/tick_size`.                                                                  |        96% | Không                                 |

---

# 1. Exness closure calendar là blocker đầu tiên

Loader làm đúng khi **không silently sửa gap**. Nhưng nếu lấy vài tháng XAUUSD M5, sẽ có gap hợp lệ hàng ngày.

Exness xác nhận MetaTrader server chạy GMT/UTC+0, và XAUUSD thường đóng quanh forex rollover; rollover khoảng **21:00 UTC mùa hè / 22:00 UTC mùa đông**, với lịch có DST/holiday/maintenance. ([Exness Help Center][2])

Hiện loader:

```text
gap
├─ nằm trong ExplicitClosureCalendar → warning
└─ không nằm calendar → ERROR
```



Đúng design. Nhưng ta chưa có **Exness XAU calendar preset**.

### Tôi đề xuất

```text
ExnessXauClosureCalendar
├─ regular weekday rollover breaks
├─ weekend closure
├─ DST-aware
├─ holidays / exceptional schedules
└─ unknown gap vẫn ERROR
```

Không hard-code kiểu:

```text
mọi gap 21:00–23:00 đều bỏ qua
```

vì có thể che mất data bị thiếu thật.

---

# 2. Warm-up là vấn đề quan trọng hơn nghe tưởng

Code hiện:

```python
minimum_bars =
    max(2, candle_feature_baseline_period) + 1

if bars < minimum_bars:
    skip M2/M3
```



Giả sử baseline = 20:

```text
M5  → first ~100 phút không detect
M15 → first ~5 giờ không detect
H1  → first ~21 giờ không detect
H4  → first ~84 giờ không detect
D1  → first ~21 ngày không detect
```

Chưa kể swing hierarchy cần lịch sử để ITH/LTH hình thành.

Vậy không được:

```text
Data Jan 1 → Dec 31
Analysis Jan 1 → Dec 31
```

mà nên:

```text
Warmup data:
Nov/Dec trước đó
        ↓
build swings / baselines / levels
        ↓
Jan 1 = analysis_start
        ↓
từ đây mới tính statistics
```

### Proposal

Engine có:

```text
replay_start
analysis_start
analysis_end
```

Warm-up vẫn **chạy production detector đầy đủ**, chỉ không tính các setup trước `analysis_start` vào nghiên cứu chính.

Tôi muốn ít nhất **40–60 D1 bars warm-up** cho pilot, vì mình còn D1/H4 context. Không coi 60 là ICT parameter — chỉ là state initialization.

---

# 3. Run ID hiện có bug research khá quan trọng

Hiện fingerprint là đại khái:

```text
symbol
M3 policy
initial facts
records
```



Nhưng nếu chạy:

```text
Run A:
displacement config X

Run B:
displacement config Y
```

với cùng data và M3 policy:

> có thể sinh **cùng `run_id`**.

Tệ hơn:

```text
commit 4b6905...
vs
future commit abc123...
```

có thể cùng ID.

Sau M4.2 ta sẽ chạy rất nhiều parameter configs, nên phải sửa ngay.

Tôi muốn manifest:

```text
run_id hash(
    source data hashes
    git commit SHA
    M2 version
    M3 version
    M4 version
    tick_size
    candle feature config
    displacement config
    M3 policy
    adjacency/calendar policy
    reference policy
    context policy
    target policy
)
```

Sau này một result luôn trả lời được:

> **Code nào + config nào + data nào tạo ra result này?**

Đặc biệt quan trọng cho ML dataset sau này.

---

# 4. Session context hiện chưa thật sự replay được

Summary đã có:

```text
session breakdown
```

nhưng nó lấy:

```text
READY payload → context → session
```



Trong khi `context` đang được truyền **một lần** vào `M4ReplayEngine`.

Nếu replay 6 tháng thì không thể:

```python
context={"session": "NY_AM"}
```

vì setup lúc 02:00 và setup lúc 14:00 rõ ràng khác nhau.

### Cần M4.2

```text
timestamp UTC
    ↓
TemporalContextBuilder
    ↓
America/New_York DST-aware
    ↓
ASIA / LONDON / NY_AM / NY_PM
```

Và **session chỉ annotate**, không filter.

Đây là nơi bạn cần review exact windows nếu chúng ta dùng chúng làm ICT feature.

---

# 5. PDH/PDL và session liquidity vẫn chưa sinh từ data

Docs hiện nói reference facts như PDH/PDL/session levels phải được build causally và truyền vào `initial_facts`; replay không tự backfill.

Điều này an toàn nhưng M4 chưa usable hoàn chỉnh.

Ta cần:

```text
Exness bars
↓
ReferenceLevelBuilder
├─ Previous Exness D1 High/Low
├─ Asia High/Low
├─ London High/Low
├─ NY AM High/Low
├─ local swings
└─ ICT TDO 00:00 NY
```

Mỗi level phải có:

```text
occurred_at
available_at
source timeframe/session
```

Ví dụ **Asia High**:

```text
Asia chưa kết thúc
→ developing high, KHÔNG được coi completed Asia High

Asia session complete
→ final Asia High becomes available
```

Hoặc nếu sau này muốn “developing Asia H/L” thì phải là concept/reference type riêng.

Đây rất quan trọng để tránh hindsight.

---

# Bảng next tasks — M4.1.1 → M4.2

| Priority | Task                     | Answer đơn giản                                 | Cách dev                                                              | Confidence | Need review            |
| -------- | ------------------------ | ----------------------------------------------- | --------------------------------------------------------------------- | ---------: | ---------------------- |
| **P0**   | Exness XAU calendar      | Phân biệt market closed với missing data        | DST-aware Exness closure calendar + holidays, unknown gaps fail       |        98% | Không                  |
| **P0**   | Replay warm-up           | Detector phải có history trước ngày nghiên cứu  | `replay_start < analysis_start`; process warmup nhưng exclude metrics |        98% | Không                  |
| **P0**   | Run manifest/fingerprint | Mỗi experiment phải reproduce được              | Data hash + SHA + all detector/policy configs                         |        99% | Không                  |
| **P1**   | Reference level builder  | Tự sinh PDH/PDL/session liquidity point-in-time | Exness D1 PDH/PDL + session H/L + provenance                          |        96% | **Có**                 |
| **P1**   | Dynamic temporal context | Mỗi setup biết đúng session                     | UTC → NY DST-aware session classifier                                 |        97% | **Có**                 |
| **P1**   | MT5 symbol metadata      | Không hard-code precision                       | ingest/log digits, point, trade tick size                             |        96% | Không                  |
| **M4.2** | First real XAU replay    | Lần đầu xem detector trên market thật           | vài tháng data + full audit                                           |        95% | **Có**                 |
| **M4.2** | Chart sample review      | Kiểm tra concept có thật sự đúng                | sample RAID/SHIFT/FVG/READY/near-miss                                 |        95% | **Có, rất quan trọng** |
| **M4.2** | Parameter distributions  | Kiểm tra 3/12/8/4/24/16/6                       | distribution trước, chưa optimize PnL                                 |        95% | **Có**                 |
| **M4.2** | Outcome labels           | Xem sau setup market đi đâu                     | future MFE/MAE/DOL/time-to-level                                      |        92% | Có                     |

## Kết luận

**M4.1 code: PASS.**

Không thấy lỗi nào khiến tôi muốn quay lại sửa M3 concepts.

Nhưng trước khi bắt đầu **M4.2 empirical research**, tôi muốn một patch nhỏ **M4.1.1**:

```text
Exness closure calendar
+ warm-up/study window
+ complete experiment fingerprint
+ dynamic time/reference infrastructure
```

Sau đó mới lấy **real Exness XAUUSD data** và bắt đầu phần quan trọng nhất của M4: chart review + near-miss/parameter distributions.

Điểm đáng mừng là từ đây các vấn đề mới chủ yếu là **research/data correctness**, không còn là redesign ICT state machine.

[1]: https://github.com/lengoctuong/ict-trading-agent/commit/4b690523792a0ea99cc74d27ab4ece2ade208ac8 "feat: implement M4.1 causal replay audit · lengoctuong/ict-trading-agent@4b69052 · GitHub"
[2]: https://get.exness.help/hc/en-us/articles/4405235684498-Instrument-trading-hours?utm_source=chatgpt.com "Instrument trading hours - Exness Help Center"
