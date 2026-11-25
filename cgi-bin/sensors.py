#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import html
import ipaddress
import json
import math
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs

try:
    import cgi
except ImportError:
    cgi = None

MAX_QUERY_BYTES = 1024
MAX_BODY_BYTES = 1024
MAX_VALUE_LENGTH = 50
MAX_POINTS = 50

IP_KEY = "IP"
DEFAULT_IP = "unknown"
RESERVED_KEYS = frozenset({"Time", IP_KEY})

LOG_FILE = Path(__file__).resolve().parent / "log.txt"


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def error(status: str, message: str) -> None:
    print(f"Status: {status}")
    print("Content-Type: text/plain; charset=UTF-8")
    print()
    print(message)
    sys.exit(0)


def normalize_ip(raw_ip: str) -> str:
    ip_str = raw_ip.strip()
    if not ip_str:
        return ""

    # IPv6ゾーンID (例: fe80::1%eth0) を除去して正規化
    ip_no_zone = ip_str.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(ip_no_zone)
    except ValueError:
        return ip_str[:MAX_VALUE_LENGTH]

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)

    return str(addr)


def get_client_ip() -> str:
    remote_addr = normalize_ip(os.environ.get("REMOTE_ADDR", ""))
    forwarded_for = os.environ.get("HTTP_X_FORWARDED_FOR", "")

    # ローカルのリバースプロキシ経由またはREMOTE_ADDR未設定時のみX-Forwarded-Forを参照
    if forwarded_for and (not remote_addr or remote_addr in {"127.0.0.1", "::1"}):
        first_forwarded = normalize_ip(forwarded_for.split(",", 1)[0])
        if first_forwarded:
            return first_forwarded

    return remote_addr or DEFAULT_IP


def extract_record_ip(record: dict[str, object]) -> str:
    raw = record.get(IP_KEY)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""

    if isinstance(raw, str):
        normalized = normalize_ip(raw)
        if normalized:
            return normalized

    return DEFAULT_IP


def parse_request_params(method: str, query: str) -> dict[str, list[str]]:
    if cgi is not None:
        cgi.maxlen = MAX_BODY_BYTES
        try:
            print(
                "Content-Type:",
                os.environ.get("CONTENT_TYPE", ""),
                file=sys.stderr,
            )
            form = cgi.FieldStorage()
        except ValueError:
            error("413 Payload Too Large", "Request body too large")

        raw_params: dict[str, list[object]] = {
            key: form.getlist(key) for key in form.keys()
        }
    else:
        raw_params = {}
        if query:
            for key, values in parse_qs(query, keep_blank_values=False).items():
                raw_params[key] = list(values)

        if method == "POST":
            content_length_str = os.environ.get("CONTENT_LENGTH", "").strip()
            if content_length_str:
                try:
                    content_length = int(content_length_str)
                except ValueError:
                    error("400 Bad Request", "Invalid Content-Length")
                if content_length < 0:
                    error("400 Bad Request", "Invalid Content-Length")
                if content_length > MAX_BODY_BYTES:
                    error("413 Payload Too Large", "Request body too large")
                body_bytes = sys.stdin.buffer.read(content_length)
            else:
                body_bytes = sys.stdin.buffer.read(MAX_BODY_BYTES + 1)
                if len(body_bytes) > MAX_BODY_BYTES:
                    error("413 Payload Too Large", "Request body too large")

            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                error("400 Bad Request", "Invalid UTF-8 body")

            for key, values in parse_qs(body_text, keep_blank_values=False).items():
                raw_params.setdefault(key, []).extend(values)

    params: dict[str, list[str]] = {}
    for key, values in raw_params.items():
        if len(key) > 50 or len(values) > 20:
            error("400 Bad Request", "Invalid parameters")

        if any(
            not isinstance(value, str) or len(value) > MAX_VALUE_LENGTH
            for value in values
        ):
            error("400 Bad Request", "Value too long")

        # 送信元IPはサーバー側で付与するため、クライアント送信値は無視する
        if key == IP_KEY:
            continue

        params[key] = [str(value) for value in values]

    return params


