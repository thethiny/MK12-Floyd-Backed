import os

from flask_cors import CORS
is_windows = os.name == "nt"
from threading import Lock
from flask import Flask, request, jsonify, url_for, g
if not is_windows:
    import gevent.monkey
    gevent.monkey.patch_all()

from src.utils.floyd import get_floyd_data, get_floyd_maps, parse_floyd_data
from src.utils import init_secrets
steam_key, *_ = init_secrets()

from src.api.mk12 import MK12API
from src.api.wb import WBAPI
from src.routes.platforms import find_any, platform_bp, sanitize_platform

mk_lock = Lock()
api = MK12API(steam_key=steam_key)
api.set_mutex_lock(mk_lock)
api.login()

wb_lock = Lock()
wb_api = WBAPI(authorization_code=api.wb_authorization_code) # Auth code is one time use
wb_api.set_mutex_lock(wb_lock)

app = Flask("Floyd Tracker")
CORS(app, resources={r"/*": {"origins": "*"}})
app.register_blueprint(platform_bp, url_prefix="/platforms")

try:
    with open("db/hits.txt", "r") as f:
        id_hits, data_hits, *_ = [int(l.strip()) for l in f.readlines() if l.strip()]
except Exception as e:
    print(e)
    id_hits = data_hits = 0

print("Starting with hits", id_hits, data_hits)

# @app.before_request
# def load_globals():
#     g.api = api
#     g.mk_lock = mk_lock
#     g.wb_api = wb_api
#     g.wb_lock = wb_lock

def write_hits():
    print("Writing {id_hits=} {data_hits=}")
    with open("db/hits.txt", "w") as f:
        f.write(str(id_hits) + "\n")
        f.write(str(data_hits) + "\n")

def write_hits_mutex():
    if (id_hits+data_hits) % 200 != 1:
        return
    if api.lock:
        with api.lock:        
            write_hits()
    else:
        write_hits()

@app.route("/id")
def get_wb_id_route():
    global id_hits
    id_hits += 1
    print("id hits:", id_hits)
    write_hits_mutex()
    
    params = request.args

    platform = params.get("platform", "").strip()
    username = params.get("username", "").strip()

    if not username or not platform:
        return jsonify(error="`platform` and `username` are both required!"), 400

    print(f"Received a request for getting id for {username} on {platform}")

    platform = sanitize_platform(platform) # Lowercase the platform

    if platform == "wb_network":
        user_id = wb_api.search(username)
        if user_id:
            user_id = user_id.get("public_id", "")
    elif platform.startswith("wb"):
        if username.isdigit():
            return jsonify(error=f"Please enter a username instead of a number."), 403
        search_by = platform.split("_", 1)[-1]
        user_id = wb_api.search_by(username, search_by, delete_afterwards=True) # friend / incoming / outgoing
        if user_id:
            user_id = user_id.get("public_id", "")
    else:
        user_id, status_code = find_any()
        if status_code != 200:
            return user_id, status_code # jsonify
        user_id = (user_id.json or {}).get("user_id")

    if not user_id:
        return jsonify(error=f"Couldn't find user {username}"), 404

    return jsonify({
        "username": username,
        "user_id": user_id,
        "platform": platform,
    })

@app.get("/data")
def get_floyd_data_route():
    global data_hits
    data_hits += 1
    print("data hits:", data_hits)
    write_hits_mutex()
    
    user_id = request.args.get("user_id", "")
    platform = request.args.get("platform", "")
    username = request.args.get("username", "")
    if not user_id or not platform or not username:
        return jsonify(
            error=f"`user_id`, `platform`, and `username` are required. Info retrieved automatically from {url_for('get_wb_id_route')}"
        )

    print(f"Received a request for getting id for {user_id} on {platform}")

    platform = sanitize_platform(platform, wb=True)

    modules = api.get_mk_id_from_wb(user_id, platform).get("player_modules", [])
    if not len(modules):
        return jsonify(error=f"User found but no id was returned from mk servers. If you're on Nintendo Switch, sorry that doesn't work now. If you're not on Switch then either your WB account isn't linked to this profile, or try again later."), 404

    player_module = modules[0]
    hydra_id = player_module["hydra_id"]
    hydra_platform = player_module["platform"]
    platform_name = player_module["platform_name"]
    hydra_name = player_module["wbpn_name"]

    profile = api.get_profile(hydra_id)
    floyd_data = get_floyd_data(profile)
    parsed_data = parse_floyd_data(floyd_data, hydra_platform)
    floyd_map = get_floyd_maps()
    
    if username.lower().strip() == user_id.lower().strip(): # no username found
        username = platform_name

    user_obj = {
        "username": username,
        "user_id": user_id,
        "user_platform": platform,
        "mk12": {
            "hydra_linked_platform": hydra_platform,
            "linked_platform_username": platform_name,
            "hydra_username": hydra_name,
            "user_id": hydra_id,
        }
    }

    parsed_data = {
        "parsed": parsed_data["parsed"],
        "raw": {floyd_map[f"profilestat{k}"]: v for k, v in parsed_data["raw"].items()},
        "hints": parsed_data["hints"],
    }

    return jsonify(user=user_obj, data=parsed_data)


if __name__ == "__main__":
    if not is_windows:
        from gevent.pywsgi import WSGIServer
        port = int(os.environ.get("PORT", 8080))
        print(f"WSGI Active on {port} with GEvent")
        WSGIServer(("0.0.0.0", port), app).serve_forever()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
        
    
    
    # Run with gunicorn -k gevent -w 1 --worker-connections 100 --preload app:app
