# Android アプリ向け `GraphSend` コマンド実装仕様書

他のエージェントへ Android アプリ側の実装を依頼するための仕様書です。  
Raspberry Pi（`r3pi-wars`）側はすでに実装済みであり、Android アプリは **既存の UDP 通信経路を使って開始・停止コマンドを送受信する UI / 通信ロジック** のみを実装します。

---

## 1. 概要・役割分担

- **Android アプリの役割**
  - ユーザー操作（トグルスイッチまたは開始／停止ボタン）に応じて、Raspberry Pi へ UDP コマンド `GraphSend=true` / `GraphSend=false` を送信する。
  - Raspberry Pi から返却される UDP 応答（`Status=GraphSendStarted` / `Status=GraphSendStopped` / `Error=...`）を解析し、画面上の状態表示・エラー表示に反映する。
  - **Android 側から CGI サーバーへ直接 HTTP 送信する必要はない**（送信先 IP や送信間隔は Raspberry Pi 側の `.env` で管理されている）。
- **Raspberry Pi 側の動作（実装済み）**
  - `GraphSend=true` を受信すると、Raspberry Pi 上のバックグラウンドスレッドで Sense HAT のセンサー値（温度・湿度・気圧・加速度・必要に応じ明るさ）を `.env` に定義された CGI サーバー（`cgi/sensors.py`）へ定期 POST 送信する。
  - `GraphSend=false` を受信するとバックグラウンド送信を停止する。
  - モーター停止コマンド（`Stop`）や通信途絶ウォッチドッグ（0.25 秒）ではグラフ送信は停止しない。

---

## 2. 通信プロトコル仕様

既存のロボットカー制御と同じ UDP 通信を使用します。

| 項目 | 仕様 |
| --- | --- |
| プロトコル | UDP (IPv4) |
| 宛先ポート | `50000`（既存ロボットカー接続先と同一） |
| 文字コード | UTF-8 |
| 送信フォーマット | `Key=Value`（複数コマンド連結時は `,` 区切り） |
| 受信フォーマット | `Key=Value`（複数項目時は `:` 区切り） |

### 2.1 送信コマンド（Android → Raspberry Pi）

既存の `BoundaryDetection` コマンド（`BoundaryDetection=true` / `BoundaryDetection=false`）と同じ命名・値形式です。

| 操作 | 推奨送信文字列 | Raspberry Pi 側で受理される別名（大文字小文字無視） |
| --- | --- | --- |
| グラフ送信を開始 | `GraphSend=true` | `1`, `start`, `on` |
| グラフ送信を停止 | `GraphSend=false` | `0`, `stop`, `off` |

- キー名 `GraphSend` は **大文字・小文字を区別** するため、正確に `GraphSend` とすること。
- 既存の `BoundaryDetection` と統一するため、送信値は **`GraphSend=true` / `GraphSend=false`** を推奨する。
- 不正な値（上記以外）を送った場合、Raspberry Pi は `Invalid Command` として破棄し、UDP 応答を返さない。

### 2.2 応答パケット（Raspberry Pi → Android）

Raspberry Pi はコマンド送信元の IP・ポートに対して、以下の UTF-8 文字列を UDP で返却します。

| 状況 | 応答文字列 | 説明 |
| --- | --- | --- |
| 送信開始成功（または既に開始済み） | `Status=GraphSendStarted` | バックグラウンド送信が動作中。冪等（重複起動しない）。 |
| 送信停止成功（または既に停止済み） | `Status=GraphSendStopped` | バックグラウンド送信が停止済み。冪等。 |
| 開始時の設定・初期化エラー | `Error=<エラー詳細>` | 例: `.env` に `GRAPH_SERVER_IP` が未設定／書式不正など。エラー文字列内の `:`・`\r`・`\n` は半角スペースに置換済み。 |

> **注意:** `:` 区切りで複数要素が含まれるパケット（例: `Status=Stopped:Error=...`）を扱う既存パーサーがある場合は、同様に `:` で分割し `Key=Value` として `Status` / `Error` を抽出すること。

---

## 3. Android アプリ実装要件

### 3.1 UI 要件

1. **操作コントロールの追加**
   - 既存画面（`BoundaryDetection` トグルやセンサー取得ボタンが配置されている操作画面）に、グラフ送信用の **トグルスイッチ**（または開始／停止ボタン）を追加する。
   - ラベル例: 「グラフ送信」「センサー送信」など、アプリ内の既存表記（日本語／英語）に合わせる。
