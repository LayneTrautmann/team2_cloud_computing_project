#!/usr/bin/env python3
import argparse, json, os, random, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError, KafkaError

SENSOR_PRESETS = {
    "weather.temp_c":       (18.0, 32.0, "C",     lambda a,b: round(random.uniform(a,b), 2)),
    "weather.humidity_pct": (35.0, 85.0, "%",     lambda a,b: round(random.uniform(a,b), 1)),
    "air.pm25":             (4.0, 120.0, "µg/m³", lambda a,b: round(random.uniform(a,b), 1)),
    "air.co2":              (400, 1800, "ppm",    lambda a,b: int(random.uniform(a,b))),
    "vibration.g":          (0.01, 2.0, "g",      lambda a,b: round(random.uniform(a,b), 3)),
    "power.watts":          (40.0, 800.0, "W",    lambda a,b: round(random.uniform(a,b), 1)),
}

DEFAULT_BROKERS = os.getenv("PUBLISHER_BROKERS", "127.0.0.1:29091,127.0.0.1:29092")
DEFAULT_SOURCE = os.getenv("PUBLISHER_SOURCE", "laptop")
DEFAULT_DEVICE_ID = os.getenv("DEVICE_ID", str(uuid.uuid4()))
DEFAULT_PROFILE = os.getenv("PUBLISHER_PROFILE")
DEFAULT_TOPICS_ENV = os.getenv("PUBLISHER_TOPICS")


@dataclass
class TopicSchedule:
    name: str
    sensors: Sequence[str]
    hz: float
    burst: int
    jitter_ms: int
    next_due: float = field(default_factory=lambda: time.time())
    counter: int = 0

    @property
    def period(self) -> float:
        return 1.0 / max(self.hz, 0.001)

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def parse_api_version(s: str):
    parts = s.strip().split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts))
    except ValueError:
        raise argparse.ArgumentTypeError("api-version must look like 3.7.0")

def ensure_topic(brokers, topic, partitions, rf, api_version):
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

def parse_topic_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [t.strip() for t in str(raw).split(",") if t.strip()]

def validate_sensors(sensors: Sequence[str]) -> List[str]:
    validated = []
    for sensor in sensors:
        if sensor not in SENSOR_PRESETS:
            raise ValueError(f"Unknown sensor '{sensor}'. Valid choices: {', '.join(SENSOR_PRESETS.keys())}")
        validated.append(sensor)
    return validated

def load_profile(path: Path, default_sensors: Sequence[str], default_hz: float, default_burst: int, default_jitter: int) -> List[TopicSchedule]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"[profile] File not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[profile] Invalid JSON in {path}: {exc}") from exc

    workloads: List[TopicSchedule] = []
    topics = data.get("topics", [])
    if not isinstance(topics, list) or not topics:
        raise SystemExit(f"[profile] Expected 'topics' list in {path}")

    for entry in topics:
        if not isinstance(entry, dict):
            raise SystemExit("[profile] Each topic entry must be an object")
        name = entry.get("name")
        if not name:
            raise SystemExit("[profile] Topic entry is missing 'name'")
        sensors = entry.get("sensors", default_sensors)
        try:
            sensors = validate_sensors(sensors)
        except ValueError as exc:
            raise SystemExit(f"[profile] {exc}") from exc

        hz = float(entry.get("hz", default_hz))
        burst = int(entry.get("burst", default_burst))
        jitter_ms = int(entry.get("jitter_ms", default_jitter))
        workloads.append(TopicSchedule(name=name, sensors=sensors, hz=hz, burst=burst, jitter_ms=jitter_ms))
    return workloads

def build_workloads(
    topics: Sequence[str],
    profile_path: Optional[str],
    default_sensors: Sequence[str],
    default_hz: float,
    default_burst: int,
    default_jitter: int,
) -> List[TopicSchedule]:
    if profile_path:
        return load_profile(Path(profile_path), default_sensors, default_hz, default_burst, default_jitter)

    if not topics:
        topics = ["utilizations"]

    workloads = []
    validated_sensors = validate_sensors(default_sensors)
    for topic in topics:
        workloads.append(
            TopicSchedule(
                name=topic,
                sensors=validated_sensors,
                hz=default_hz,
                burst=default_burst,
                jitter_ms=default_jitter,
            )
        )
    return workloads

