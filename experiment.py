"""
experiment.py - Compare the dispatching rules with a proper simulation experiment.

For each rule: 30 replications. One replication = 1 day warm-up + 1 week of measured production.
Every result is shown as: mean +- half-width of the 95% confidence interval.
"""
import statistics

import simpy
from scipy import stats

from model import ProductionLine
from rules import RULES

WARMUP = 24 * 60            # 1 day. The line starts empty, so the first day is not typical.
RUN_LENGTH = 7 * 24 * 60    # 1 week of measured production
REPLICATIONS = 30


def run_replication(rule, seed):
    """One simulation run with one rule and one seed. Returns the performance measures."""
    env = simpy.Environment()
    line = ProductionLine(env, rule=rule, seed=seed)
    env.run(until=WARMUP)
    line.reset_stats()                      # throw away the warm-up data
    env.run(until=WARMUP + RUN_LENGTH)
    for m in line.machines:
        m.set_state(m.state)                # close the last open period

    done = line.finished
    bottleneck = line.machines[1]           # M2
    return {
        "Throughput (parts/week)": len(done),
        "Avg lead time (min)": statistics.mean(p.finish_time - p.arrival_time for p in done),
        "Avg tardiness (min)": statistics.mean(max(0.0, p.finish_time - p.due_date) for p in done),
        "Utilization M2 (%)": 100 * bottleneck.time_in_state["busy"] / RUN_LENGTH,
        "Energy cost (EUR/week)": sum(m.energy_cost for m in line.machines),
    }


def confidence_interval(values, confidence=0.95):
    """Return (mean, half-width). Half-width = t * s / sqrt(n), with the t-distribution
    because we estimate the standard deviation s from only n = 30 values."""
    n = len(values)
    t = stats.t.ppf((1 + confidence) / 2, n - 1)
    return statistics.mean(values), t * statistics.stdev(values) / n ** 0.5


def run_experiment():
    """Run all rules. Returns {rule name: [one result dict per replication]}.
    Seeds 1..30 are the same for every rule (common random numbers),
    so every rule sees the same parts and the same breakdowns."""
    return {name: [run_replication(rule, seed) for seed in range(1, REPLICATIONS + 1)]
            for name, rule in RULES.items()}


def print_table(results):
    """Print mean +- 95% CI half-width as a Markdown table (can be pasted into the README)."""
    metrics = list(results["FIFO"][0])
    print("\n| Rule | " + " | ".join(metrics) + " |")
    print("|---" * (len(metrics) + 1) + "|")
    for name, reps in results.items():
        cells = [confidence_interval([r[metric] for r in reps]) for metric in metrics]
        print(f"| {name} | " + " | ".join(f"{mean:.1f} ± {hw:.1f}" for mean, hw in cells) + " |")


def print_paired(results, rule_a, rule_b):
    """Paired comparison: CI of the difference (rule_a - rule_b) replication by replication.
    Because both rules used the same seeds, this CI is much narrower than the separate CIs.
    If the interval does not contain 0, the difference is statistically significant."""
    print(f"\nPaired difference {rule_a} - {rule_b}:")
    for metric in results[rule_a][0]:
        diffs = [a[metric] - b[metric] for a, b in zip(results[rule_a], results[rule_b])]
        mean, hw = confidence_interval(diffs)
        significant = "significant" if abs(mean) > hw else "not significant"
        print(f"  {metric:<25} {mean:8.1f} ± {hw:.1f}  ({significant})")


if __name__ == "__main__":
    results = run_experiment()
    print_table(results)
    print_paired(results, "ECO", "EDD")
    print_paired(results, "SPT", "EDD")
