#!/usr/bin/env python3
import sys, time, argparse
from datetime import datetime
from pyspark.sql import SparkSession, Row
from pyspark import StorageLevel

def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

def get_args():
    p = argparse.ArgumentParser(description="Smart House MapReduce using RDDs (Mongo v10.x)")
    p.add_argument("--iters", type=int, default=3, help="Iterations per experiment")
    p.add_argument("--M", type=int, default=50, help="Number of map tasks (target partitions)")
    p.add_argument("--R", type=int, default=5, help="Number of reduce tasks")
    p.add_argument("--collections", type=str, required=True,
                   help="Comma-separated shard collection names (e.g. readings_shard1,...,readings_shard5)")
    p.add_argument("--writeMode", type=str, default="append", choices=["append","overwrite"])
    p.add_argument("--readUri", type=str, default="mongodb://mongo-svc:27017", help="Mongo read URI")
    p.add_argument("--writeUri", type=str, default="mongodb://mongo-svc:27017", help="Mongo write URI")
    return p.parse_args()

def spark_session(app="SmartHouseRDD"):
    return (SparkSession.builder
            .appName(app)
            # session-level Mongo URIs (v10.x)
            .config("spark.mongodb.read.connection.uri", "mongodb://mongo-svc:27017")
            .config("spark.mongodb.write.connection.uri", "mongodb://mongo-svc:27017")
            # helpful shuffle tuning for classroom runs
            .config("spark.sql.adaptive.enabled", "true")
            .getOrCreate())

def df_shard(spark, db, coll, read_uri):
    return (spark.read
            .format("mongodb")            .option("connection.uri", read_uri)
            .option("database", db)
            .option("collection", coll)
            .load())




def rdd_from_shards(spark, collections, read_uri, prop_filter):
    """
    prop_filter: 1 for load, 0 for work
    Returns an RDD of tuples we need:
      (house_id, household_id, plug_id, value)
    """
    rdds = []
    for coll in collections:
        df = (df_shard(spark, "sensors", coll, read_uri)
              .select("house_id", "household_id", "plug_id", "property", "value")
              .where("property IS NOT NULL AND value IS NOT NULL AND "
                     "house_id IS NOT NULL AND household_id IS NOT NULL AND plug_id IS NOT NULL AND "
                     f"property = {int(prop_filter)}"))
        rdds.append(df.rdd.map(lambda r: (int(r.house_id), int(r.household_id), int(r.plug_id), float(r.value))))
    if not rdds:
        return spark.sparkContext.emptyRDD()
    return rdds[0] if len(rdds) == 1 else spark.sparkContext.union(rdds)





def write_df_to_mongo(df, db, coll, write_uri, mode):
    (df.write
       .format("mongodb")
       .mode(mode)
       .option("connection.uri", write_uri)
       .option("database", db)
       .option("collection", coll)
       .save())

def avg_load_per_plug_rdd(sc, rdd, R):
    """
    Input rdd: (house_id, household_id, plug_id, load_value)
    Output RDD: (house_id, household_id, plug_id, avg_load)
    """
    keyed = rdd.map(lambda x: ((x[0], x[1], x[2]), (x[3], 1.0)))
    agg = (keyed
           .repartition(R)   # ensure reducers
           .reduceByKey(lambda a, b: (a[0] + b[0], a[1] + b[1])))
    return agg.map(lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[1][0] / kv[1][1]))

def total_work_per_house_rdd(sc, rdd_work, R):
    """
    Input rdd_work: (house_id, household_id, plug_id, work_value_accumulating)
    Compute per-plug delta = max(work) - min(work), then sum deltas by house.
    Output RDD: (house_id, total_work_Wh)
    """
    # per plug min/max
    plug_keyed = rdd_work.map(lambda x: ((x[0], x[1], x[2]), (x[3], x[3])))   # (min, max) init
    mm = plug_keyed.aggregateByKey(
        (float("+inf"), float("-inf")),
        lambda acc, v: (min(acc[0], v[0]), max(acc[1], v[1])),
        lambda a, b: (min(a[0], b[0]), max(a[1], b[1]))
    )
    plug_delta = mm.map(lambda kv: (kv[0], max(0.0, kv[1][1] - kv[1][0])))
    # sum by house
    by_house = (plug_delta
                .map(lambda kv: (kv[0][0], kv[1]))  # house_id -> delta
                .repartition(R)
                .reduceByKey(lambda a, b: a + b))
    return by_house

def print_executors(sc):
    # Use Spark's JVM (Py4J) APIs but iterate via a Java iterator
    # to avoid .toArray() / .length() issues.
    jsc = sc._jsc
    jmap = jsc.sc().getExecutorMemoryStatus()   # Java Map[BlockManagerId,String]
    it = jmap.keySet().iterator()               # Java Iterator over BlockManagerId
    executors = []
    while it.hasNext():
        addr = str(it.next())                   # e.g., BlockManagerId(1, 10.244.x.y,7078, None)
        if "driver" not in addr:
            executors.append(addr)
    log(f"Executors in cluster: {len(executors)}")
    for e in executors:
        log(f"  - {e}")
    return len(executors)



