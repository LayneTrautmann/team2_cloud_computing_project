#!/usr/bin/env python3
import argparse, json, random, time, uuid, sys, os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError, KafkaError

# -------------------- Sensor presets --------------------
# name -> (lo, hi, units, generator)
# Weather & environmental
PRESETS = {
    # Weather & environment
    "weather.temp_c":        ( -10.0,  40.0,   "C",     lambda a,b: round(random.uniform(a,b), 2)),
    "weather.humidity_pct":  (   5.0, 100.0,   "%",     lambda a,b: round(random.uniform(a,b), 1)),
    "weather.pressure_hpa":  ( 950.0, 1050.0,  "hPa",   lambda a,b: round(random.uniform(a,b), 1)),
    "weather.wind_mps":      (   0.0,  25.0,   "m/s",   lambda a,b: round(random.uniform(a,b), 2)),
    "air.pm25":              (   1.0, 150.0,   "µg/m³", lambda a,b: round(random.uniform(a,b), 1)),
    "air.co2":               ( 350.0, 2000.0,  "ppm",   lambda a,b: int(random.uniform(a,b))),
    "light.lux":             (   0.0, 20000.0, "lux",   lambda a,b: round(random.uniform(a,b), 0)),
    "power.watts":           (  40.0,  800.0,  "W",     lambda a,b: round(random.uniform(a,b), 1)),
    "water.ph":              (   6.0,   9.0,   "pH",    lambda a,b: round(random.uniform(a,b), 2)),
    "water.turbidity_ntu":   (   0.1,  50.0,   "NTU",   lambda a,b: round(random.uniform(a,b), 2)),
    "water.temp_c":          (   4.0,  35.0,   "C",     lambda a,b: round(random.uniform(a,b), 2)),

    # Optional ops/dev metrics
    "sys.cpu_pct":           (   3.0,   92.0,  "%",     lambda a,b: round(random.uniform(a,b), 1)),
    "sys.mem_used_mb":       ( 512.0,  8192.0, "MB",    lambda a,b: int(random.uniform(a,b))),
    "net.rtt_ms":            (   5.0,  120.0,  "ms",    lambda a,b: round(random.uniform(a,b), 1)),
    "app.events_per_min":    (   0.0,  300.0,  "ev/m",  lambda a,b: int(random.uniform(a,b))),
    "device.battery_pct":    (   5.0,  100.0,  "%",     lambda a,b: round(random.uniform(a,b), 1)),
}

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def parse_api_version(s: str):
    parts = s.strip().split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts))
    except ValueError:
        raise argparse.ArgumentTypeError("api-version must look like 3.7.0")

def ensure_topic(brokers, topic, partitions, rf, api_version):
    admin = None
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=brokers,
            client_id="sensor-admin",
            api_version=api_version,
        )
        admin.create_topics([NewTopic(name=topic, num_partitions=partitions, replication_factor=rf)])
        print(f"[init] created topic '{topic}' (partitions={partitions}, rf={rf})")
    except TopicAlreadyExistsError:
        print(f"[init] topic '{topic}' already exists")
    except Exception as e:
        print(f"[init] topic create skipped/failed: {e.__class__.__name__}: {e}")
    finally:
        try:
            if admin:
                admin.close()
        except Exception:
            pass

def make_producer(brokers, acks, linger_ms, compression, retries, api_version):
    return KafkaProducer(
        bootstrap_servers=brokers,
        acks=acks,
        linger_ms=linger_ms,
        retries=retries,
        value_serializer=lambda d: json.dumps(d).encode("utf-8"),
        key_serializer=lambda d: str(d).encode("utf-8") if d is not None else None,
        compression_type=None if compression == "none" else compression,
        api_version=api_version,
        api_version_auto_timeout_ms=15000,
        max_in_flight_requests_per_connection=1,
    )

@dataclass
class SensorConfig:
    name: str
    hz: float = 1.0
    burst: int = 1
    lo: Optional[float] = None
    hi: Optional[float] = None
    units: Optional[str] = None

def load_config(path: Optional[str]) -> List[SensorConfig]:
    if not path:
        return []
    with open(path, "r") as f:
        doc = json.load(f)
    out: List[SensorConfig] = []
    for item in doc.get("sensors", []):
        out.append(
            SensorConfig(
                name=item["name"],
                hz=float(item.get("hz", 1.0)),
                burst=int(item.get("burst", 1)),
                lo=item.get("lo"),
                hi=item.get("hi"),
                units=item.get("units"),
            )
        )
    return out

