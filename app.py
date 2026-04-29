from flask import Flask, render_template, request, jsonify
import requests
import os
import logging
from dotenv import load_dotenv

# Configurar logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cargar las variables del archivo .env
load_dotenv()

# Variable global para cachear el token
_cached_token = None

def obtener_access_token():
    """Obtiene el accessToken con recarga forzada de .env."""
    global _cached_token
    load_dotenv(override=True)
    
    email = os.getenv("EMT_EMAIL")
    password = os.getenv("EMT_PASSWORD")
    client_id = os.getenv("X_CLIENT_ID")
    api_key = os.getenv("X_API_KEY")

    if _cached_token:
        return _cached_token

    url = "https://openapi.emtmadrid.es/v1/mobilitylabs/user/login/"
    headers = {'email': email, 'password': password} if email and password else {'X-ClientId': client_id, 'X-ApiKey': api_key}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if response.status_code == 200 and 'data' in data and data['data']:
            _cached_token = data['data'][0].get('accessToken')
            logger.info("Login exitoso en EMT.")
            return _cached_token
        else:
            logger.error(f"Fallo en login EMT: {data}")
    except Exception as e:
        logger.error(f"Error técnico en login: {e}")
    return None

def crear_app():
    app = Flask(__name__)

    def extract_coordinates(features):
        points = []
        for feature in features:
            geom = feature.get('geometry', {})
            if geom.get('type') == 'MultiLineString':
                for line in geom.get('coordinates', []):
                    points.extend([[p[1], p[0]] for p in line])
            elif geom.get('type') == 'LineString':
                coords = geom.get('coordinates', [])
                points.extend([[c[1], c[0]] for c in coords])
        return points

    def get_line_route(line_id):
        token = obtener_access_token()
        if not token: return None
        l_id = line_id.zfill(3) if line_id.isdigit() else line_id
        url = f"https://openapi.emtmadrid.es/v1/transport/busemtmad/lines/{l_id}/route/"
        try:
            response = requests.get(url, headers={'accessToken': token}, timeout=10)
            data = response.json()
            if 'data' in data and isinstance(data['data'], dict):
                itinerary = data['data'].get('itinerary', {})
                points = []
                for direction in ['toA', 'toB']:
                    dir_data = itinerary.get(direction, {})
                    features = dir_data.get('features', [])
                    if features:
                        points = extract_coordinates(features)
                        if points: break
                return points
        except Exception as e:
            logger.error(f"Error obteniendo ruta {l_id}: {e}")
        return None

    @app.route('/api/lines')
    def get_all_lines():
        token = obtener_access_token()
        if not token: return jsonify({"error": "No token"}), 401
        
        from datetime import datetime
        today = datetime.now().strftime('%d/%m/%Y')
        
        # El endpoint /lines/info/ es el más fiable actualmente para obtener todas las líneas
        url = "https://openapi.emtmadrid.es/v1/transport/busemtmad/lines/info/"
        try:
            logger.info(f"Obteniendo líneas desde {url}")
            r = requests.get(url, headers={'accessToken': token}, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                items = data.get('data')
                if isinstance(items, list):
                    lines = []
                    for item in items:
                        lines.append({
                            'line': item.get('line'),
                            'label': item.get('label'),
                            'name': f"Línea {item.get('label')}: {item.get('nameA')} - {item.get('nameB')}"
                        })
                    logger.info(f"Éxito: {len(lines)} líneas obtenidas.")
                    return jsonify({"lines": lines})
            
            # Fallback a getlistlines si info falla
            url_fallback = "https://openapi.emtmadrid.es/v1/transport/busemtmad/getlistlines/"
            logger.info(f"Fallback a {url_fallback}")
            r = requests.post(url_fallback, headers={'accessToken': token}, 
                              json={"SelectDate": today}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                items = data.get('data')
                if isinstance(items, list):
                    lines = [{
                        'line': i.get('line'),
                        'label': i.get('label'),
                        'name': f"Línea {i.get('label')}: {i.get('nameA')} - {i.get('nameB')}"
                    } for i in items]
                    return jsonify({"lines": lines})

        except Exception as e:
            logger.error(f"Error cargando líneas: {e}")
                
        return jsonify({"lines": [], "error": "No se pudieron cargar las líneas"}), 404

    @app.route('/api/line/<line_id>/stops')
    def get_line_stops(line_id):
        token = obtener_access_token()
        if not token: return jsonify({"error": "No token"}), 401
        l_id = line_id.zfill(3) if line_id.isdigit() else line_id
        url = f"https://openapi.emtmadrid.es/v1/transport/busemtmad/lines/{l_id}/route/"
        try:
            r = requests.get(url, headers={'accessToken': token}, timeout=10)
            data = r.json()
            stops_list = []
            if 'data' in data and isinstance(data['data'], dict):
                stops_data = data['data'].get('stops', {})
                dir_stops = stops_data.get('toA') or stops_data.get('toB')
                if dir_stops:
                    features = dir_stops.get('features', [])
                    for f in features:
                        props = f.get('properties', {})
                        geom = f.get('geometry', {})
                        coords = geom.get('coordinates', [])
                        stops_list.append({
                            'stop_id': props.get('stopNum'),
                            'name': props.get('stopName'),
                            'lat': coords[1] if len(coords) > 1 else None,
                            'lon': coords[0] if len(coords) > 0 else None
                        })
            return jsonify({"stops": stops_list})
        except Exception as e:
            logger.error(f"Error paradas {l_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/line/<line_id>/route')
    def get_line_route_api(line_id):
        points = get_line_route(line_id)
        if points:
            return jsonify({"route": points})
        return jsonify({"error": "No se encontró la ruta"}), 404

    @app.route('/api/line/<line_id>/buses')
    def get_line_buses(line_id):
        token = obtener_access_token()
        if not token: return jsonify({"error": "No token"}), 401
        
        # 1. Obtener las paradas de la línea para saber dónde preguntar
        l_id = line_id.zfill(3) if line_id.isdigit() else line_id
        route_url = f"https://openapi.emtmadrid.es/v1/transport/busemtmad/lines/{l_id}/route/"
        try:
            r_route = requests.get(route_url, headers={'accessToken': token}, timeout=10)
            route_data = r_route.json()
            if 'data' not in route_data or 'stops' not in route_data['data']:
                return jsonify({"buses": []})
            
            # Recopilar todos los stopNum únicos de la línea
            stops = route_data['data']['stops']
            all_stop_nums = []
            for direction in ['toA', 'toB']:
                if direction in stops and 'features' in stops[direction]:
                    for f in stops[direction]['features']:
                        all_stop_nums.append(f['properties']['stopNum'])
            
            # Tomar una muestra representativa (ej: cada 4 paradas)
            all_stop_nums = list(set(all_stop_nums))
            sample_size = min(len(all_stop_nums), 10)
            if sample_size == 0: return jsonify({"buses": []})
            
            step = len(all_stop_nums) // sample_size
            sample_stops = [all_stop_nums[i] for i in range(0, len(all_stop_nums), step)][:sample_size]
            
            buses_found = {}
            
            # 2. Consultar 'arrives' para esas paradas
            for s_id in sample_stops:
                url_arr = f"https://openapi.emtmadrid.es/v2/transport/busemtmad/stops/{s_id}/arrives/"
                body = {
                    "cultureInfo": "ES",
                    "Text_StopRequired_YN": "N",
                    "Text_EstimationsRequired_YN": "Y",
                    "Text_IncidencesRequired_YN": "N"
                }
                try:
                    r_arr = requests.post(url_arr, headers={'accessToken': token}, json=body, timeout=5)
                    arr_data = r_arr.json()
                    if 'data' in arr_data and arr_data['data']:
                        for stop_info in arr_data['data']:
                            for arrive in stop_info.get('Arrive', []):
                                # Filtrar por nuestra línea
                                if arrive.get('line') == line_id:
                                    bus_id = arrive.get('bus')
                                    geom = arrive.get('geometry', {})
                                    coords = geom.get('coordinates', [])
                                    if bus_id and coords:
                                        # Guardar o actualizar posición (el más cercano a la parada será el más reciente)
                                        buses_found[bus_id] = {
                                            'bus_id': bus_id,
                                            'lat': coords[1],
                                            'lon': coords[0],
                                            'stop_id': arrive.get('stop'),
                                            'estimate': arrive.get('estimateArrive')
                                        }
                except: continue
                
            return jsonify({"buses": list(buses_found.values())})
            
        except Exception as e:
            logger.error(f"Error en get_line_buses: {e}")
            return jsonify({"error": str(e)}), 500

    def get_stop_detail(stop_id):
        token = obtener_access_token()
        if not token: return None
        url = f"https://openapi.emtmadrid.es/v1/transport/busemtmad/stops/{stop_id}/detail/"
        try:
            response = requests.get(url, headers={'accessToken': token}, timeout=10)
            data = response.json()
            if 'data' in data and data['data']:
                # El API devuelve data[0]['stops'][0]
                stops_container = data['data'][0]
                if 'stops' in stops_container and stops_container['stops']:
                    stop = stops_container['stops'][0]
                    geometry = stop.get('geometry', {})
                    coords = geometry.get('coordinates', [])
                    lat = coords[1] if len(coords) > 1 else (stop.get('lat') or stop.get('latitude'))
                    lon = coords[0] if len(coords) > 0 else (stop.get('lon') or stop.get('longitude'))
                    return {'name': stop.get('name', f"Parada {stop_id}"), 'lat': lat, 'lon': lon}
        except Exception as e:
            logger.error(f"Error en get_stop_detail: {e}")
        return None

    def consultar_emt(stop_id, line_arrive=None):
        token = obtener_access_token()
        if not token: return {"error": "Error de autenticación"}
        
        # 1. Obtener info básica de la parada
        stop_detail = get_stop_detail(stop_id)
        lat, lon = (stop_detail['lat'], stop_detail['lon']) if stop_detail else (None, None)
        
        # 2. Consultar tiempos de llegada
        l_path = f"{line_arrive}/" if line_arrive and line_arrive.lower() != 'all' else ""
        url = f"https://openapi.emtmadrid.es/v2/transport/busemtmad/stops/{stop_id}/arrives/{l_path}"
        body = {"cultureInfo": "ES", "Text_StopRequired_YN": "Y", "Text_EstimationsRequired_YN": "Y", "Text_IncidencesRequired_YN": "Y"}
        
        try:
            response = requests.post(url, headers={'accessToken': token}, json=body, timeout=10)
            data = response.json()
            
            arrivals = []
            incidents = ""
            
            if 'data' in data and data['data']:
                stop_data = data['data'][0]
                incidents = stop_data.get('Incident', {}).get('ext_description', '')
                
                for arr in stop_data.get('Arrive', []):
                    # Fallback de coordenadas desde la propia respuesta de "arrives"
                    if (lat is None or lon is None) and 'latitude' in arr:
                        lat, lon = arr['latitude'], arr['longitude']
                        
                    arrivals.append({
                        'line': arr.get('line'),
                        'destination': arr.get('destination'),
                        'minutes': 0 if arr.get('estimateArrive', 0) < 30 else arr.get('estimateArrive', 0) // 60,
                        'distance': arr.get('DistanceBus', 0)
                    })
                arrivals.sort(key=lambda x: x['minutes'])

            # 3. Obtener rutas de las líneas
            line_routes = []
            lines_to_fetch = []
            
            if line_arrive and line_arrive.lower() != 'all':
                lines_to_fetch = [line_arrive]
            else:
                # Si no hay filtro, sacamos las líneas únicas de los próximos autobuses
                lines_to_fetch = list(set(arr['line'] for arr in arrivals))

            for l_id_raw in lines_to_fetch:
                # Normalizar ID (ej: "9" -> "009")
                l_id = l_id_raw.zfill(3) if l_id_raw.isdigit() else l_id_raw
                route = get_line_route(l_id)
                if route:
                    line_routes.append({'label': l_id_raw, 'points': route})

            return {
                'stop_info': {
                    'stop_id': stop_id, 
                    'name': stop_detail['name'] if stop_detail else f"Parada {stop_id}", 
                    'lat': lat, 
                    'lon': lon
                },
                'arrivals': arrivals,
                'line_routes': line_routes,
                'incidents': incidents
            }
        except Exception as e:
            logger.error(f"Error general en consultar_emt: {e}")
        return {"error": "No se pudieron obtener datos"}

    @app.route('/', methods=['GET', 'POST'])
    def index():
        data, error, stop_id, line_arrive = None, None, "", ""
        if request.method == 'POST':
            stop_id = request.form.get('stop_id', '').strip()
            line_arrive = request.form.get('line_arrive', '').strip()
            if not stop_id:
                error = "Introduce un número de parada"
            else:
                result = consultar_emt(stop_id, line_arrive if line_arrive else 'all')
                if 'error' in result: error = result['error']
                else: data = result
        return render_template('index.html', data=data, error=error, stop_id=stop_id, line_arrive=line_arrive)

    return app

if __name__ == '__main__':
    app = crear_app()
    app.run(debug=True, port=5001)
