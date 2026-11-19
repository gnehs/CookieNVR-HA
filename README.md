# CookieNVR for Home Assistant

[English](#english) | [繁體中文](#繁體中文)

## 繁體中文

CookieNVR（家用 NVR）的 Home Assistant 整合。狀態透過 SSE 即時推送，影像來自 NVR 的 RTSP 轉發，不會另外連相機。

### 需求

- Home Assistant 2026.8.0 或更新（實測 2026.10.4）
- CookieNVR，API 版本 1（`/api/info` 的 `apiVersion`）

### 安裝（HACS）

1. HACS → 右上角選單 → 自訂儲存庫，加入本 repo，類別選「Integration」。
2. 下載「CookieNVR」，重新啟動 Home Assistant。
3. 設定 → 裝置與服務 → 新增整合 → CookieNVR。

手動安裝：把 `custom_components/cookienvr` 複製到 HA 設定目錄的 `custom_components/`。

### 設定

1. CookieNVR 在區域網路上廣播自己（mDNS，需要 host 網路），HA 的「裝置與服務」會出現「已發現：CookieNVR」，按「新增」。找不到時改按「新增整合 → CookieNVR」輸入網址（例如 `http://192.168.1.10:8765`）。
2. HA 顯示一個連結與一組代碼（例如 `BCDF-GHJK`）。開啟連結、登入 CookieNVR，確認代碼一樣後按「允許」，HA 會自動完成設定。不需要建立或複製任何 Token。
3. HA 拿到的權限跟按「允許」的帳號相同：一般使用者可以看畫面、收事件、控制 PTZ；要在 HA 切換錄影與偵測開關、看磁碟用量，請用管理員帳號核准。核准後的連線會列在 CookieNVR 的「帳號 → 連接其他 App」，可隨時撤銷。
4. 撤銷後 HA 會要求重新連接，走同一個核准流程。NVR 換了 IP 後 HA 會從廣播自動更新網址。
5. 打不開核准頁時（例如 HA 用的網址從你的瀏覽器連不到），可以在 CookieNVR 的「帳號 → 連接其他 App」建立 API Token，貼到設定表單的「API Token（進階）」欄位。

NVR 新增或刪除攝影機後，HA 自動同步，不需要重啟整合。

### 實體

每台攝影機是一個裝置，NVR 本身另有一個裝置。

| 實體 | 說明 |
| --- | --- |
| `camera.<名稱>` | 快照取自 NVR；串流來源為 NVR 的 RTSP 轉發（main） |
| `binary_sensor.<名稱>_motion` | 動作與事件進行中 |
| `binary_sensor.<名稱>_online` | 攝影機上線 |
| `binary_sensor.<名稱>_<類別>` | 進行中的事件含該類別（物件辨識啟用後才有） |
| `switch.<名稱>_recording`、`_detection` | 錄影與偵測開關（需管理員 Token） |
| `select.<名稱>_ptz_preset`、`button.<名稱>_ptz_*` | PTZ 預置點與上下左右微調（有 PTZ 才有） |
| `image.<名稱>_last_event`、`sensor.<名稱>_last_event` | 最近事件的縮圖與時間 |
| NVR：磁碟容量、可用、用量、錄影總量、正常 worker 數 | 需管理員 Token |

### 服務

- `cookienvr.ptz_move`：`pan`、`tilt`、`zoom`（-1 到 1）、`mode`（`continuous` 或 `relative`）、`duration`、`speed`。
- `cookienvr.ptz_preset`：`action`（`goto`、`save`、`delete`）、`preset`、`name`。儲存與刪除需管理員 Token。
- `cookienvr.export_clip`：`start`，加上 `end` 或 `duration`（擇一），可選 `filename`。影片寫到 HA 的 media 目錄下的 `cookienvr/`，並回傳 `path` 與 `media_content_id`。

三個服務的對象都是這個整合的 `camera` 實體。

### 事件

每筆事件開始與結束都會發出 `cookienvr_event`：

```yaml
camera: 前門          # 攝影機名稱
camera_id: abc123
device_id: 0f1e...    # HA 裝置 ID
event_id: 42
state: start          # start 或 end
score: 0.42
labels: []            # 物件類別，沒有物件辨識時為空
thumbnail_url: http://nvr:8765/api/events/42/thumb.jpg?token=cnvr_...
playback_url: http://nvr:8765/#/recordings?camera=abc123
```

`thumbnail_url` 帶著 Token 以便推播軟體直接抓圖；不要把這個網址貼到公開的地方。

### 連線可靠性

SSE 斷線後以 1 到 30 秒退避重連，重連後先取得完整狀態；另外每 60 秒輪詢一次。NVR 連不到時所有實體變為 unavailable。

### 開發

```
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

## English

Home Assistant integration for CookieNVR, a home NVR. State is pushed over SSE, and video comes from the NVR's RTSP restream, so Home Assistant never opens a second connection to the camera.

### Requirements

- Home Assistant 2026.8.0 or newer (tested on 2026.10.4)
- CookieNVR with API version 1 (`apiVersion` from `/api/info`)

### Install (HACS)

1. HACS, top-right menu, Custom repositories. Add this repository as an Integration.
2. Download "CookieNVR" and restart Home Assistant.
3. Settings, Devices and services, Add integration, CookieNVR.

### Setup

1. CookieNVR announces itself on the local network (mDNS, needs host networking), so Settings, Devices & services shows it as discovered. Select Add. If it does not show up, add the CookieNVR integration and enter the address (for example `http://192.168.1.10:8765`).
2. Home Assistant shows a link and a code such as `BCDF-GHJK`. Open the link, sign in to CookieNVR, check that the code matches and select Allow. Setup finishes by itself. There is no token to create or copy.
3. Home Assistant gets the permissions of the account that approved it. A regular user covers viewing, events and PTZ. Approve with an administrator for the recording and detection switches and the disk sensors. The connection is listed under Account, Connect other apps in CookieNVR and can be revoked there.
4. After a revoke, Home Assistant asks you to reconnect through the same approval. When the NVR gets a new IP address, discovery updates the entry.
5. If the approval page cannot be opened, create an API token under Account, Connect other apps and paste it into the advanced API token field.

Cameras added or removed on the NVR appear or disappear in Home Assistant without a reload.

### Entities, services and events

Each camera is a device, and the NVR is a device of its own. Entities are listed in the table above. Services `cookienvr.ptz_move`, `cookienvr.ptz_preset` and `cookienvr.export_clip` target the integration's camera entities. Every event start and end fires `cookienvr_event` with `camera`, `camera_id`, `device_id`, `event_id`, `state`, `score`, `labels`, `thumbnail_url` and `playback_url`. The thumbnail URL carries the API token, so keep it private.

### Reliability

The SSE stream reconnects with a 1 to 30 second backoff and refetches full state after each reconnect. A 60 second poll is the safety net. When the NVR is unreachable all entities become unavailable.
