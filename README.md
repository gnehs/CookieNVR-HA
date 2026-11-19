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
4. 撤銷後 HA 會要求重新連接，走同一個核准流程。NVR 換了 IP 或 port 後 HA 會從廣播自動更新網址；更新前會先要新位址證明它就是發這組 Token 的那台 NVR（Token 本身不會送出），別的裝置冒充同一台 NVR 拿不到 Token。
5. 打不開核准頁時（例如 HA 用的網址從你的瀏覽器連不到），可以在 CookieNVR 的「帳號 → 連接其他 App」建立 API Token，貼到設定表單的「API Token（進階）」欄位。

NVR 新增或刪除攝影機後，HA 自動同步，不需要重啟整合。

### 實體

每台攝影機是一個裝置，NVR 本身另有一個裝置。

| 實體 | 說明 |
| --- | --- |
| `camera.<名稱>` | 快照取自 NVR；串流來源為 NVR 的 RTSP 轉發（main） |
| `binary_sensor.<名稱>_motion` | 動作與事件進行中 |
| `binary_sensor.<名稱>_online` | 攝影機上線 |
| `binary_sensor.<名稱>_<類別>` | 物件辨識勾選的每個類別一個（人、汽車、狗…），進行中的事件含該類別時開啟 |
| `sensor.<名稱>_<感應器>` | 狀態辨識（鐵捲門：關／移動中／開），每個狀態感應器一個，見下方 |
| `switch.<名稱>_recording`、`_detection` | 錄影與偵測開關（需管理員 Token） |
| `select.<名稱>_ptz_preset`、`button.<名稱>_ptz_*` | PTZ 預置點與上下左右微調（有 PTZ 才有） |
| `image.<名稱>_last_event`、`sensor.<名稱>_last_event` | 最近事件的縮圖與時間 |
| NVR：磁碟容量、可用、用量、錄影總量、正常 worker 數 | 需管理員 Token |

### 物件類別感應器

在 CookieNVR 的攝影機設定勾選要辨識的類別後，每個類別會成為一個獨立的感應器，例如攝影機「前門」的 `binary_sensor.qian_men_person`（顯示為「前門 人」）。勾選或取消後感應器會自動增減，不需要重新載入整合；同一台攝影機的同一個類別永遠是同一個實體，改名或取消後再勾回來，自動化都不會斷。

- 類別在事件進行中被確認的當下就開啟，事件結束時關閉。斷線重連後依 NVR 的完整狀態校正。
- 裝置類別是 occupancy（「偵測到／未偵測到」），和代表畫面變動的 motion 感應器區分開來。
- 屬性：`last_detected`（最近一次偵測到的時間）、`event_id`（對應的事件）。
- 常見類別有中英文名稱；自訂模型的類別顯示原始名稱（例如 `garbage_bin` 顯示為「Garbage bin」）。
- 內建模型以 COCO 資料集訓練，沒有「包裹」類別；要偵測包裹得在 CookieNVR 換用自訂模型，類別出現後這裡就會有對應的感應器。

```yaml
automation:
  - alias: 前門有人時推播
    triggers:
      - trigger: state
        entity_id: binary_sensor.qian_men_person
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: 前門有人
```

### 狀態辨識

在 CookieNVR 為攝影機畫面的某個區域訓練「狀態感應器」（例如「鐵捲門」：關、移動中、開）後，每個感應器會成為所屬攝影機底下的一個 `sensor`，例如 `sensor.qian_men_tie_juan_men`。新增、改名、刪除感應器或調整類別都會自動同步，不需要重新載入整合；實體以感應器的固定編號識別，改名不會讓自動化失效。

- 狀態是 enum 感應器，選項就是 CookieNVR 設定的類別（依設定順序）。還沒有確定的狀態時是 `unknown`：剛啟動、尚未通過連續驗證、尚未訓練、已暫停，或攝影機沒有畫面（CookieNVR 在這些情況會自己把狀態改回未知）。只有 NVR 連不上或感應器、攝影機被刪除時才會是 `unavailable`。
- 屬性：`score`（切換時的信心 0 到 1）、`since`（切換到目前狀態的時間）、`candidate`（正在驗證、尚未切換的候選狀態）、`problem`（目前沒在辨識的原因：`disabled`、`untrained`、`stale-model`、`camera-off`、`paused`、`worker`，正常時為空）。
- 每次切換也會觸發事件 `cookienvr_state_change`，內容有 `camera`、`camera_id`、`device_id`、`sensor`、`sensor_id`、`from`（該感應器第一筆為空）、`to`、`score`、`changed_at` 與 `thumbnail_url`（切換時那塊區域的畫面，網址帶 API Token，請保密）。想在通知附上圖片、或只關心特定的 `from`／`to` 時用事件；一般自動化直接用實體的狀態觸發即可。
- 舊版 CookieNVR（沒有狀態辨識）照常運作，只是不會有這些實體。

