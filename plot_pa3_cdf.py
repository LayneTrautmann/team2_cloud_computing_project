#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, os, glob
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def ecdf_xy(values: np.ndarray):
    """
    Returns x,y for plotting a CDF with
      x = CDF in [0,1], y = sorted values
    """
    v = np.sort(values)
    n = v.size
    x = np.arange(1, n + 1) / n
    y = v
    return x, y

def label_from_row(row):
    """
    Build a compact experiment label from the first row of a CSV.
    Expected columns: maps, reduces, executors (or similar).
    Falls back to generic label if fields are missing.
    """
    def _get(name, default=None):
        for cand in [name, name.lower(), name.upper()]:
            if cand in row:
                return row[cand]
        return default

    m = _get('maps', _get('M', ''))
    r = _get('reduces', _get('R', ''))
    e = _get('executors', _get('E', ''))
    parts = []
    if m != '': parts.append(f"M{int(m)}")
    if r != '': parts.append(f"R{int(r)}")
    if e != '': parts.append(f"E{int(e)}")
    return "_".join(parts) if parts else "exp"

def percentiles(series, pcts=(50, 90, 95, 99)):
    out = {}
    s = pd.to_numeric(series, errors="coerce").dropna()
    for p in pcts:
        out[f"p{p}"] = float(np.percentile(s, p))
    return out

def main():
    if len(sys.argv) < 3:
        print("Usage: plot_pa3_cdf.py <results_dir> <outfile_prefix>")
        sys.exit(1)

    results_dir = sys.argv[1]
    out_prefix  = sys.argv[2]

    if not os.path.isdir(results_dir):
        print(f"ERROR: results_dir not found: {results_dir}")
        sys.exit(1)

    # Load all results_*.csv from the directory
    files = sorted(glob.glob(os.path.join(results_dir, "results_*.csv")))
    if not files:
        print(f"No CSVs matched in {results_dir} (looking for results_*.csv)")
        sys.exit(1)

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if df.empty:
                continue

            # Normalize likely column names once:
            cols_lower = {c: c.lower() for c in df.columns}
            df.rename(columns=cols_lower, inplace=True)

            # Attach an experiment label derived from first row (maps/reduces/executors)
            first = df.iloc[0].to_dict()
            exp_label = label_from_row(first)
            df["exp_label"] = exp_label
            df["source_csv"] = os.path.basename(f)
            frames.append(df)
        except Exception as ex:
            print(f"WARN: Skipping {f}: {ex}")

    if not frames:
        print("No non-empty CSVs loaded.")
        sys.exit(0)

    all_df = pd.concat(frames, ignore_index=True)

    # We expect timing columns named iter_total_s and mapreduce_s
    for required in ["iter_total_s", "mapreduce_s"]:
        if required not in all_df.columns:
            print(f"ERROR: Expected column '{required}' not found in CSVs.")
            sys.exit(1)

    # ---------------------------
    # Percentile table per experiment (using iter_total_s)
    # ---------------------------
    rows = []
    for exp, grp in all_df.groupby("exp_label", sort=True):
        # require at least 10 iterations to make percentiles meaningful
        s = pd.to_numeric(grp["iter_total_s"], errors="coerce").dropna()
        n = s.size
        if n < 10:
            # still include with whatever is available; change if you prefer to skip
            pass
        pct = percentiles(s, (50, 90, 95, 99))
        rows.append({
            "experiment": exp,
            "n_iters": n,
            **pct
        })

    pct_df = pd.DataFrame(rows).sort_values("experiment").reset_index(drop=True)
    pct_out = f"{out_prefix}_iter_total_percentiles.csv"
    pct_df.to_csv(pct_out, index=False)
    print(f"Wrote percentiles CSV → {pct_out}")

    # ---------------------------
    # Plot: CDF of iter_total_s  (X = CDF, Y = time)
    # ---------------------------
    plt.figure(figsize=(9, 7))
    for exp, grp in all_df.groupby("exp_label", sort=True):
        vals = pd.to_numeric(grp["iter_total_s"], errors="coerce").dropna().to_numpy()
        if vals.size == 0:
            continue
        x, y = ecdf_xy(vals)
        plt.plot(x, y, linewidth=2, label=f"{exp} (n={vals.size})")

    plt.xlabel("CDF (0 → 1)")
    plt.ylabel("Iteration time (s)")
    plt.title("CDF of Iteration Completion Time (iter_total_s)")
    plt.grid(True, alpha=0.3, linestyle="--")
    plt.xlim(0, 1)
    plt.legend(loc="best", fontsize=9)
    out_png_iter = f"{out_prefix}_iter_total_cdf.png"
    plt.tight_layout()
    plt.savefig(out_png_iter, dpi=180)
    print(f"Wrote plot → {out_png_iter}")

    # ---------------------------
    # Plot: CDF of mapreduce_s   (X = CDF, Y = time)
    # ---------------------------
    plt.figure(figsize=(9, 7))
    for exp, grp in all_df.groupby("exp_label", sort=True):
        vals = pd.to_numeric(grp["mapreduce_s"], errors="coerce").dropna().to_numpy()
        if vals.size == 0:
            continue
        x, y = ecdf_xy(vals)
        plt.plot(x, y, linewidth=2, label=f"{exp} (n={vals.size})")

    plt.xlabel("CDF (0 → 1)")
    plt.ylabel("MapReduce compute+write time (s)")
    plt.title("CDF of MapReduce Phase (mapreduce_s)")
    plt.grid(True, alpha=0.3, linestyle="--")
    plt.xlim(0, 1)
    plt.legend(loc="best", fontsize=9)
    out_png_map = f"{out_prefix}_mapreduce_cdf.png"
    plt.tight_layout()
    plt.savefig(out_png_map, dpi=180)
    print(f"Wrote plot → {out_png_map}")

if __name__ == "__main__":
    main()

