from flask import Flask, request, jsonify
import logging, os, random, signal
from datetime import datetime
from typing import List, Dict, Any, Optional
from pymongo import MongoClient, ASCENDING, errors
from bson.objectid import ObjectId
from urllib.parse import urlparse
from collections import defaultdict

# -------- Logging --------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flask-app")

app = Flask(__name__)

# -------- Mongo wiring (env-driven) --------
def _db_name_from_uri(uri: str) -> Optional[str]:
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

def _resolve_collection_names() -> List[str]:
    raw = os.getenv("MONGO_SHARD_COLLECTIONS")
    if raw:
        names = [part.strip() for part in raw.split(",") if part.strip()]
        if names:
            return names

    try:
        shard_count = max(1, int(os.getenv("MONGO_SHARDS", "5")))
    except ValueError:
        shard_count = 5
    prefix = os.getenv("MONGO_SHARD_PREFIX", "readings_shard")

    if shard_count == 1:
        return [os.getenv("MONGO_PRIMARY_COLLECTION", "readings")]
    return [f"{prefix}{idx+1}" for idx in range(shard_count)]

COLLECTION_NAMES = _resolve_collection_names()
READING_COLLECTIONS = [db[name] for name in COLLECTION_NAMES]

log.info("Using Mongo collections: %s", ", ".join(COLLECTION_NAMES))

# Create a helpful index (idempotent; no-op if it already exists)
for coll in READING_COLLECTIONS:
    try:
        coll.create_index([("ts", ASCENDING)])
    except Exception as e:
        log.warning("Index creation skipped for %s: %s", coll.name, e)

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
    payload.setdefault("sensorType", payload.get("sensor_type", payload.get("sensorType", "unknown")))
    return payload

def _pick_collection():
    return random.choice(READING_COLLECTIONS)

def _insert_single(doc: Dict[str, Any]):
    collection = _pick_collection()
    res = collection.insert_one(doc)
    return collection.name, res.inserted_id

def _insert_many_random(docs: List[Dict[str, Any]]) -> int:
    """Assign each document to a random shard and bulk-insert per shard."""
    buckets = defaultdict(list)
    for doc in docs:
        buckets[_pick_collection()].append(doc)

    inserted = 0
    for collection, chunk in buckets.items():
        res = collection.insert_many(chunk, ordered=False)
        inserted += len(res.inserted_ids)
        log.debug("Inserted %d docs into %s", len(res.inserted_ids), collection.name)
    return inserted

# -------- Routes --------
@app.route("/update_data", methods=["POST"])
def update_data():
    payload_in = request.get_json(force=True, silent=True) or {}
    payload = _coerce_payload(payload_in)

    log.info("POST /update_data payload=%s", payload)

    try:
        coll_name, inserted_id = _insert_single(payload)
        return jsonify({"status": "ok", "collection": coll_name, "id": str(inserted_id)}), 200
    except errors.PyMongoError as e:
        log.exception("Mongo insert failed")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/bulk_update", methods=["POST"])
def bulk_update():
    try:
        payload = request.get_json(force=True, silent=True)
        if not isinstance(payload, list):
            return jsonify({"status":"error","error":"expected JSON array"}), 400

        docs: List[Dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                docs.append(_coerce_payload(item))
        if not docs:
            return jsonify({"status":"ok","inserted":0}), 200

        inserted_count = _insert_many_random(docs)
        return jsonify({"status":"ok","inserted":inserted_count}), 200
    except errors.PyMongoError as e:
        log.exception("Mongo bulk insert failed")
        return jsonify({"status":"error","error":str(e)}), 500

@app.route("/last", methods=["GET"])
def last():
    try:
        n = max(1, min(100, int(request.args.get("n", 5))))
        docs: List[Dict[str, Any]] = []
        projection = {"_id": 1, "ts": 1, "sensorType": 1, "value": 1}
        for collection in READING_COLLECTIONS:
            docs.extend(list(collection.find({}, projection).sort("_id", -1).limit(n)))
        docs.sort(key=lambda d: d.get("_id"), reverse=True)
        docs = docs[:n]
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

# Tiny metric for eyeballing load
_requests_total = 0
@app.before_request
def _count_req():
    global _requests_total
    _requests_total += 1

@app.route("/metrics")
def metrics():
    return f"flask_requests_total { _requests_total }\n", 200, {"Content-Type":"text/plain"}

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
