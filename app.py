import os
import json
from flask import Flask, jsonify, request
from flask_cors import CORS
from FlightRadarAPI import FlightRadar24API
from functools import wraps

# app.pyのあるディレクトリを基準にする(実行時のカレントディレクトリに依存させない)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# index.htmlの置き場所として考えられる候補を順に探す
CANDIDATE_DIRS = [
    SCRIPT_DIR,                                          # app.pyと同じディレクトリ
    os.path.join(SCRIPT_DIR, '..', 'frontend'),          # backend/app.py から見た ../frontend
    os.path.join(SCRIPT_DIR, 'frontend'),                # app.py から見た ./frontend
]


def find_static_dir():
    """index.htmlが実在するディレクトリを候補から探す。見つからなければNone"""
    for d in CANDIDATE_DIRS:
        d_abs = os.path.abspath(d)
        if os.path.isdir(d_abs) and os.path.exists(os.path.join(d_abs, 'index.html')):
            return d_abs
    return None


STATIC_DIR = find_static_dir()

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
CORS(app)
fr_api = FlightRadar24API()


@app.route('/')
def index():
    """index.htmlをルートで配信"""
    if STATIC_DIR is None:
        tried = "\n".join(f"- {os.path.abspath(d)}" for d in CANDIDATE_DIRS)
        return (
            "<pre>index.html が見つかりません。以下の場所を探しましたが存在しませんでした:\n"
            f"{tried}\n\n"
            "app.py と index.html を同じディレクトリに置くか、\n"
            "backend/app.py + frontend/index.html の構成にしてください。</pre>",
            500,
        )
    return app.send_static_file('index.html')


# デフォルトbounds: 北, 西, 南, 東(日本周辺)
DEFAULT_BOUNDS = "46.0,122.0,24.0,146.0"

# 広域取得防止用の面積上限(緯度差 × 経度差の目安値)
# DEFAULT_BOUNDS(日本周辺)の面積 = |46-24| × |146-122| = 528 を下回らない値にする
MAX_BOUNDS_AREA = 600.0


