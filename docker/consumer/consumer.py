#!/usr/bin/env python3
import os, sys, time, signal, logging, json
from typing import Any, Dict, Optional, Sequence, List
import requests
from requests.adapters import HTTPAdapter, Retry
from kafka import KafkaConsumer
from kafka.errors import KafkaConfigurationError

# ---------- Helpers ----------
def _parse_topics(raw: Optional[str]) -> Sequence[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(entry).strip() for entry in raw if str(entry).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]

# ---------- Config via environment ----------
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "192.168.5.21:9092,192.168.5.70:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "utilizations")
_topics_env = _parse_topics(os.getenv("KAFKA_TOPICS"))
if _topics_env:
    KAFKA_TOPICS = list(_topics_env)
else:
    KAFKA_TOPICS = list(_parse_topics(KAFKA_TOPIC) or ["utilizations"])

# NEW (scale & flexibility)
KAFKA_TOPIC_PATTERN = os.getenv("KAFKA_TOPIC_PATTERN")  # e.g. ^weather\.|^air\.|^water\.
KAFKA_AUTO_OFFSET_RESET = os.getenv("KAFKA_AUTO_OFFSET_RESET", "latest")  # or "earliest"
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "vm3-consumer")
KAFKA_INSTANCE_ID = os.getenv("KAFKA_INSTANCE_ID")  # static membership id (optional)

FLASK_URL = os.getenv("FLASK_URL", "http://127.0.0.1:5000/update_data")
POST_TIMEOUT = float(os.getenv("POST_TIMEOUT", "5"))
POST_BACKOFF_SEC = float(os.getenv("POST_BACKOFF_SEC", "1.0"))

# New: batching to reduce Flask pressure
POST_BATCH_SIZE = int(os.getenv("POST_BATCH_SIZE", "1"))  # if >1, use bulk endpoint
POST_ENDPOINT_BULK = os.getenv("FLASK_URL_BULK", "").strip() or None  # e.g. http://flask:5000/bulk_update

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("consumer")

