# Checkpoint UNI trên BingX — báo cáo bàn giao

Trạng thái hiện tại: **BLOCKED — chưa xác nhận READY_FOR_UNI_SELL_REVIEW**.
Không có order mua/bán/paid/release/transfer/withdraw hoặc thay đổi API permissions.

## Nền và các nhóm thay đổi

Đã đọc lịch sử 12 PR đã merge; nền chính xác là commit
`944bedad42538f9c41629b9b45be071fd585f557` (network diagnostics). Checkout làm việc
ở cloud Linux, không phải filesystem laptop `C:\Users\pc\arbitrage_bot`.
`.env` thật không được mở, in, sửa, ghi đè hay commit bởi tác vụ này. Tests dùng
ENV_PATH file tạm rỗng; fixtures auth đều giả. Không tải credential laptop lên cloud.

Các PR được apilar để review/rollback theo nhóm:

1. #13 — `dfbc409`: safe errors, directional diagnostics và transition alerts.
2. #14 — `647b20e`: GET-only SPOT clients, balance/order contracts, market/fees và
   pure liquidity preview; CI Python 3.11/3.12 pass.
3. Nhánh `feat/uni-readonly-checkpoint` — tích hợp runtime background, private
   dashboard/report, Windows helpers, no-leak logs và regression bổ sung.

Không tự merge. CI chạy pytest + compileall ở Python 3.11 và 3.12, kể cả PR apilado.
Báo cáo trạng thái CI chính xác của head nằm trên GitHub PR và phần bàn giao chat.
Không có linter/type checker được cấu hình; không thêm tooling mới.

## Đã hoàn thành bằng code/test

- Diagnóstico Binance → OKX: hiện từng network, origin withdrawal enabled/fresh,
  timestamp/age, fee/min; destination deposit enabled/fresh/timestamp/age; lý do
  reject và safe collector error code. Missing fee không là fee=0.
- API errors không đưa signed URL, headers, response msg hay exception/context gốc
  vào status/log. HTTP/auth/IP/clock/rate-limit/timeout/DNS/TLS/schema có mã riêng.
- Telegram: health, collector lỗi/recovery, route feasibility, threshold, network
  deposit/withdraw changes; dedup/cooldown/pending transitions; retry riêng từng
  channel để console thành công không che Telegram lỗi. Có test-notification
  tường minh, không cần thay balance/order để kiểm thử.
- Balance Binance/BingX spot và OKX funding/trading_cash: UNI/USDT, free/locked,
  timestamp, significant-change alerts. Không dùng unrealized P&L/equity làm cash.
- Order monitoring UNI-USDT: open + recent history, pending/partial/filled/
  cancelled/failed, paging bounded. Không tạo/sửa/hủy order, không coi open hoặc
  biến mất là fill. OKX tdMode khác cash bị từ chối.
- Account polling độc lập với scanner P2P; lỗi/chậm một collector không chặn các
  task khác. API data incomplete/expired/unauthorized làm phần phụ thuộc BLOCKED.
- Account snapshot riêng, không công khai balance/order trong status.json/log;
  account pane yêu cầu login, no-store, không hiển thị dữ liệu hết TTL.
- Module UNI đọc symbols/depth/bookTicker và commissionRate chính thức; chuẩn hóa
  asks giảm dần do BingX thực tế trả về; kiểm tra timestamp/consistency và limits.
- Preview chỉ mô phỏng UNI available, làm tròn xuống step, walk bids, VWAP,
  slippage, fee quote equivalent, unfilled; không cộng projected net vào cash.
- Output paths được kiểm tra để không ghi đè .env hay status công khai. Windows
  setup/smoke dùng test helper ENV_PATH tạm; setup giữ nguyên .env hiện hữu.

Local validation cuối: **302 tests pass**, compileall và git diff --check pass.
Không gửi Telegram thật, không dùng credential thật, không thực thi giao dịch.
PowerShell helpers đã review nhưng cần chạy thực tế trên laptop Windows.

## Dữ liệu thực đã lấy và phần chưa thể xác nhận

Public smoke BingX thành công lúc **2026-10-31 17:24:03 UTC+7**:

- UNI-USDT active, bid 6.976 USDT, ask 6.989 USDT.
- Depth 100 bid levels + 100 ask levels, snapshots có server timestamps mới và
  nhất quán ở lần quan sát này. Đây là mẫu lịch sử, không dùng để gửi order.
- tickSize 0.001; stepSize 0.001; minQty 0.933; maxQty 18667.165.
- minNotional 1 USDT; maxNotional 757852.02918248 USDT theo API tại lần kiểm tra.

**UNI balance xác nhận: UNKNOWN. USDT balance: UNKNOWN.** Không có authenticated
read-only access laptop trong môi trường này; không giả định zero. Account/order
health và fee riêng của tài khoản vẫn cần chạy local. Public-only report trả
BLOCKED đúng thiết kế. Không đủ quantity/fee thật để xác nhận VWAP/slippage/net
cho lượng UNI thực tế. Không tuyên bố check A–F đã đạt live đầy đủ.

Inputs phải lấy lại khi review: UNI free/locked, USDT free/locked, open/partial
orders, maker/taker fee có timestamp (và fee asset/discount), bids/asks/depth mới,
quantity dự định, step/tick/min/max, liquidity legs, remainder, VWAP/slippage.
Cloud allowlist hiện không gồm api.binance.com/openapi.okx.com cho private REST;
đây là giới hạn môi trường, không là bằng chứng adapter sai. Không bypass policy.

## Thao tác người dùng cần làm trên laptop

Thực hiện quy trình trong `docs/LOCAL_WINDOWS.md`, nhánh cuối
`feat/uni-readonly-checkpoint`, giữ nguyên .env hiện hữu:

1. Fetch/switch nhánh, chạy setup/test helper mới; không reset/clean đè file local.
2. Chạy `uni_review.ps1 -PublicOnly` để xác minh market/network mà không đọc .env.
3. Chạy `uni_review.ps1` để tiến trình local ký GET bằng Read credentials hiện có.
4. Nếu đã cấu hình Telegram, chạy `uni_review.ps1 -Notify -TestNotification`; có
   thể dùng `-Watch -Notify` để quan sát thay đổi mà không tạo order.
5. Nếu dùng dashboard: `run_scanner.ps1 -Accounts` và `run_dashboard.ps1`; account
   pane cần cấu hình login local hiện hữu. Không chia sẻ .env, key/token/passphrase.
6. Với auth/Read/IP/clock/region hoặc endpoint fee bị từ chối, giữ phần đó BLOCKED,
   kiểm tra read-only access trên laptop; **không bật Trade/Withdraw**. Nếu API
   Read không cho fee/order data, lấy các giá trị đó từ UI thủ công để review tiếp,
   không giả lập chúng là kết quả API thành công.

Chỉ chia sẻ report đã lọc hoặc các trường health/balance/market/fee cần thiết,
không log cũ hay credential. Chưa có bằng chứng live account/fee/Telegram thì
không được báo READY_FOR_UNI_SELL_REVIEW và không được bán UNI.
