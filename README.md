# flight_tracer

---

## 全体フロー

```
[ユーザーが地図をズーム]
        ↓
[Leaflet] zoomend イベント発火
        ↓
[JS] onMapMoveEnd() → デバウンス(800ms)
        ↓
[JS] getCurrentBoundsParam() → "北,西,南,東"
        ↓
[JS] fetch('/api/flights?bounds=北,西,南,東')
        ↓
[Flask] /api/flights → parse_bounds() → 面積チェック
        ↓
[Flask] fr_api.get_bounds() → fr_api.get_flights(bounds=...)
        ↓
[FlightRadar24API] 指定範囲のフライトのみ返却
        ↓
[Flask] serialize_flight_basic() → JSON
        ↓
[JS] allFlights = レスポンス
        ↓
[JS] filterFlights() → updateMap() → マーカー更新
```

---

## 1. フロントエンド側（`index.html`）

### ① イベントの発火

```javascript
map.on('moveend', onMapMoveEnd);
map.on('zoomend', onMapMoveEnd);
```

地図をズーム（マウスホイール・ピンチイン・ズームボタン）すると、Leaflet が処理を完了した瞬間に `zoomend` イベントが1回発火する。パン（ドラッグ移動）の場合は `moveend` が発火する。

### ② デバウンス処理

```javascript
let moveDebounceTimer = null;

function onMapMoveEnd() {
    clearTimeout(moveDebounceTimer);
    moveDebounceTimer = setTimeout(() => {
        fetchFlights();
    }, 800);
}
```

ズームスライダーを連続操作すると `zoomend` が何度も発火しうる。そのたびにAPIを叩くのは無駄なので、最後の操作から800ms経ってから初めて `fetchFlights()` を実行する。途中で新しい操作が入るとタイマーはリセットされる。

### ③ 表示範囲（bounds）の取得

```javascript
function getCurrentBoundsParam() {
    const b = map.getBounds();
    const tl_y = b.getNorth();   // 北端緯度
    const tl_x = b.getWest();    // 西端経度
    const br_y = b.getSouth();   // 南端緯度
    const br_x = b.getEast();    // 東端経度
    return `${tl_y},${tl_x},${br_y},${br_x}`;
}
```

`map.getBounds()` は「今画面に映っている領域」を `LatLngBounds` として返す。これを文字列 `"北,西,南,東"` に変換する。

### ④ APIリクエスト

```javascript
const boundsParam = getCurrentBoundsParam();
const response = await fetch(`${API_BASE}/flights?bounds=${encodeURIComponent(boundsParam)}`);
```

`encodeURIComponent` によりカンマは `%2C` にエンコードされる。

---

## 2. バックエンド側（`app.py`）

### ① クエリパラメータの受け取り

```python
bounds_str = request.args.get('bounds', DEFAULT_BOUNDS)
```

パラメータがなければデフォルト値（日本周辺）を使う。

### ② パースと検証

```python
coords = [float(x) for x in bounds_str.split(',')]
```

4つに分割できない場合は `ValueError` → 400エラー。

### ③ 面積ガード（広域取得防止）

```python
area = abs(tl_y - br_y) * abs(tl_x - br_x)
if area > MAX_BOUNDS_AREA:
    return jsonify({'error': 'bounds_too_large', ...}), 400
```

緯度差 × 経度差で表示範囲の広さをチェックする。修正前は `MAX_BOUNDS_AREA = 40.0` に対し `DEFAULT_BOUNDS` の面積が528だったため、初回表示から常にこのガードに引っかかっていた。`600.0` への修正でこの問題は解消される。

### ④ FlightRadar24APIへの範囲指定取得

```python
bounds_obj = fr_api.get_bounds({...})
flights = fr_api.get_flights(bounds=bounds_obj)
```

サーバー側でも指定範囲だけを FlightRadar24 から取得する（全世界取得はしない）。

### ⑤ シリアライズと返却

```python
return jsonify([serialize_flight_basic(f) for f in flights])
```

IATA/ICAOコードなど軽量な基本情報のみを返す。

---

## 3. フロントエンド側（レスポンス処理）

```javascript
const flights = await response.json();
allFlights = flights;
filterFlights();
checkWatchList(flights);
```

- 検索ボックスに入力があれば `allFlights` から絞り込む
- `updateMap()` でマーカーを更新
  - 範囲外になったフライト → マーカー削除
  - 新しく範囲内に入ったフライト → 新規マーカー作成
  - 既存フライト → 位置と向きを更新
- 取得結果をウォッチリストと照合し、状態変化があれば通知

---

## 4. ポップアップ詳細取得の流れ（ズームとは別のオンデマンド取得）

```
[マーカークリック]
        ↓
[Leaflet] popupopen イベント発火
        ↓
[JS] loadFlightDetails(flightId, marker)
        ↓
[JS] fetch('/api/flight/xxx?bounds=...')
        ↓
[Flask] 該当bounds内からflight_idを検索
        ↓
[Flask] fr_api.get_flight_details() → set_flight_details()
        ↓
[Flask] serialize_flight_detailed() → フルネーム込みJSON
        ↓
[JS] marker.setPopupContent() → ポップアップを詳細版に差し替え
```

全マーカーは軽量な基本情報で描画し、ユーザーが興味を持った機体だけ詳細情報を追加取得する設計。

---

## まとめ

| フェーズ | 処理内容 |
|---|---|
| ズーム操作 | `zoomend` → デバウンス800ms → `getCurrentBoundsParam()` |
| APIリクエスト | `GET /api/flights?bounds=北,西,南,東` |
| バックエンド検証 | パース → 面積チェック(`MAX_BOUNDS_AREA`) → 範囲指定取得 |
| マーカー更新 | 範囲外は削除、新規は作成、既存は位置更新 |
| オンデマンド詳細 | ポップアップopen時に `/api/flight/<id>` でフルネーム取得 |