def merge_preset_and_cfg(sc: SensorConfig):
    if sc.name not in PRESETS and (sc.lo is None or sc.hi is None or sc.units is None):
        raise ValueError(f"Unknown preset '{sc.name}' and no lo/hi/units provided in config.")
    if sc.name in PRESETS:
        lo0, hi0, units0, _ = PRESETS[sc.name]
        return (sc.lo if sc.lo is not None else lo0,
                sc.hi if sc.hi is not None else hi0,
                sc.units if sc.units is not None else units0,
                PRESETS[sc.name][3])
    # Custom sensor (not in PRESETS)
    return (sc.lo, sc.hi, sc.units, lambda a,b: round(random.uniform(a,b), 2))

def parse_args():
    ap = argparse.ArgumentParser(description="Config-driven multi-sensor Kafka publisher")
    # K8s-friendly env fallbacks
    ap.add_argument("--brokers", default=os.getenv("BROKERS", "127.0.0.1:9092"),
                    help="Comma-separated bootstrap servers")
    ap.add_argument("--topic", default=os.getenv("TOPIC", "sensors"),
                    help="Shared topic when --topic-mode=shared")
    ap.add_argument("--topic-mode", choices=["shared","per-sensor"], default=os.getenv("TOPIC_MODE","shared"),
                    help="Publish to one shared topic or a topic per sensor")
    ap.add_argument("--create-topic", action="store_true",
                    help="Attempt to create topic(s) on startup")
    ap.add_argument("--partitions", type=int, default=int(os.getenv("PARTITIONS", "3")))
    ap.add_argument("--replication-factor", type=int, default=int(os.getenv("RF", "2")))
    ap.add_argument("--sensor", action="append",
                    help="Repeatable: sensor preset name. If omitted, use config or defaults.")
    ap.add_argument("--config", help="JSON file listing sensors with per-sensor hz/burst and optional lo/hi/units")
    ap.add_argument("--device-id", default=os.getenv("DEVICE_ID", str(uuid.uuid4())))
    ap.add_argument("--source", default=os.getenv("SOURCE", "k8s-job"))
    ap.add_argument("--acks", default=os.getenv("ACKS", "all"), choices=["0","1","all"])
    ap.add_argument("--batch-ms", type=int, default=int(os.getenv("BATCH_MS","50")))
    ap.add_argument("--compression", default=os.getenv("COMPRESSION","none"),
                    choices=["none","gzip","lz4","snappy","zstd"])
    ap.add_argument("--retries", type=int, default=int(os.getenv("RETRIES","5")))
    ap.add_argument("--jitter-ms", type=int, default=int(os.getenv("JITTER_MS","150")))
    ap.add_argument("--log-every", type=int, default=int(os.getenv("LOG_EVERY","1")))
    ap.add_argument("--api-version", type=parse_api_version, default=parse_api_version(os.getenv("API_VERSION","3.7.0")))
    # NEW: stress & control
    ap.add_argument("--rate-multiplier", type=float, default=float(os.getenv("RATE_MULT", "1.0")),
                    help="Multiply every sensor's Hz by this factor")
    ap.add_argument("--duration-sec", type=float, default=float(os.getenv("DURATION_SEC", "0")),
                    help="If >0, exit after this many seconds")
    ap.add_argument("--max-messages", type=int, default=int(os.getenv("MAX_MESSAGES", "0")),
                    help="If >0, exit after sending this many messages")
    ap.add_argument("--seed", type=int, default=int(os.getenv("SEED", "0")),
                    help="Optional RNG seed for reproducibility")
    ap.add_argument("--key-strategy", choices=["sensor", "device", "random"], default=os.getenv("KEY_STRATEGY","sensor"),
                    help="Kafka message key to influence partitioning")
    ap.add_argument("--input", help="Path to CSV file")
    return ap.parse_args()