```yaml
automation:
  - alias: 鐵捲門開了就通知
    triggers:
      - trigger: state
        entity_id: sensor.qian_men_tie_juan_men
        to: "開"
    actions:
      - action: notify.notify
        data:
          message: 鐵捲門開了
```

### 服務

- `cookienvr.ptz_move`：`pan`、`tilt`、`zoom`（-1 到 1）、`mode`（`continuous` 或 `relative`）、`duration`、`speed`。
- `cookienvr.ptz_preset`：`action`（`goto`、`save`、`delete`）、`preset`、`name`。儲存與刪除需管理員 Token。
- `cookienvr.export_clip`：`start`，加上 `end` 或 `duration`（擇一），可選 `filename`。影片寫到 HA 的 media 目錄下的 `cookienvr/`，並回傳 `path` 與 `media_content_id`。

三個服務的對象都是這個整合的 `camera` 實體。

### 事件

每筆事件開始、結束，以及進行中確認了新的物件類別時，都會發出 `cookienvr_event`：

```yaml
camera: 前門          # 攝影機名稱
camera_id: abc123
device_id: 0f1e...    # HA 裝置 ID
event_id: 42
state: update         # start、update（確認了新類別）或 end
score: 0.42
labels: [person, car] # 目前為止確認的所有類別，沒有物件辨識時為空
new_labels: [car]     # 這一次新確認的類別；start 時等於 labels，end 時為空
thumbnail_url: http://nvr:8765/api/events/42/thumb.jpg?token=cnvr_...
playback_url: http://nvr:8765/#/recordings?camera=abc123
```

想做「有人才通知」時，觸發條件用 `new_labels` 含 `person`，不論類別是在事件開始時還是進行中才確認都只會觸發一次。

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
4. After a revoke, Home Assistant asks you to reconnect through the same approval. When the NVR gets a new IP address or port, discovery updates the entry, but only after the new address proves it issued the stored token (without the token being sent), so a device announcing itself as your NVR gets nothing.
5. If the approval page cannot be opened, create an API token under Account, Connect other apps and paste it into the advanced API token field.

Cameras added or removed on the NVR appear or disappear in Home Assistant without a reload.

### Entities, services and events

Each camera is a device, and the NVR is a device of its own. Entities are listed in the table above. Services `cookienvr.ptz_move`, `cookienvr.ptz_preset` and `cookienvr.export_clip` target the integration's camera entities. Every object class ticked in CookieNVR becomes its own occupancy binary sensor that turns on as soon as an event in progress confirms the class and off when the event ends; sensors follow the ticked classes without a reload, keep their entity across renames and re-ticking, and carry `last_detected` and `event_id`. The bundled model is trained on COCO, which has no package class, so package sensors need a custom model. Every event start, every newly confirmed class (`state: update`) and every end fires `cookienvr_event` with `camera`, `camera_id`, `device_id`, `event_id`, `state`, `score`, `labels`, `new_labels`, `thumbnail_url` and `playback_url`; trigger on `new_labels` containing `person` to be told once per event. The thumbnail URL carries the API token, so keep it private.

Each state sensor trained in CookieNVR (for example a shutter that is closed, moving or open) becomes an enum `sensor` on its camera's device, such as `sensor.front_door_shutter`, with the classes you configured as its options. Sensors, renames and class changes follow without a reload, and the entity is keyed by the sensor's fixed id so a rename keeps your automations. The state is `unknown` while CookieNVR has no verified state (just started, not yet confirmed, untrained, paused or the camera has no picture; CookieNVR resets the state itself in these cases), and `unavailable` only when the NVR cannot be reached or the sensor or camera is gone. Attributes are `score`, `since`, `candidate` (the state being verified) and `problem` (`disabled`, `untrained`, `stale-model`, `camera-off`, `paused` or `worker`, empty when all is well). Every change also fires `cookienvr_state_change` with `camera`, `camera_id`, `device_id`, `sensor`, `sensor_id`, `from` (empty for a sensor's first reading), `to`, `score`, `changed_at` and `thumbnail_url` (the picture of the region at that moment, carrying the API token); use it to attach the picture to a notification, and trigger on the entity's state otherwise. A CookieNVR without state classification keeps working and simply has no such sensors.

### Reliability

The SSE stream reconnects with a 1 to 30 second backoff and refetches full state after each reconnect. A 60 second poll is the safety net. When the NVR is unreachable all entities become unavailable.
