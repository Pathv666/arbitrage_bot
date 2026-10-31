# Chạy V1 trên laptop Windows trước VPS

Mục tiêu của giai đoạn này là xác minh scanner **read-only** trên laptop Windows trước khi bật lại triển khai VPS.

## Điều kiện

- Windows 10/11.
- Python 3.11+; script mặc định dùng Python 3.11 qua `py -3.11`.
- Git.
- Internet tới các endpoint P2P công khai của Binance, OKX và Bybit.
- Không cần API key giao dịch hoặc quyền withdrawal.

## 1. Clone

```powershell
git clone https://github.com/Pathv666/arbitrage_bot.git
cd arbitrage_bot
```

Nếu đã clone:

```powershell
git switch main
git pull
```

## 2. Cài đặt

Trong PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup.ps1
```

Script:
- tạo `.venv`;
- cài package + dev dependencies;
- tạo `.env` từ `.env.example` nếu chưa có;
- không ghi đè `.env` hiện hữu;
- chạy toàn bộ pytest.

## 3. Cấu hình an toàn ban đầu

Giữ:

```dotenv
SCANNER_V1=true
EXCHANGES=Binance,OKX,Bybit
BINGX_ENABLED=false
FIAT=VND
ASSETS=USDT
NO_INPUT=true
```

Điều chỉnh `MAX_FIAT` theo số vốn bạn muốn **mô phỏng/đánh giá**. Scanner không tự giao dịch.

### NETWORK_COSTS_JSON

Mặc định để:

```dotenv
NETWORK_COSTS_JSON=[]
```

Khi trống, scanner vẫn có thể đọc order books và tính fills nhưng **không đánh dấu route cross-exchange là khả thi**. Chỉ nhập snapshot sau khi fee/minimum/deposit/withdraw status được xác minh trực tiếp trên từng sàn.

Không dùng giá trị minh họa trong `.env.example` như dữ liệu thật.

### Nhập snapshot phí mạng đã xác minh

Không sửa NETWORK_COSTS_JSON bằng tay. Sau khi kiểm tra trực tiếp màn hình rút/nạp của sàn, dùng:

    .\scripts\windows\set_network_snapshot.ps1 -Exchange Binance -Network TRC20 -WithdrawFee <fee> -MinWithdrawal <minimum>

Lặp lại cho OKX/Bybit. Script tự ghi timestamp UTC và thay snapshot cũ cùng exchange/asset/network.

Xem snapshot hiện tại:

    .\.venv\Scripts\python.exe -m p2p_arb_bot.network_snapshot --env .env show

Sau khi thay snapshot, restart scanner. Snapshot quá NETWORK_COST_TTL_S sẽ tự hết hiệu lực.
### Tự động lấy phí/trạng thái mạng qua API chỉ-đọc

Nếu có API credentials chỉ-đọc, scanner sẽ tự refresh metadata mạng và ghi health vào dashboard. Không cần nhập snapshot thủ công định kỳ.

Trong `.env`:

```dotenv
NETWORK_METADATA_REFRESH_S=600

BINANCE_API_KEY=
BINANCE_API_SECRET=

OKX_API_KEY=
OKX_API_SECRET=
OKX_API_PASSPHRASE=

BYBIT_API_KEY=
BYBIT_API_SECRET=

BINGX_API_KEY=
BINGX_API_SECRET=
```

Quy tắc an toàn:
- chỉ tạo key có quyền **Read** / quyền đọc tài sản cần thiết;
- **không bật Trade**;
- **không bật Withdraw**;
- ưu tiên bind IP khi sàn hỗ trợ;
- `.env` đã nằm trong `.gitignore`; không copy key vào issue/PR/chat;
- nếu collector lỗi khi refresh, snapshot động cũ không được giả làm dữ liệu mới; route liên quan sẽ mất snapshot và quay lại REJECTED trừ khi có manual snapshot còn hiệu lực.

Nguồn API:
- Binance: signed USER_DATA `GET /sapi/v1/capital/config/getall`;
- OKX: authenticated `GET /api/v5/asset/currencies`;
- Bybit: authenticated `GET /v5/asset/coin/query-info`;
- BingX: authenticated Read `GET /openApi/wallets/v1/capital/config/getall`.

Manual snapshot vẫn là fallback có chủ ý qua `set_network_snapshot.ps1`.
## 4. Smoke test read-only

```powershell
.\scripts\windows\smoke.ps1
```

Smoke chỉ đọc endpoint thị trường và chạy tests; không tạo P2P order, không paid/release, không withdrawal.

## 5. Chạy scanner

```powershell
.\scripts\windows\run_scanner.ps1
```

Dừng bằng Ctrl+C.

Theo dõi:
- console;
- `p2p_arb_bot.log`;
- `status.json`;
- `opportunities.db`.

## 6. Dashboard local

Mở PowerShell thứ hai:

```powershell
.\scripts\windows\run_dashboard.ps1
```

Mặc định truy cập `http://127.0.0.1:8000`.

Nếu chỉ giám sát thì không cần đặt `DASHBOARD_PASSWORD`. Nếu bật hành động quản trị local, đặt password + secret trong `.env`.

## 7. Tiêu chí đạt trước VPS

Chỉ chuyển sang VPS khi:
- CI GitHub pass;
- local tests pass;
- smoke Binance/OKX/Bybit pass ổn định;
- scanner chạy liên tục nhiều giờ mà không chết khi một exchange lỗi;
- dashboard/status có quote age và health hợp lý;
- network-cost snapshots đã được xác minh và route gating hoạt động đúng;
- không có secret trong repo;
- không có hành vi tạo order/paid/release/withdrawal.

## VPS deployment

Workflow VPS hiện là **manual-only**. Không push nào lên `main` tự deploy V1. Chỉ bật lại auto-deploy sau khi local acceptance hoàn tất và cấu hình VPS được audit riêng.

## Observabilidad de red y Telegram (después de 944beda)

Scanner không in exception gốc từ API/transport. `status.json` và dashboard dùng
mã lỗi cố định: `authentication`, `access_denied`, `clock`, `rate_limit`, `timeout`,
`dns`, `tls`, `schema`. Không copy key, signed URL hoặc response lỗi vào chat.
Bảng diagnostics hiển thị riêng withdraw ở Binance và deposit ở OKX cho từng
network, freshness, fee/minimum và lý do reject. Thiếu fee không được coi là 0.
Khi đã cấu hình API collector, lỗi collector không tự fallback sang manual snapshot
của chính sàn đó. Các sàn không cấu hình API vẫn dùng snapshot manual đã xác minh.

Telegram dùng cấu hình token/chat hiện có, không cần thay đổi API permission.
Alerts theo transition: health lỗi/hồi phục, collector lỗi/hồi phục, route
rejected/feasible, net edge qua threshold, deposit/withdraw thay đổi. Cùng trạng thái
không gửi lặp; cooldown mặc định 180 giây cho mỗi topic; trạng thái cuối còn pending
sẽ được gửi khi hết cooldown. Gating/cooldown của alert opportunity vẫn độc lập.
Không bật Trade/Withdraw để xử lý lỗi Read. Với lỗi auth/IP/region, dừng phần phụ
thuộc, kiểm tra cấu hình read-only trên laptop; scanner còn lại vẫn tiếp tục.

Khi chạy test, dùng `ENV_PATH` trỏ tới một file rỗng trong thư mục tạm để test không
load `.env` thật. Không gửi `.env` hoặc file log cũ chưa được lọc credential.