def main():
    args = parse_args()
    brokers = [b.strip() for b in args.brokers.split(",") if b.strip()]

    if args.seed:
        random.seed(args.seed)

    # Build sensor list:
    sensors_cfg = load_config(args.config)
    if args.sensor:
        # CLI list wins; assign default Hz/Burst for each
        sensors_cfg = [SensorConfig(name=s, hz=1.0, burst=1) for s in args.sensor]
    if not sensors_cfg:
        # sensible defaults showcasing (e)
        sensors_cfg = [
            SensorConfig("weather.temp_c", hz=2.0, burst=1),
            SensorConfig("weather.humidity_pct", hz=1.0, burst=1),
            SensorConfig("air.pm25", hz=0.5, burst=1),
            SensorConfig("weather.pressure_hpa", hz=0.2, burst=1),
            SensorConfig("light.lux", hz=5.0, burst=1),
            SensorConfig("water.ph", hz=0.1, burst=1),
        ]

    
    acks_arg = args.acks
    if acks_arg == "0":
        acks_arg = 0
    elif acks_arg == "1":
        acks_arg = 1

    # Ensure topics:
    if args.create_topic:
        if args.topic_mode == "shared":
            ensure_topic(brokers, args.topic, args.partitions, args.replication_factor, args.api_version)
        else:
            for sc in sensors_cfg:
                ensure_topic(brokers, sc.name, args.partitions, args.replication_factor, args.api_version)

    producer = make_producer(
        brokers=brokers,
        acks=args.acks,
        linger_ms=args.batch_ms,
        compression=args.compression,
        retries=args.retries,
        api_version=args.api_version,
    )


    print(f"[start] brokers={brokers} topic={args.topic} topic_mode={args.topic_mode}")
    if args.input:
        print(f"[start] mode=csv file={args.input} device_id={args.device_id}")
    else:
        print(f"[start] mode=synthetic sensors={[s.name for s in sensors_cfg]} device_id={args.device_id}")

    # counters & stop conditions
    sent_total = 0
    deadline = time.time() + args.duration_sec if args.duration_sec > 0 else None

    # Precompute periods with rate multiplier
    periods = {}
    for s in sensors_cfg:
        eff_hz = max(0.001, s.hz * max(0.001, args.rate_multiplier))
        periods[s.name] = 1.0 / eff_hz
    next_due = {s.name: time.time() for s in sensors_cfg}

    # Using New Publisher Data
    if args.input:
        import csv, uuid
        print(f"[csv] replaying {args.input}")
        with open(args.input, newline="") as f:
            r = csv.reader(f)
            count = 0
            for rid, ts, val, prop, plug, hh, house in r:
                payload = {
                    "id": rid,
                    "timestamp": ts,
                    "value": float(val),
                    "property": int(prop),
                    "plug_id": int(plug),
                    "household_id": int(hh),
                    "house_id": int(house),
                    "device_id": args.device_id,
                    "source": args.source,
                }
                key = f"{payload['house_id']}-{payload['household_id']}-{payload['plug_id']}"
                topic = args.topic if args.topic_mode == "shared" else "debs"
                producer.send(topic, key=key, value=payload)
                count += 1
                if args.max_messages > 0 and count >= args.max_messages:
                    break
        producer.flush()
        producer.close(60) 
        print("[csv] done")
        return

    try:
        while True:
            now = time.time()
            for sc in sensors_cfg:
                if now < next_due[sc.name]:
                    continue
                next_due[sc.name] = now + periods[sc.name]

                lo, hi, units, gen = merge_preset_and_cfg(sc)
                for _ in range(max(1, sc.burst)):
                    value = gen(lo, hi)
                    payload = {
                        "sensorType": sc.name,
                        "value": value,
                        "units": units,
                        "ts": iso_now(),
                        "device_id": args.device_id,
                        "source": args.source
                    }

                    # message key strategy
                    if args.key_strategy == "sensor":
                        key_to_use = sc.name
                    elif args.key_strategy == "device":
                        key_to_use = args.device_id
                    else:
                        key_to_use = uuid.uuid4().hex[:8]

                    topic_to_use = args.topic if args.topic_mode == "shared" else sc.name
                    fut = producer.send(topic_to_use, key=key_to_use, value=payload)
                    fut.add_errback(lambda e: print(f"  ! delivery error: {repr(e)}", file=sys.stderr, flush=True))
                    if args.log_every > 0:
                        print(f"sent {topic_to_use} {payload}", flush=True)
                    if args.jitter_ms > 0:
                        time.sleep(random.uniform(0, args.jitter_ms/1000.0))

                    sent_total += 1
                    if args.max_messages > 0 and sent_total >= args.max_messages:
                        raise KeyboardInterrupt

            # Prevent busy loop + duration stop
            if deadline and time.time() >= deadline:
                raise KeyboardInterrupt
            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\n[stop] closing producer...")
    finally:
        try:
            producer.flush()
            producer.close(60)
        except KafkaError as e:
            print(f"[stop] flush error: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
