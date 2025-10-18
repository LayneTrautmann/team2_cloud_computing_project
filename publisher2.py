#!/usr/bin/env python3
import argparse, json, random, time, uuid, sys, hashlib
from datetime import datetime, timezone
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError, KafkaError

# -------------------- Sensors (NO weather.*) --------------------
# name -> (lo, hi, units, generator)
SENSOR_PRESETS = {
    "sys.cpu_pct":        (3.0, 92.0,  "%",     lambda a,b: round(random.uniform(a,b), 1)),
    "sys.mem_used_mb":    (512, 8192,  "MB",    lambda a,b: int(random.uniform(a,b))),
    "net.rtt_ms":         (5.0, 120.0, "ms",    lambda a,b: round(random.uniform(a,b), 1)),
    "app.events_per_min": (0,   300,   "ev/m",  lambda a,b: int(random.uniform(a,b))),
    "camera.motion":      (0,   1,     "bool",  lambda a,b: bool(int(random.uniform(a,b+1)))),
    "device.battery_pct": (5.0, 100.0, "%",     lambda a,b: round(random.uniform(a,b), 1)),
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

def delivery_report(metadata):
    try:
        print(f"  ↳ delivered to partition={metadata.partition} offset={metadata.offset}", flush=True)
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
        compression_type=compression if compression != "none" else None,
        api_version=api_version,
        api_version_auto_timeout_ms=15000,
        max_in_flight_requests_per_connection=1,
    )

def parse_args():
    ap = argparse.ArgumentParser(
        description="Non-overlapping multi-sensor publisher (no weather.*); mirrors publisher.py CLI."
    )
    ap.add_argument(
        "--brokers",
        default="127.0.0.1:29091,127.0.0.1:29092",
        help="Comma-separated bootstrap servers (default: 127.0.0.1:29091,127.0.0.1:29092)",
    )
    ap.add_argument("--topic", default="utilizations")
    ap.add_argument("--create-topic", action="store_true", help="Attempt to create topic on startup.")
    ap.add_argument("--partitions", type=int, default=3, help="Topic partitions if creating (default 3)")
    ap.add_argument("--replication-factor", type=int, default=2, help="Topic RF if creating (default 2)")
    ap.add_argument("--sensor", action="append",
                    help="Repeatable: sensor name. Choices: " + ", ".join(SENSOR_PRESETS.keys()))
    ap.add_argument("--device-id", default=str(uuid.uuid4()))
    ap.add_argument("--source", default="laptop")
    ap.add_argument("--hz", type=float, default=1.0, help="Messages/sec per sensor (default 1.0)")
    ap.add_argument("--burst", type=int, default=1, help="Messages per tick per sensor (default 1)")
    ap.add_argument("--jitter-ms", type=int, default=150, help="Random jitter between sends (ms)")
    ap.add_argument("--acks", default="all", choices=["0","1","all"])
    ap.add_argument("--batch-ms", type=int, default=50, help="linger/batch time (ms)")
    ap.add_argument("--compression", default="none", choices=["none","gzip","lz4","snappy","zstd"])
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--log-every", type=int, default=1, help="Print every N sends (default every message)")
    ap.add_argument("--api-version", type=parse_api_version, default=parse_api_version("3.7.0"),
                    help="Kafka API version to use (default 3.7.0). Example: 3.7.0")
    return ap.parse_args()

def main():
    args = parse_args()

    # Pick a default set that’s clearly not weather.*
    sensors = args.sensor or ["sys.cpu_pct", "net.rtt_ms", "app.events_per_min"]
    for s in sensors:
        if s not in SENSOR_PRESETS:
            print(f"Unknown sensor '{s}'. Valid: {', '.join(SENSOR_PRESETS.keys())}", file=sys.stderr)
            sys.exit(2)

    brokers = [b.strip() for b in args.brokers.split(",") if b.strip()]

    if args.create_topic:
        ensure_topic(brokers, args.topic, args.partitions, args.replication_factor, args.api_version)

    producer = make_producer(
        brokers=brokers,
        acks=args.acks,
        linger_ms=args.batch_ms,
        compression=args.compression,
        retries=args.retries,
        api_version=args.api_version,
    )

    print(f"[start] publishing to topic='{args.topic}' brokers={brokers}")
    print(f"[start] sensors={sensors} device_id={args.device_id}")

    period = 1.0 / max(args.hz, 0.001)
    counter = 0
    try:
        while True:
            t0 = time.time()
            for name in sensors:
                lo, hi, units, gen = SENSOR_PRESETS[name]
                for _ in range(args.burst):
                    value = gen(lo, hi)
                    payload = {
                        "sensor_type": name,
                        "value": value if not isinstance(value, bool) else int(value),  # booleans -> 0/1 for consistency
                        "units": units,
                        "ts": iso_now(),
                        "device_id": args.device_id,
                        "source": args.source
                    }
                    fut = producer.send(args.topic, key=name, value=payload)
                    fut.add_callback(delivery_report)
                    fut.add_errback(lambda e: print(f"  ! delivery error: {repr(e)}", file=sys.stderr, flush=True))
                    counter += 1
                    if args.jitter_ms > 0:
                        time.sleep(random.uniform(0, args.jitter_ms/1000.0))
                    if args.log_every > 0 and (counter % args.log_every == 0):
                        print(f"sent {payload}", flush=True)

            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
    except KeyboardInterrupt:
        print("\n[stop] closing producer...")
    finally:
        try:
            producer.flush(10)
        except KafkaError as e:
            print(f"[stop] flush error: {e}", file=sys.stderr)
        producer.close()

if __name__ == "__main__":
    main()

