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
