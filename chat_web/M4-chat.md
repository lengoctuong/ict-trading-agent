Check lại. Lần này **raw source mới đã có M4.1.1 thật**: `M4ReplayEngine.version = "0.1.1"`, có `m4_support.py`, study window, experiment manifest, Exness calendar, dynamic context và causal reference builder.

## Kết quả review lại M4.1.1

| Status                    | Task                          | Hiểu đơn giản                                      | Review                                                                                                                                                                                                                                                                       | Confidence | Need review      |
| ------------------------- | ----------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------: | ---------------- |
| ✅ DONE                    | Warm-up                       | Có history trước ngày bắt đầu thống kê             | `replay_start < analysis_start`, warm-up vẫn chạy detector nhưng không tính vào analysis.                                                                                                                                                                                    |        98% | Không            |
| ✅ DONE                    | Reproducible run              | Biết chính xác data/code/config nào tạo result     | Manifest đã hash git SHA, M2/M3/M4 version, source hashes, symbol metadata, detector configs, M3 policy, calendar/reference/context/target policy.                                                                                                                           |        99% | Không            |
| ✅ DONE                    | MT5 symbol metadata           | Không đoán tick size/digits                        | Có adapter từ `MT5.symbol_info()` và engine dùng `trade_tick_size`.                                                                                                                                                                                                          |        98% | Không            |
| ✅ DONE                    | Exness D1 / PDH-PDL           | PDH/PDL lấy từ D1 Exness thật                      | `build_exness_xauusd_intraday_v0()` dùng UTC + native D1; reference builder chỉ emit sau D1 close.                                                                                                                                                                           |        97% | Không            |
| ✅ DONE                    | Dynamic session context infra | Mỗi timestamp biết context NY/DST                  | `SessionContextProvider` convert UTC → `America/New_York`, lưu cả sessions + NY timestamp.                                                                                                                                                                                   |        96% | **Có**, xem dưới |
| ✅ DONE                    | Session H/L causal builder    | Không hindsight Asia/London H/L                    | Chỉ emit session high/low khi complete source window.                                                                                                                                                                                                                        |        97% | Không            |
| ✅ DONE                    | Exness calendar framework     | Market closure không bị coi là missing bars        | Có regular calendar + exceptional closures explicit.                                                                                                                                                                                                                         |        92% | Không            |
| 🟠 **FIX trước M4.2**     | **Session overlap semantics** | London và NY AM có thể cùng active                 | Current provider bắt phải chọn `primary_session`; nếu overlap mà không priority thì raise. Tôi không muốn research ép một session thắng session kia.                                                                                                                         |    **97%** | **Có**           |
| 🟠 **FIX trước dùng TDO** | **TDO availability**          | 00:00 NY open được biết ngay lúc 00:00             | Builder hiện để TDO `available_at = bar.close_time`, tức M5 source thì trễ 5 phút.                                                                                                                                                                                           |    **95%** | Không            |
| 🟡 VERIFY DATA            | Exness exact closure minutes  | Đừng tin lịch hard-code trước khi nhìn actual feed | Code dùng precise `20:58→22:02` summer / `21:58→23:02` winter. Exness official xác nhận UTC+0, US DST và XAU thường đóng quanh rollover, nhưng trang public hiện mô tả rollover thường 21:00/22:00 và kéo dài 1–2 giờ chứ không chứng minh trực tiếp các phút hard-code đó.  |        85% | Không            |

## Issue mới quan trọng nhất: session overlap

Ta đã chọn session là **context, không phải hard filter**. Nhưng current API:

```text
sessions = [LONDON, NY_AM]
primary_session = ?
```

và nếu hai window overlap mà chưa đặt priority, code raise error.

Tôi không thích giải bằng:

```text
NY_AM > LONDON
```

vì sẽ mất thông tin research.

Tôi muốn:

```text
sessions = [LONDON, NY_AM]
primary_session = optional
```

và M4 breakdown làm **multi-label**:

```text
setup counted in:
- London
- NY_AM
- London+NY_AM overlap
```

Sau này LLM cũng được nhìn cả hai.

**Không nên buộc market phải thuộc đúng một session.**

---

## TDO có một lỗi semantic nhỏ

Code hiện tạo TDO:

```text
00:00 NY M5 bar opens
...
00:05 bar closes
→ TDO becomes available
```



Nhưng concept đúng phải là:

```text
00:00 NY
→ open price đã biết
→ TDO level exists
```

Hệ thống vẫn chỉ ra decision trên closed bars, không vấn đề. Nhưng fact phải phản ánh:

```text
occurred_at = 00:00
available_at = 00:00
```

hoặc có riêng `observed_at` nếu replay engine cần log lúc ingest.

Không nên encode sai concept chỉ vì implementation closed-bar.

---

# Exness calendar: không blocker architecture nữa

Exness xác nhận server trading clock là **UTC+0**, DST schedule theo instrument, và gold thường đóng trong rollover; rollover thường bắt đầu 21:00 UTC mùa hè / 22:00 UTC mùa đông. ([Exness Help Center][1])

Current `ExnessXauCalendarPreset` đã đúng hướng: regular closure + exceptional holidays riêng.

Nhưng khi có CSV thật tôi muốn làm:

```text
actual XAU gaps
vs
calendar expected gaps
```

Nếu actual Exness bars là:

```text
20:55 → 22:05
```

thì calendar phải learn/validate theo actual schedule, không mở rộng closure chỉ để loader pass.

---

# M4 hiện tại tôi đánh thế này

```text
M4.1 replay skeleton          PASS
M4.1.1 hardening              ~95% PASS
M3 semantics                  FROZEN

Còn trước first serious replay:
1. session overlap multi-label
2. TDO availability semantics
```

Hai patch này nhỏ. **Không cần M4.1.2 lớn.**

Sau đó sang **M4.2 real empirical run**:

| M4.2 task                   | Answer đơn giản                               | Need review            |
| --------------------------- | --------------------------------------------- | ---------------------- |
| Load real Exness XAU        | Chạy market thật                              | Không                  |
| Validate calendar/data gaps | Data có sạch không                            | Không                  |
| First multi-month replay    | Xem detector bắt được gì                      | Có                     |
| Detection distributions     | Reclaim/shift/FVG/reaction thường mất bao lâu | **Có**                 |
| Near-miss analysis          | Current parameters đang miss gì               | **Có, rất quan trọng** |
| Chart sampling              | Bạn nhìn trực tiếp concept có đúng không      | **Có, bắt buộc**       |
| Parameter sensitivity       | Tune `3/12/8/4/24/16/6` bằng data             | **Có**                 |
| Outcome MFE/MAE             | Setup sau đó chạy tốt/xấu thế nào             | Có                     |
| Regression cases            | Khóa các XAU setup đã review thành tests      | Không                  |

**Kết luận:** patch session-overlap + TDO semantics rồi bắt đầu M4.2. Không thấy lý do quay lại M3.

[1]: https://get.exness.help/hc/en-us/articles/4405235684498-Instrument-trading-hours "Instrument trading hours – Exness Help Center"