2. **状態表示**
   - 現在の状態（停止中 / 送信中、必要なら応答待ち）がユーザーに視覚的に分かるようにする。
   - 初期状態は **停止中（OFF）** とする。
3. **エラー表示**
   - `Error=...` を受信した場合、既存のエラー表示方法（Toast / Snackbar / ステータス欄など）でユーザーに通知する。
   - `GraphSend=true` 送信後に `Error=...` を受け取った場合は、UI を **停止中（OFF）** へ戻す。

### 3.2 状態管理・通信処理要件

1. **コマンド送信**
   - 既存の UDP 送信処理（`BoundaryDetection` や `Sensor` と同じ送信口）を利用し、メインスレッドをブロックせずにバックグラウンドスレッド／コルーチンで送信する。
   - ON 操作時: `GraphSend=true` を送信。
   - OFF 操作時: `GraphSend=false` を送信。
2. **応答ハンドリング**
   - 既存の UDP 受信処理に以下の分岐を追加する。
     - `Status == "GraphSendStarted"` → グラフ送信状態を **ON** に確定。
     - `Status == "GraphSendStopped"` → グラフ送信状態を **OFF** に確定。
     - `Error` 受信時 → グラフ送信開始要求中であれば状態を **OFF** に戻し、エラー内容を表示。
3. **既存機能との非干渉（重要）**
   - `Status=Stopped` は **モーター停止（走行停止）** を意味する応答であり、グラフ送信の停止ではない。**`Status=Stopped` を受信しても `GraphSend` の UI 状態を OFF にしないこと。**
   - 走行コマンド（`Forward` 等）や `BoundaryDetection` と並行して `GraphSend` が動作しても互いに干渉しないこと。
4. **UDP パケットロス・再送への配慮**
   - UDP のため応答が返らない可能性がある。トグル操作後、一定時間（例: 2〜3 秒）応答が無い場合は応答待ち状態を解除して直前の確定状態に戻す、または再操作可能な状態に保つこと（操作不能なロック状態にしない）。
   - `GraphSend=true` / `GraphSend=false` は Raspberry Pi 側で冪等なため、ユーザーが再度押下しても安全に動作する。

---

## 4. 実装時の調査・作業手順（担当エージェント向け）

1. Android プロジェクト内で `BoundaryDetection`・`Status=Stopped`・`Sensor=`・ポート `50000` を検索し、既存のコマンド生成・UDP 送受信・状態管理（ViewModel 等）・UI 定義の実装パターンを把握する。
2. 既存パターンに合わせて以下を追加・修正する。
   - コマンド定義（enum / 定数 / パケット生成関数）に `GraphSend` を追加。
   - 受信パーサー／状態更新処理に `GraphSendStarted` / `GraphSendStopped`（および必要に応じ `Error` 時の状態復帰）を追加。
   - 操作画面の UI（Compose または XML レイアウト）に `GraphSend` 制御 UI を追加し、ViewModel / Activity / Fragment と接続。
3. 既存のユニットテスト構成に合わせてテストを追加する。

---

## 5. 受け入れ条件・テスト観点

- [ ] **コマンド生成テスト**: ON 時に `"GraphSend=true"`、OFF 時に `"GraphSend=false"` が UTF-8 で生成されること。
- [ ] **応答パーステスト**:
  - `"Status=GraphSendStarted"` 受信でグラフ送信状態が ON になること。
  - `"Status=GraphSendStopped"` 受信でグラフ送信状態が OFF になること。
  - `"Status=Stopped"` 受信ではグラフ送信状態（ON）が維持されること。
  - `"Error=..."` 受信でエラーが表示され、開始要求中だった場合は OFF に戻ること。
- [ ] **実機結合確認**:
  1. Raspberry Pi 側で `.env` に `GRAPH_SERVER_IP` を設定し `python3 src/main.py` を起動する。
  2. Android アプリでグラフ送信を ON にし、`Status=GraphSendStarted` を受信して UI が ON になること、および CGI のグラフページにデータが増え始めることを確認する。
  3. 走行操作・`Stop` を行ってもグラフ送信が継続することを確認する。
  4. Android アプリでグラフ送信を OFF にし、`Status=GraphSendStopped` を受信して UI が OFF になり、CGI への送信が止まることを確認する。
  5. Raspberry Pi 側で `.env` の `GRAPH_SERVER_IP` を空にした状態で ON にし、エラーメッセージが表示されて UI が OFF に戻ることを確認する。