def main():

    args = get_args()
    shards = [s.strip() for s in args.collections.split(",") if s.strip()]
    spark = spark_session("SmartHouseRDD (M={}, R={})".format(args.M, args.R))
    sc = spark.sparkContext

    # Prove executors exist (utilization proof helper)
    num_exec = print_executors(sc)
    log("RDD target partitions (maps) = {}, reducers (R) = {}".format(args.M, args.R))

    # Warm-up read (optional)
    log("Warming up DataSource and Mongo connections...")
    _ = df_shard(spark, "sensors", shards[0], args.readUri).limit(1).count()

    # PA4: Pre load data - outside the loop
    log("Pre loading data from MongoDB")
    rdd_load = rdd_from_shards(spark, shards, args.readUri, prop_filter=1)
    rdd_load = rdd_load.repartition(args.M).persist(StorageLevel.MEMORY_ONLY)
    _ = rdd_load.count()  # materialize

    rdd_work = rdd_from_shards(spark, shards, args.readUri, prop_filter=0)
    rdd_work = rdd_work.repartition(args.M).persist(StorageLevel.MEMORY_ONLY)
    _ = rdd_work.count()  # materialize
    log("Data pre-loaded")

    results_summary = []  # rows for the printed table

    for it in range(1, args.iters + 1):
        log(f"=== Iteration {it} START ===")

        # PA4: Data loaded get time
        t0 = time.time()
        t_load_in = t0
        t_work_in = t0

        # Compute avg_load_per_plug
        rdd_avg = avg_load_per_plug_rdd(sc, rdd_load, args.R).persist(StorageLevel.MEMORY_ONLY)
        avg_count = rdd_avg.count()
        df_avg = spark.createDataFrame(rdd_avg.map(lambda x: Row(
            house_id=int(x[0]), household_id=int(x[1]), plug_id=int(x[2]), avg_load=float(x[3])
        )))
        write_df_to_mongo(df_avg, "analytics", "avg_load_per_plug", args.writeUri, args.writeMode)
        t_avg = time.time()

        # Compute total_work_per_house
        rdd_twh = total_work_per_house_rdd(sc, rdd_work, args.R).persist(StorageLevel.MEMORY_ONLY)
        twh_count = rdd_twh.count()
        df_twh = spark.createDataFrame(rdd_twh.map(lambda x: Row(
            house_id=int(x[0]), total_work_Wh=float(x[1])
        )))
        write_df_to_mongo(df_twh, "analytics", "total_work_per_house", args.writeUri, args.writeMode)
        t_twh = time.time()

        # Optional overall summaries
        rdd_avg_all = (rdd_load.map(lambda x: (1, (x[3], 1.0)))
                              .reduceByKey(lambda a, b: (a[0] + b[0], a[1] + b[1]))
                              .mapValues(lambda s: s[0] / s[1]))
        overall_avg = rdd_avg_all.collect()[0][1] if not rdd_avg_all.isEmpty() else None

        rdd_total_work_all = (rdd_work.map(lambda x: (1, x[3]))
                                     .aggregateByKey((float("+inf"), float("-inf")),
                                                     lambda acc, v: (min(acc[0], v), max(acc[1], v)),
                                                     lambda a, b: (min(a[0], b[0]), max(a[1], b[1])))
                                     .mapValues(lambda mm: max(0.0, mm[1] - mm[0])))
        overall_twh = rdd_total_work_all.collect()[0][1] if not rdd_total_work_all.isEmpty() else None

        # Row for table
        results_summary.append({
            "iter": it,
            "maps": args.M,
            "reduces": args.R,
            "rdd_load_in_s": round(t_load_in - t0, 3),
            "rdd_work_in_s": round(t_work_in - t_load_in, 3),
            "avg_reduce_s": round(t_avg - t_work_in, 3),
            "twh_reduce_s": round(t_twh - t_avg, 3),
            "avg_count": int(avg_count),
            "twh_count": int(twh_count),
            "overall_avg_load": None if overall_avg is None else round(overall_avg, 6),
            "overall_total_work_Wh": None if overall_twh is None else round(overall_twh, 6),
            "executors": num_exec
        })

        log(f"=== Iteration {it} END ===")

        rdd_avg.unpersist(False)
        rdd_twh.unpersist(False)

    # PA4: Unpersist shared RDDs
    rdd_load.unpersist(False)
    rdd_work.unpersist(False)

    # Pretty print a simple table to stdout (CSV-like)
    cols = ["iter","maps","reduces","executors",
            "rdd_load_in_s","rdd_work_in_s","avg_reduce_s","twh_reduce_s",
            "avg_count","twh_count","overall_avg_load","overall_total_work_Wh"]
    header = " | ".join(f"{c:>20}" for c in cols)
    sep = "-" * len(header)
    print("\n" + header)
    print(sep)
    for row in results_summary:
        print(" | ".join(f"{str(row[c]):>20}" for c in cols))

    # PA4: Save CSV for CDF plotting (format: iteration, exec_time, response_time)
    csv_file = "pa4_results.csv"
    log(f"Saving results to {csv_file}")
    with open(csv_file, 'w') as f:
        for row in results_summary:
            exec_time = row["avg_reduce_s"] + row["twh_reduce_s"]
            f.write(f"{row['iter']}, {exec_time}, {exec_time}\n")

    spark.stop()

if __name__ == "__main__":
    main()
