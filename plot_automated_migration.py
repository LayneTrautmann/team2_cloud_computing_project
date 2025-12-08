#!/usr/bin/env python3
"""
Parse automated migration log and create CDF plot
"""
import re
import numpy as np
import matplotlib.pyplot as plt

# Parse the log file
log_file = 'automated_migration_final.log'

before_latencies = []
after_latencies = []
migration_triggered = False

with open(log_file, 'r') as f:
    for line in f:
        # Check if migration was triggered
        if 'MIGRATION TRIGGERED' in line:
            migration_triggered = True
            continue

        # Extract latency values
        match = re.search(r'Latency: ([\d.]+)s', line)
        if match:
            latency = float(match.group(1))
            if not migration_triggered:
                before_latencies.append(latency)
            else:
                after_latencies.append(latency)

print(f"Found {len(before_latencies)} iterations BEFORE migration")
print(f"Found {len(after_latencies)} iterations AFTER migration")

# Calculate CDFs
def calculate_cdf(data):
    sorted_data = np.sort(data)
    cdf = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
    return sorted_data, cdf

before_sorted, before_cdf = calculate_cdf(before_latencies)
after_sorted, after_cdf = calculate_cdf(after_latencies)

# Create plot
plt.figure(figsize=(10, 6))
plt.plot(before_sorted, before_cdf, 'r-', linewidth=2.5, label=f'Before Migration (with stress, n={len(before_latencies)})')
plt.plot(after_sorted, after_cdf, 'g-', linewidth=2.5, label=f'After Migration (no stress, n={len(after_latencies)})')

plt.xlabel('Iteration Time (seconds)', fontsize=12)
plt.ylabel('CDF', fontsize=12)
plt.title('PA4 Automated Migration: Tail Latency Improvement', fontsize=14, fontweight='bold')
plt.legend(fontsize=11, loc='lower right')
plt.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('PA4_Automated_Migration_CDF.png', dpi=300, bbox_inches='tight')
print("\nSaved: PA4_Automated_Migration_CDF.png")

# Print statistics
print("\n=== Automated Migration Results ===")
print(f"\nBEFORE Migration (with stress):")
print(f"  Average: {np.mean(before_latencies):.3f}s")
print(f"  Median: {np.median(before_latencies):.3f}s")
print(f"  Min: {np.min(before_latencies):.3f}s")
print(f"  Max: {np.max(before_latencies):.3f}s")

print(f"\nAFTER Migration (no stress):")
print(f"  Average: {np.mean(after_latencies):.3f}s")
print(f"  Median: {np.median(after_latencies):.3f}s")
print(f"  Min: {np.min(after_latencies):.3f}s")
print(f"  Max: {np.max(after_latencies):.3f}s")

improvement = ((np.mean(before_latencies) - np.mean(after_latencies)) / np.mean(before_latencies)) * 100
print(f"\nImprovement: {improvement:.1f}%")
