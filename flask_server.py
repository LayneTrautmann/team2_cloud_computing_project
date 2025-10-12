from flask import Flask, request, jsonify
import logging, os, signal
from datetime import datetime
from pymongo import MongoClient, ASCENDING, errors
from bson.objectid import ObjectId
from urllib.parse import urlparse

# -------- Logging --------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flask-app")

app = Flask(__name__)

# -------- Mongo wiring (env-driven) --------
def _db_name_from_uri(uri: str) -> str | None:
    try:
        p = urlparse(uri)
        # urlparse puts the db in path like "/sensors"
        if p.path and p.path != "/":
            return p.path.lstrip("/")
    except Exception:
        pass
    return None

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/sensors")
FALLBACK_DB = os.getenv("MONGO_DB", "sensors")
DB_NAME = _db_name_from_uri(MONGO_URI) or FALLBACK_DB

# Configure a short selection timeout so failures surface quickly
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]
readings = db["readings"]

# Create a helpful index (idempotent; no-op if it already exists)
try:
    readings.create_index([("ts", ASCENDING)])
except Exception as e:
    log.warning("Index creation skipped: %s", e)

# Startup readiness ping (does not crash the app; logs only)
try:
    client.admin.command("ping")
    log.info("MongoDB ping OK; using db='%s' via %s", DB_NAME, MONGO_URI)
except Exception as e:
    log.error("MongoDB not reachable at startup: %s", e)

# -------- Helpers --------
def _stringify_ids(docs):
    for d in docs:
        if isinstance(d.get("_id"), ObjectId):
            d["_id"] = str(d["_id"])
    return docs

def _coerce_payload(raw):
    """Ensure minimal schema + server-side timestamp fallback."""
    payload = raw if isinstance(raw, dict) else {}
    payload.setdefault("ts", datetime.utcnow().isoformat() + "Z")
    payload.setdefault("sensorType", payload.get("sensor_type", "unknown"))
    return payload

# -------- Routes --------
@app.route("/update_data", methods=["POST"])
def update_data():
    payload_in = request.get_json(force=True, silent=True) or {}
    payload = _coerce_payload(payload_in)

    log.info("POST /update_data payload=%s", payload)

    try:
        res = readings.insert_one(payload)
        return jsonify({"status": "ok", "id": str(res.inserted_id)}), 200
    except errors.PyMongoError as e:
        log.exception("Mongo insert failed")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/last", methods=["GET"])
def last():
    try:
        n = max(1, min(100, int(request.args.get("n", 5))))
        docs = list(readings.find({}, {"_id": 1, "ts": 1, "sensorType": 1, "value": 1}).sort("_id", -1).limit(n))
        return jsonify(_stringify_ids(docs)), 200
    except errors.PyMongoError as e:
        log.exception("Mongo query failed")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/healthz", methods=["GET"])
def healthz():
    """Liveness: app process is up."""
    return jsonify({"status": "healthy"}), 200

@app.route("/readyz", methods=["GET"])
def readyz():
    """Readiness: confirm DB reachable."""
    try:
        client.admin.command("ping")
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "not_ready", "error": str(e)}), 503

# -------- Graceful shutdown --------
def _graceful_shutdown(*_):
    try:
        client.close()
    finally:
        os._exit(0)

signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)

# -------- Entrypoint --------
if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