# ---------- Graceful shutdown ----------
_shutdown = False
def _handle_stop(signum, _frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s; shutting down...", signum)

signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

def make_http_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=["POST", "GET"],
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

# ---------- Normalization helpers ----------
def _decode_maybe(b: Optional[bytes]) -> Optional[str]:
    if b is None:
        return None
    try:
        return b.decode("utf-8", errors="replace")
    except Exception:
        return None

def _json_parse_maybe(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def _infer_sensor_type(obj: Any, kafka_key_text: Optional[str], topic: str) -> str:
    # 1) From dict-like data
    if isinstance(obj, dict):
        for k in ("sensorType", "sensor_type", "type", "name", "sensor"):
            if k in obj and isinstance(obj[k], str) and obj[k].strip():
                return obj[k].strip()
    # 2) From Kafka message key
    if kafka_key_text and kafka_key_text.strip():
        return kafka_key_text.strip()
    # 3) Fallback: use topic
    return topic

def _ensure_sensorType_field(obj: Dict[str, Any], sensor_type: str) -> None:
    # Preserve original key if present, but ensure canonical "sensorType"
    if "sensorType" not in obj:
        if "sensor_type" in obj and isinstance(obj["sensor_type"], str):
            obj["sensorType"] = obj["sensor_type"]
        else:
            obj["sensorType"] = sensor_type

def _wrap_any_to_document(
    value_obj: Any,
    sensor_type: str,
    kafka_meta: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Convert arbitrary input (dict/list/scalar/text) to a document Flask/Mongo can store.
    - dict: pass through, ensure sensorType, add meta
    - list: wrap under "values"
    - scalar/text: wrap under "value" or "raw"
    """
    if isinstance(value_obj, dict):
        doc = dict(value_obj)  # shallow copy; keep user fields intact
        _ensure_sensorType_field(doc, sensor_type)
    elif isinstance(value_obj, list):
        doc = {"sensorType": sensor_type, "values": value_obj}
    elif isinstance(value_obj, (int, float, bool)) or value_obj is None:
        doc = {"sensorType": sensor_type, "value": value_obj}
    else:
        # string or unknown -> store as raw
        doc = {"sensorType": sensor_type, "raw": str(value_obj)}

    # Attach minimal metadata (namespaced) to help with debugging/auditing
    doc.setdefault("_meta", {})
    doc["_meta"].setdefault("kafka", {})
    doc["_meta"]["kafka"].update(kafka_meta)
    return doc

def normalize_message(
    value_bytes: Optional[bytes],
    key_bytes: Optional[bytes],
    topic: str,
    partition: int,
    offset: int
) -> Dict[str, Any]:
    """
    Turn the Kafka record into a Mongo-ready dict, without losing heterogeneous shapes.
    """
    key_text = _decode_maybe(key_bytes)
    value_text = _decode_maybe(value_bytes)

    parsed = _json_parse_maybe(value_text)
    sensor_type = _infer_sensor_type(parsed if parsed is not None else value_text, key_text, topic)

    kafka_meta = {
        "topic": topic,
        "partition": partition,
        "offset": offset,
        "key": key_text,
    }

    # If JSON parse succeeded, pass dict/list/scalar through; else keep the raw text
    value_obj = parsed if parsed is not None else value_text
    doc = _wrap_any_to_document(value_obj, sensor_type, kafka_meta)
    return doc

# ---------- Main ----------
def main() -> int:
    log.info("Starting consumer | bootstrap=%s | topics=%s | group_id=%s | flask_url=%s",
             KAFKA_BOOTSTRAP, KAFKA_TOPICS, KAFKA_GROUP_ID, FLASK_URL)

    # Build kwargs to allow cooperative-sticky + static membership when available
    consumer_kwargs = dict(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=KAFKA_GROUP_ID,
        enable_auto_commit=True,
        auto_offset_reset=KAFKA_AUTO_OFFSET_RESET,
        consumer_timeout_ms=0,
        request_timeout_ms=30000,
        max_poll_interval_ms=300000,
        reconnect_backoff_ms=50,
        reconnect_backoff_max_ms=1000,
    )
    # Optional scale-friendly settings (ignore if kafka-python too old)
    try:
        consumer_kwargs["partition_assignment_strategy"] = ("cooperative-sticky",)
    except Exception:
        pass
    if KAFKA_INSTANCE_ID:
        consumer_kwargs["group_instance_id"] = KAFKA_INSTANCE_ID

    try:
        consumer = KafkaConsumer(**consumer_kwargs)
    except (TypeError, KafkaConfigurationError):
        # fallback (older kafka-python missing some kwargs)
        for k in ("partition_assignment_strategy", "group_instance_id"):
            consumer_kwargs.pop(k, None)
        consumer = KafkaConsumer(**consumer_kwargs)

    if KAFKA_TOPIC_PATTERN:
        if KAFKA_TOPICS:
            log.info(
                "Topic pattern provided; ignoring static topic list %s",
                KAFKA_TOPICS,
            )
        consumer.subscribe(pattern=KAFKA_TOPIC_PATTERN)
    else:
        consumer.subscribe(topics=KAFKA_TOPICS)

    session = make_http_session()
    _batch: List[Dict[str, Any]] = []

    def flush_batch():
        if not _batch:
            return
        try:
            if POST_BATCH_SIZE > 1 and POST_ENDPOINT_BULK:
                resp = session.post(POST_ENDPOINT_BULK, json=_batch, timeout=POST_TIMEOUT)
                if resp.status_code != 200:
                    log.warning("Bulk POST failed status=%s body=%s",
                                resp.status_code, (resp.text or "")[:200])
            else:
                # send individually to /update_data
                for doc in _batch:
                    r = session.post(FLASK_URL, json=doc, timeout=POST_TIMEOUT)
                    if r.status_code != 200:
                        log.warning("Flask POST failed status=%s body=%s",
                                    r.status_code, (r.text or "")[:200])
            log.info("Flask POST ok (batch size=%d)", len(_batch))
        except Exception as e:
            log.error("Error posting to Flask (batch size=%d): %s", len(_batch), e)
        finally:
            _batch.clear()

    try:
        while not _shutdown:
            records = consumer.poll(timeout_ms=1000)
            if not records:
                continue

            for tp, msgs in records.items():
                for msg in msgs:
                    if _shutdown:
                        break
                    try:
                        doc = normalize_message(
                            msg.value, msg.key, tp.topic, tp.partition, msg.offset
                        )
                        # compact preview
                        preview = json.dumps({k: doc[k] for k in ("sensorType", "value", "values", "raw", "device_id", "source") if k in doc})[:240]
                        log.info("Consumed %s p=%s o=%s | %s", tp.topic, tp.partition, msg.offset, preview)
                    except Exception as e:
                        log.exception("Normalization error; skipping %s-%s@%s: %s",
                                      tp.topic, tp.partition, msg.offset, e)
                        continue

                    # enqueue & flush in batches
                    _batch.append(doc)
                    if len(_batch) >= max(1, POST_BATCH_SIZE):
                        flush_batch()
    except Exception as e:
        log.exception("Fatal error in consumer loop: %s", e)
        return 1
    finally:
        try:
            flush_batch()
        except Exception:
            pass
        try:
            consumer.close()
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass
        log.info("Consumer stopped.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
