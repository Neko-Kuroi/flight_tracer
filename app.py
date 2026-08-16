import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from FlightRadarAPI import FlightRadar24API
from functools import wraps

# app.pyのあるディレクトリを基準にする(実行時のカレントディレクトリに依存させない)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, 'index.html')

app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
CORS(app)
fr_api = FlightRadar24API()


@app.route('/')
def index():
    """index.htmlをルートで配信(app.pyと同じディレクトリに置く前提)"""
    if not os.path.exists(INDEX_PATH):
        return (
            f"index.html が見つかりません: {INDEX_PATH}<br>"
            f"app.py と index.html を同じディレクトリに置いてください。",
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


def serialize_flight_detailed(flight):
    """set_flight_details()実行後、フルネーム込み(取れなければコードにフォールバック)"""
    return {
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
    }


@app.route('/api/flights')
@handle_fr_errors
def get_flights():
    """bounds(地図の表示範囲)内のフライト一覧を取得(軽量版・詳細取得なし)"""
    bounds_str = request.args.get('bounds', DEFAULT_BOUNDS)
    try:
        tl_y, tl_x, br_y, br_x = parse_bounds(bounds_str)
    except ValueError as e:
        return jsonify({'error': 'invalid_bounds', 'detail': str(e)}), 400

    area = abs(tl_y - br_y) * abs(tl_x - br_x)
    if area > MAX_BOUNDS_AREA:
        return jsonify({
            'error': 'bounds_too_large',
            'detail': f'表示範囲が広すぎます(area={area:.1f})。ズームインしてください'
        }), 400

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

    bounds_obj = fr_api.get_bounds({
        "tl_y": tl_y, "tl_x": tl_x,
        "br_y": br_y, "br_x": br_x
    })
    flights = fr_api.get_flights(bounds=bounds_obj)
    target = next((f for f in flights if f.id == flight_id), None)

    if target is None:
        return jsonify({'error': 'Flight not found in current bounds'}), 404

    details = fr_api.get_flight_details(target)
    target.set_flight_details(details)

    return jsonify(serialize_flight_detailed(target))


if __name__ == '__main__':
    print(f"[起動確認] app.py の場所: {BASE_DIR}")
    print(f"[起動確認] index.html の場所: {INDEX_PATH} (存在: {os.path.exists(INDEX_PATH)})")
    app.run(host='0.0.0.0', debug=True, port=5000)