def load_graph_data() -> dict[str, dict[str, list[dict[str, float]]]]:
    # センサーキー × IPアドレス（系統）ごとに最新50件の数値データを保持
    data: dict[str, dict[str, deque[dict[str, float]]]] = defaultdict(
        lambda: defaultdict(lambda: deque(maxlen=MAX_POINTS))
    )

    if LOG_FILE.exists():
        with LOG_FILE.open("r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                for line in f:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue

                    if not isinstance(record, dict):
                        continue

                    # TimeはUNIX時刻（秒）
                    try:
                        timestamp = float(record["Time"][0])
                    except (KeyError, IndexError, ValueError, TypeError):
                        continue

                    if not math.isfinite(timestamp):
                        continue

                    # JavaScriptのDate用にミリ秒へ変換
                    timestamp_ms = timestamp * 1000.0
                    source_ip = extract_record_ip(record)

                    for key, values in record.items():
                        if key in RESERVED_KEYS:
                            continue

                        if not isinstance(values, list):
                            continue

                        for value in values:
                            try:
                                number = float(value)
                            except (ValueError, TypeError):
                                continue

                            if not math.isfinite(number):
                                continue

                            data[key][source_ip].append({
                                "x": timestamp_ms,
                                "y": number,
                            })
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # IPアドレスを安定ソート（unknownは末尾）し、各系統を時系列順に整列
    result: dict[str, dict[str, list[dict[str, float]]]] = {}
    for key, ip_map in data.items():
        result[key] = {
            ip: sorted(points, key=lambda point: point["x"])
            for ip, points in sorted(
                ip_map.items(),
                key=lambda item: (item[0] == DEFAULT_IP, item[0]),
            )
        }

    return result


def show_graphs() -> None:
    graph_data = load_graph_data()

    graph_json = json.dumps(
        graph_data,
        ensure_ascii=False,
        allow_nan=False,
    )

    # HTMLのscript要素に安全に埋め込む
    graph_json = (
        graph_json
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

    print("Content-Type: text/html; charset=UTF-8")
    print()

    print("""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1">

    <title>Sensor Log Charts</title>

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

    <style>
        body {
            font-family: sans-serif;
            margin: 20px;
            background: #f5f5f5;
            color: #222;
        }

        .page {
            max-width: 1200px;
            margin: auto;
        }

        h1 {
            margin-bottom: 16px;
        }

        .controls {
            background: white;
            padding: 16px 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            align-items: center;
            justify-content: space-between;
        }

        .control-group {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }

        .control-label {
            font-weight: bold;
            font-size: 14px;
            margin-right: 4px;
        }

        .ip-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border: 1px solid #d0d7de;
            border-radius: 999px;
            background: #fafafa;
            font-size: 13px;
            cursor: pointer;
            user-select: none;
        }

        .color-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }

        .mode-btn,
        .action-btn {
            padding: 6px 12px;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            background: white;
            cursor: pointer;
            font-size: 13px;
        }

        .mode-btn.active {
            background: #0969da;
            border-color: #0969da;
            color: white;
        }

        .ip-section {
            margin-bottom: 28px;
        }

        .ip-section-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 0 0 12px 0;
            font-size: 20px;
        }

        .chart-box {
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
        }

        .chart-box h2 {
            margin-top: 0;
            font-size: 18px;
        }

        .chart-container {
            position: relative;
            height: 300px;
        }

        .message {
            padding: 20px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
        }
    </style>
</head>

<body>
    <div class="page">
        <h1>センサーログ（IPアドレス別系統）</h1>
        <div id="controls" class="controls" hidden></div>
        <div id="charts"></div>
    </div>

    <script id="graph-data" type="application/json">""")

    # Pythonで生成したJSONをHTMLに埋め込む
    print(graph_json)

    print("""</script>

    <script>
        const data = JSON.parse(
            document.getElementById("graph-data").textContent
        );

        const controlsEl = document.getElementById("controls");
        const container = document.getElementById("charts");
        const activeCharts = [];

        const PALETTE = [
            "rgb(54, 162, 235)",
            "rgb(255, 99, 132)",
            "rgb(75, 192, 192)",
            "rgb(255, 159, 64)",
            "rgb(153, 102, 255)",
            "rgb(46, 184, 92)",
            "rgb(235, 87, 166)",
            "rgb(230, 176, 0)",
            "rgb(63, 81, 181)",
            "rgb(0, 150, 136)"
        ];

        function withAlpha(color, alpha) {
            if (color.startsWith("rgb(")) {
                return color.replace("rgb(", "rgba(").replace(")", `, ${alpha})`);
            }
            if (color.startsWith("hsl(")) {
                return color.replace("hsl(", "hsla(").replace(")", `, ${alpha})`);
            }
            return color;
        }

        function collectIps(graphData) {
            const ipSet = new Set();
            for (const seriesByIp of Object.values(graphData)) {
                for (const ip of Object.keys(seriesByIp)) {
                    ipSet.add(ip);
                }
            }
            return Array.from(ipSet).sort((a, b) => {
                if (a === "unknown" && b !== "unknown") return 1;
                if (b === "unknown" && a !== "unknown") return -1;
                return a.localeCompare(b);
            });
        }

        const allIps = collectIps(data);
        const ipColors = new Map();
        allIps.forEach((ip, index) => {
            if (ip === "unknown") {
                ipColors.set(ip, "rgb(120, 128, 138)");
            } else if (index < PALETTE.length) {
                ipColors.set(ip, PALETTE[index]);
            } else {
                const hue = Math.round((index * 137.508) % 360);
                ipColors.set(ip, `hsl(${hue}, 68%, 48%)`);
            }
        });

        const selectedIps = new Set(allIps);
        let viewMode = "by-sensor"; // "by-sensor" | "by-ip"

        // 日本時間で日時を表示
        function formatJST(timestamp, withYear = false) {
            const options = {
                timeZone: "Asia/Tokyo",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            };

            if (withYear) {
                options.year = "numeric";
            }

            return new Date(timestamp).toLocaleString("ja-JP", options);
        }

        function createLineChart(canvas, datasets, showLegend) {
            const chart = new Chart(canvas, {
                type: "line",
                data: { datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    parsing: false,
                    plugins: {
                        legend: {
                            display: showLegend
                        },
                        tooltip: {
                            callbacks: {
                                title: function(items) {
                                    if (!items.length) {
                                        return "";
                                    }
                                    return formatJST(items[0].parsed.x, true);
                                },
                                label: function(context) {
                                    return (
                                        context.dataset.label +
                                        ": " +
                                        context.parsed.y
                                    );
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            type: "linear",
                            title: {
                                display: true,
                                text: "日時（JST）"
                            },
                            ticks: {
                                maxTicksLimit: 6,
                                callback: function(value) {
                                    return formatJST(value);
                                }
                            }
                        },
                        y: {
                            title: {
                                display: true,
                                text: "値"
                            }
                        }
                    }
                }
            });
            activeCharts.push(chart);
        }

        function buildDataset(ip, points) {
            const color = ipColors.get(ip) || "rgb(54, 162, 235)";
            return {
                label: ip,
                data: points,
                borderColor: color,
                backgroundColor: withAlpha(color, 0.12),
                borderWidth: 2,
                tension: 0.2,
                pointRadius: 3,
                pointHoverRadius: 6,
                fill: false
            };
        }

        function clearCharts() {
            while (activeCharts.length > 0) {
                activeCharts.pop().destroy();
            }
            container.textContent = "";
        }

        function renderEmpty(text) {
            const message = document.createElement("div");
            message.className = "message";
            message.textContent = text;
            container.appendChild(message);
        }

        function renderControls() {
            if (allIps.length === 0) {
                controlsEl.hidden = true;
                return;
            }

            controlsEl.hidden = false;
            controlsEl.textContent = "";

            const ipGroup = document.createElement("div");
            ipGroup.className = "control-group";

            const ipLabel = document.createElement("span");
            ipLabel.className = "control-label";
            ipLabel.textContent = "系統（IP）:";
            ipGroup.appendChild(ipLabel);

            for (const ip of allIps) {
                const chip = document.createElement("label");
                chip.className = "ip-chip";

                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = selectedIps.has(ip);
                checkbox.addEventListener("change", () => {
                    if (checkbox.checked) {
                        selectedIps.add(ip);
                    } else {
                        selectedIps.delete(ip);
                    }
                    renderCharts();
                });

                const dot = document.createElement("span");
                dot.className = "color-dot";
                dot.style.backgroundColor = ipColors.get(ip);

                const text = document.createElement("span");
                text.textContent = ip;

                chip.appendChild(checkbox);
                chip.appendChild(dot);
                chip.appendChild(text);
                ipGroup.appendChild(chip);
            }

            if (allIps.length > 1) {
                const selectAllBtn = document.createElement("button");
                selectAllBtn.type = "button";
                selectAllBtn.className = "action-btn";
                selectAllBtn.textContent = "すべて選択";
                selectAllBtn.addEventListener("click", () => {
                    allIps.forEach((ip) => selectedIps.add(ip));
                    renderControls();
                    renderCharts();
                });
                ipGroup.appendChild(selectAllBtn);
            }

            const modeGroup = document.createElement("div");
            modeGroup.className = "control-group";

            const modeLabel = document.createElement("span");
            modeLabel.className = "control-label";
            modeLabel.textContent = "表示:";
            modeGroup.appendChild(modeLabel);

            const modes = [
                { id: "by-sensor", label: "センサー別（系統比較）" },
                { id: "by-ip", label: "IP別（系統ごとに分割）" }
            ];

            for (const mode of modes) {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className =
                    "mode-btn" + (viewMode === mode.id ? " active" : "");
                btn.textContent = mode.label;
                btn.addEventListener("click", () => {
                    if (viewMode !== mode.id) {
                        viewMode = mode.id;
                        renderControls();
                        renderCharts();
                    }
                });
                modeGroup.appendChild(btn);
            }

            controlsEl.appendChild(ipGroup);
            controlsEl.appendChild(modeGroup);
        }

        function renderCharts() {
            clearCharts();

            const keys = Object.keys(data);
            if (keys.length === 0) {
                renderEmpty("表示できる数値データがありません。");
                return;
            }

            const visibleIps = allIps.filter((ip) => selectedIps.has(ip));
            if (visibleIps.length === 0) {
                renderEmpty("表示するIPアドレス（系統）を選択してください。");
                return;
            }

            if (viewMode === "by-sensor") {
                for (const key of keys) {
                    const seriesByIp = data[key];
                    const datasets = visibleIps
                        .filter((ip) => Array.isArray(seriesByIp[ip]) && seriesByIp[ip].length > 0)
                        .map((ip) => buildDataset(ip, seriesByIp[ip]));

                    if (datasets.length === 0) {
                        continue;
                    }

                    const box = document.createElement("div");
                    box.className = "chart-box";

                    const heading = document.createElement("h2");
                    heading.textContent = key;
                    box.appendChild(heading);

                    const chartContainer = document.createElement("div");
                    chartContainer.className = "chart-container";

                    const canvas = document.createElement("canvas");
                    chartContainer.appendChild(canvas);
                    box.appendChild(chartContainer);
                    container.appendChild(box);

                    createLineChart(canvas, datasets, true);
                }
            } else {
                for (const ip of visibleIps) {
                    const ipKeys = keys.filter(
                        (key) => Array.isArray(data[key][ip]) && data[key][ip].length > 0
                    );

                    if (ipKeys.length === 0) {
                        continue;
                    }

                    const section = document.createElement("section");
                    section.className = "ip-section";

                    const sectionHeader = document.createElement("h2");
                    sectionHeader.className = "ip-section-header";

                    const dot = document.createElement("span");
                    dot.className = "color-dot";
                    dot.style.backgroundColor = ipColors.get(ip);

                    const label = document.createElement("span");
                    label.textContent = `系統: ${ip}`;

                    sectionHeader.appendChild(dot);
                    sectionHeader.appendChild(label);
                    section.appendChild(sectionHeader);

                    for (const key of ipKeys) {
                        const box = document.createElement("div");
                        box.className = "chart-box";

                        const heading = document.createElement("h2");
                        heading.textContent = `${key} (${ip})`;
                        box.appendChild(heading);

                        const chartContainer = document.createElement("div");
                        chartContainer.className = "chart-container";

                        const canvas = document.createElement("canvas");
                        chartContainer.appendChild(canvas);
                        box.appendChild(chartContainer);
                        section.appendChild(box);

                        createLineChart(
                            canvas,
                            [buildDataset(ip, data[key][ip])],
                            false
                        );
                    }

                    container.appendChild(section);
                }
            }
        }

        renderControls();
        renderCharts();
    </script>
</body>
</html>
""")


def main() -> None:
    configure_stdout()

    query = os.environ.get("QUERY_STRING", "")
    method = os.environ.get("REQUEST_METHOD", "GET").upper()

    # GETのクエリ文字列を制限
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        error("414 URI Too Long", "Query too long")

    # クエリのないGETは認証なしでグラフを表示
    if method == "GET" and not query:
        show_graphs()
        sys.exit(0)

    params = parse_request_params(method, query)
    if not params:
        error("400 Bad Request", "No parameters provided")

    client_ip = get_client_ip()
    record: dict[str, object] = {
        **params,
        IP_KEY: client_ip,
    }

    # JSONに変換
    res = json.dumps(record, ensure_ascii=False)

    # ログに追記（同時アクセス時の行混在を防ぐためロック）
    with LOG_FILE.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(res + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # HTML出力
    safe_res = html.escape(res)

    print("Content-Type: text/html; charset=UTF-8")
    print()

    print(f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>Result</title>
</head>
<body>
    <pre>{safe_res}</pre>
</body>
</html>
""")


if __name__ == "__main__":
    main()