def handle_fr_errors(f):
    """FlightRadarAPI呼び出しの例外を共通処理するデコレータ"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            app.logger.error(f"FlightRadarAPI error in {f.__name__}: {e}")
            return jsonify({'error': 'flightradar_unavailable', 'detail': str(e)}), 503
    return wrapper


def parse_bounds(bounds_str):
    """'tl_y,tl_x,br_y,br_x' 文字列をパースしてFlightRadar24APIのbounds形式に変換"""
    coords = [float(x) for x in bounds_str.split(',')]
    if len(coords) != 4:
        raise ValueError('bounds must have 4 comma-separated values: tl_y,tl_x,br_y,br_x')
    tl_y, tl_x, br_y, br_x = coords
    return tl_y, tl_x, br_y, br_x


def validate_bounds_area(tl_y, tl_x, br_y, br_x):
    """面積が大きすぎる場合はエラーレスポンス(Flaskの(body, status)タプル)を返す。問題なければNone"""
    area = abs(tl_y - br_y) * abs(tl_x - br_x)
    if area > MAX_BOUNDS_AREA:
        return jsonify({
            'error': 'bounds_too_large',
            'detail': f'表示範囲が広すぎます(area={area:.1f})。ズームインしてください'
        }), 400
    return None


def serialize_flight_basic(flight):
    """get_flights()直後の基本属性のみ(IATA/ICAOコードは含まれる。フルネームは未取得)"""
    return {
        'id': flight.id,
        'callsign': flight.callsign or getattr(flight, 'number', '') or '',
        'airline': flight.airline_icao or getattr(flight, 'airline_iata', '') or 'Unknown',
        'aircraft': flight.aircraft_code or 'Unknown',
        'registration': flight.registration or 'N/A',
        'origin': flight.origin_airport_iata or 'N/A',
        'destination': flight.destination_airport_iata or 'N/A',
        'lat': flight.latitude,
        'lng': flight.longitude,
        'altitude': flight.altitude,
        'speed': flight.ground_speed,
        'heading': flight.heading or 0,
    }


def serialize_flight_detailed(flight, raw_details=None):
    """
    set_flight_details()実行後の情報 + 生のdetails辞書からICAOコード・航跡を追加。
    実機確認済み(2026-XX-XX): airport.origin/destination.code.icao、trail[].lat/lng/alt/ts
    のキー構造は実データと一致することを確認済み。
    """
    result = {
        'id': flight.id,
        'callsign': flight.callsign or getattr(flight, 'number', '') or '',
        'airline': getattr(flight, 'airline_name', None) or flight.airline_icao or 'Unknown',
        'aircraft': getattr(flight, 'aircraft_model', None) or flight.aircraft_code or 'Unknown',
        'registration': flight.registration or 'N/A',
        'origin': getattr(flight, 'origin_airport_name', None) or flight.origin_airport_iata or 'N/A',
        'destination': getattr(flight, 'destination_airport_name', None) or flight.destination_airport_iata or 'N/A',
        'lat': flight.latitude,
        'lng': flight.longitude,
        'altitude': flight.altitude,
        'speed': flight.ground_speed,
        'heading': flight.heading or 0,
        'origin_icao': None,
        'destination_icao': None,
        'trail': [],
    }

    if raw_details:
        airport = raw_details.get('airport', {}) or {}
        origin = airport.get('origin') or {}
        destination = airport.get('destination') or {}
        result['origin_icao'] = (origin.get('code') or {}).get('icao')
        result['destination_icao'] = (destination.get('code') or {}).get('icao')

        # 実機確認: trailは新しい順(降順)で返る。ts昇順(古い→新しい)にソートしてから渡す
        trail_raw = raw_details.get('trail', []) or []
        trail_sorted = sorted(trail_raw, key=lambda p: p.get('ts', 0))
        result['trail'] = [
            {
                'lat': p.get('lat'),
                'lng': p.get('lng'),
                'alt': p.get('alt'),
                'ts': p.get('ts'),
            }
            for p in trail_sorted
        ]

    return result


@app.route('/api/flights')
@handle_fr_errors
def get_flights():
    """bounds(地図の表示範囲)内のフライト一覧を取得(軽量版・詳細取得なし)"""
    bounds_str = request.args.get('bounds', DEFAULT_BOUNDS)
    try:
        tl_y, tl_x, br_y, br_x = parse_bounds(bounds_str)
    except ValueError as e:
        return jsonify({'error': 'invalid_bounds', 'detail': str(e)}), 400

    area_error = validate_bounds_area(tl_y, tl_x, br_y, br_x)
    if area_error:
        return area_error

    bounds_obj = fr_api.get_bounds({
        "tl_y": tl_y, "tl_x": tl_x,
        "br_y": br_y, "br_x": br_x
    })
    flights = fr_api.get_flights(bounds=bounds_obj)
    return jsonify([serialize_flight_basic(f) for f in flights])


@app.route('/api/flight/<flight_id>')
@handle_fr_errors
def get_flight_details(flight_id):
    """
    個別便の詳細を取得。get_flight_details()にはFlightオブジェクトが必要なため、
    まず現在のbounds内のフライト一覧からidで該当機体を再取得してからset_flight_detailsする。
    """
    bounds_str = request.args.get('bounds', DEFAULT_BOUNDS)
    try:
        tl_y, tl_x, br_y, br_x = parse_bounds(bounds_str)
    except ValueError as e:
        return jsonify({'error': 'invalid_bounds', 'detail': str(e)}), 400

    area_error = validate_bounds_area(tl_y, tl_x, br_y, br_x)
    if area_error:
        return area_error

    bounds_obj = fr_api.get_bounds({
        "tl_y": tl_y, "tl_x": tl_x,
        "br_y": br_y, "br_x": br_x
    })
    flights = fr_api.get_flights(bounds=bounds_obj)
    target = next((f for f in flights if f.id == flight_id), None)

    if target is None:
        return jsonify({'error': 'Flight not found in current bounds'}), 404

    raw_details = fr_api.get_flight_details(target)
    target.set_flight_details(raw_details)

    return jsonify(serialize_flight_detailed(target, raw_details))


if __name__ == '__main__':
    print(f"[起動確認] app.py の場所: {SCRIPT_DIR}")
    print(f"[起動確認] static_folder: {STATIC_DIR}")
    if STATIC_DIR:
        print(f"[起動確認] index.html: 存在します ({os.path.join(STATIC_DIR, 'index.html')})")
    else:
        print("[起動確認] index.html: 見つかりませんでした")
    app.run(host='0.0.0.0', debug=True, port=5000)