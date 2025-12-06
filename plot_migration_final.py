#!/usr/bin/env python3
"""
Plot CDF comparing BEFORE and AFTER migration (100 iterations each)
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Read the two 100-iteration experiments
before = pd.read_csv('pa4_results/migration_comparison/results_100iter_before_migration.csv')
after = pd.read_csv('pa4_results/migration_comparison/results_100iter_after_migration.csv')

# Get iteration total times
before_times = before['iter_total_s'].values
after_times = after['iter_total_s'].values

# Calculate CDFs
def calculate_cdf(data):
    sorted_data = np.sort(data)
    cdf = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
    return sorted_data, cdf

before_sorted, before_cdf = calculate_cdf(before_times)
after_sorted, after_cdf = calculate_cdf(after_times)

# Create plot
plt.figure(figsize=(10, 6))
plt.plot(before_sorted, before_cdf, 'r-', linewidth=2, label=f'Before Migration (w/ stress, n={len(before_times)})')
plt.plot(after_sorted, after_cdf, 'g-', linewidth=2, label=f'After Migration (clean node, n={len(after_times)})')

plt.xlabel('Iteration Time (seconds)', fontsize=12)
plt.ylabel('CDF', fontsize=12)
plt.title('PA4 Manual Migration: Tail Latency Improvement', fontsize=14, fontweight='bold')
plt.legend(fontsize=11)
plt.grid(True, alpha=0.3)

# Add percentile lines
for percentile in [50, 90, 95, 99]:
    before_val = np.percentile(before_times, percentile)
    after_val = np.percentile(after_times, percentile)
    plt.axhline(y=percentile/100, color='gray', linestyle='--', alpha=0.3, linewidth=0.8)

plt.tight_layout()
plt.savefig('PA4_Migration_Final_CDF.png', dpi=300, bbox_inches='tight')
print("Saved: PA4_Migration_Final_CDF.png")

# Print statistics
print("\n=== 100-Iteration Migration Results ===")
print(f"\nBEFORE Migration (with stress on cloud-c1-w6):")
print(f"  Average: {before_times.mean():.3f}s")
print(f"  Median (p50): {np.percentile(before_times, 50):.3f}s")
print(f"  p90: {np.percentile(before_times, 90):.3f}s")
print(f"  p95: {np.percentile(before_times, 95):.3f}s")
print(f"  p99: {np.percentile(before_times, 99):.3f}s")
print(f"  Max: {before_times.max():.3f}s")

print(f"\nAFTER Migration (clean node cloud-c1-w17):")
print(f"  Average: {after_times.mean():.3f}s")
print(f"  Median (p50): {np.percentile(after_times, 50):.3f}s")
print(f"  p90: {np.percentile(after_times, 90):.3f}s")
print(f"  p95: {np.percentile(after_times, 95):.3f}s")
print(f"  p99: {np.percentile(after_times, 99):.3f}s")
print(f"  Max: {after_times.max():.3f}s")

print(f"\nImprovement:")
print(f"  Average: {((before_times.mean() - after_times.mean()) / before_times.mean() * 100):.1f}%")
print(f"  Tail (max): {((before_times.max() - after_times.max()) / before_times.max() * 100):.1f}%")
print(f"  p99: {((np.percentile(before_times, 99) - np.percentile(after_times, 99)) / np.percentile(before_times, 99) * 100):.1f}%")