def send_for_workload(
    producer: KafkaProducer,
    workload: TopicSchedule,
    device_id: str,
    source: str,
    log_every: int,
    counter: int,
) -> int:
    for sensor_name in workload.sensors:
        lo, hi, units, gen = SENSOR_PRESETS[sensor_name]
        for _ in range(workload.burst):
            payload = {
                "sensor_type": sensor_name,
                "value": gen(lo, hi),
                "units": units,
                "ts": iso_now(),
                "device_id": device_id,
                "source": source,
            }
            fut = producer.send(workload.name, key=sensor_name, value=payload)
            fut.add_callback(delivery_report)
            fut.add_errback(lambda e: print(f"  ! delivery error: {repr(e)}", file=sys.stderr, flush=True))
            counter += 1
            workload.counter += 1
            if log_every > 0 and (counter % log_every == 0):
                print(f"[{workload.name}] sent {payload}", flush=True)
            if workload.jitter_ms > 0:
                time.sleep(random.uniform(0, workload.jitter_ms / 1000.0))
    return counter

def parse_args():
    ap = argparse.ArgumentParser(
        description="Realistic multi-sensor publisher that sends timestamped JSON to a Kafka broker."
    )
    ap.add_argument(
        "--brokers",
        default=DEFAULT_BROKERS,
        help=f"Comma-separated bootstrap servers (default/env PUBLISHER_BROKERS: {DEFAULT_BROKERS})",
    )
    ap.add_argument(
        "--topic",
        dest="topics",
        action="append",
        default=[],
        help="Repeatable topic name. Provide multiple --topic flags to publish to several topics.",
    )
    ap.add_argument("--create-topic", action="store_true", help="Attempt to create topic on startup.")
    ap.add_argument("--partitions", type=int, default=3, help="Topic partitions if creating (default 3)")
    ap.add_argument("--replication-factor", type=int, default=2, help="Topic RF if creating (default 2)")
    ap.add_argument("--sensor", action="append",
                    help="Repeatable: sensor name. Defaults to a preset mix. Choices: " + ", ".join(SENSOR_PRESETS.keys()))
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="Path to JSON profile that describes per-topic workloads.")
    ap.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    ap.add_argument("--source", default=DEFAULT_SOURCE)
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

def main() -> int:
    args = parse_args()

    topic_list: List[str] = list(args.topics or [])
    topic_list.extend(parse_topic_list(DEFAULT_TOPICS_ENV))

    default_sensors = args.sensor or ["weather.temp_c", "air.pm25", "power.watts"]

    try:
        workloads = build_workloads(
            topics=topic_list,
            profile_path=args.profile,
            default_sensors=default_sensors,
            default_hz=args.hz,
            default_burst=args.burst,
            default_jitter=args.jitter_ms,
        )
    except ValueError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 2

    brokers = [b.strip() for b in args.brokers.split(",") if b.strip()]
    if not brokers:
        print("[config] No valid brokers specified", file=sys.stderr)
        return 2

    if args.create_topic:
        for workload in workloads:
            ensure_topic(brokers, workload.name, args.partitions, args.replication_factor, args.api_version)

    producer = make_producer(
        brokers=brokers,
        acks=args.acks,
        linger_ms=args.batch_ms,
        compression=args.compression,
        retries=args.retries,
        api_version=args.api_version,
    )

    now = time.time()
    for workload in workloads:
        workload.next_due = now

    print(f"[start] brokers={brokers}")
    print(f"[start] device_id={args.device_id} source={args.source}")
    for workload in workloads:
        print(
            f"[start] topic={workload.name} hz={workload.hz} burst={workload.burst} "
            f"sensors={list(workload.sensors)} jitter_ms={workload.jitter_ms}"
        )

    counter = 0
    min_period = min(w.period for w in workloads)
    sleep_cap = min(1.0, max(0.05, min_period / 4.0))

    try:
        while True:
            now = time.time()
            for workload in workloads:
                if now + 1e-6 >= workload.next_due:
                    counter = send_for_workload(
                        producer=producer,
                        workload=workload,
                        device_id=args.device_id,
                        source=args.source,
                        log_every=args.log_every,
                        counter=counter,
                    )
                    workload.next_due = now + workload.period

            next_due = min(w.next_due for w in workloads)
            wait = next_due - time.time()
            if wait > 0:
                time.sleep(min(wait, sleep_cap))
    except KeyboardInterrupt:
        print("\n[stop] closing producer...")
    finally:
        try:
            producer.flush(10)
        except KafkaError as e:
            print(f"[stop] flush error: {e}", file=sys.stderr)
        producer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
