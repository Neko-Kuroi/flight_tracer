from flask import Flask, jsonify, request
from flask_cors import CORS
from FlightRadarAPI import FlightRadar24API
from functools import wraps

app = Flask(__name__)
CORS(app)
fr_api = FlightRadar24API()


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


def serialize_flight(flight):
    """Flightオブジェクトを一覧表示用の辞書に変換"""
    return {
        'id': flight.id,
        'callsign': flight.callsign or '',
        'airline': flight.airline_name or 'Unknown',
        'aircraft': flight.aircraft_model or 'Unknown',
        'registration': flight.registration or 'N/A',
        'origin': flight.origin_airport_name or 'Unknown',
        'destination': flight.destination_airport_name or 'Unknown',
        'lat': flight.latitude,
        'lng': flight.longitude,
        'altitude': flight.altitude,
        'speed': flight.ground_speed,
        'heading': flight.heading,
    }


@app.route('/api/flights')
@handle_fr_errors
def get_flights():
    """全世界のフライト一覧を取得（フィルタなし・常に全件）"""
    flights = fr_api.get_flights()
    return jsonify([serialize_flight(f) for f in flights])


@app.route('/api/flights/area/<bounds>')
@handle_fr_errors
def get_flights_by_area(bounds):
    coords = [float(x) for x in bounds.split(',')]
    bounds_obj = fr_api.get_bounds({
        "tl_y": coords[0], "tl_x": coords[1],
        "br_y": coords[2], "br_x": coords[3]
    })
    flights = fr_api.get_flights(bounds=bounds_obj)
    return jsonify([serialize_flight(f) for f in flights])


@app.route('/api/flight/<flight_id>')
@handle_fr_errors
def get_flight_details(flight_id):
    """
    ⚠️ 未検証: get_flight_details()の返り値構造は実機で確認できていません
    （Cloudflareブロックにより検証未完了）。フロントエンドからも未接続。
    実機で構造確認が取れ次第、このコメントを削除・フィールド名を再確認してください。
    """
    details = fr_api.get_flight_details(flight_id)

    if not details:
        return jsonify({'error': 'Flight not found'}), 404

    identification = details.get('identification', {})
    aircraft = details.get('aircraft', {})
    airline = details.get('airline', {}) or {}
    airport = details.get('airport', {})
    origin = airport.get('origin') or {}
    destination = airport.get('destination') or {}
    trail = details.get('trail', [])
    latest = trail[0] if trail else {}

    return jsonify({
        'id': identification.get('id', flight_id),
        'callsign': identification.get('callsign'),
        'airline': airline.get('name'),
        'aircraft': (aircraft.get('model') or {}).get('text'),
        'registration': aircraft.get('registration'),
        'origin': origin.get('name'),
        'destination': destination.get('name'),
        'origin_icao': (origin.get('code') or {}).get('icao'),
        'destination_icao': (destination.get('code') or {}).get('icao'),
        'lat': latest.get('lat'),
        'lng': latest.get('lng'),
        'altitude': latest.get('alt'),
        'speed': latest.get('spd'),
        'heading': latest.get('hd'),
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)