"""
experiment.py - Compare the dispatching rules with a proper simulation experiment.

For each rule: 30 replications. One replication = 1 day warm-up + 1 week of measured production.
Every result is shown as: mean +- half-width of the 95% confidence interval.
"""
import statistics

import matplotlib.pyplot as plt
import simpy
from matplotlib.lines import Line2D
from scipy import stats

from model import ProductionLine
from rules import RULES

WARMUP = 24 * 60            # 1 day. The line starts empty, so the first day is not typical.
RUN_LENGTH = 7 * 24 * 60    # 1 week of measured production
REPLICATIONS = 30
# The three objectives for the multi-objective comparison, and their direction.
OBJECTIVES = {"Throughput (parts/week)": "max", "Avg tardiness (min)": "min", "Energy cost (EUR/week)": "min"}
BLUE, GRAY, INK = "#2a78d6", "#8a8984", "#52514e"   # plot colors: Pareto front, dominated, text


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


def dominates(results, a, b):
    """Rule a dominates rule b if a is significantly better on at least one objective and not
    significantly worse on any objective. 'Significantly' = paired 95% CI does not contain 0,
    so small differences that are only random noise do not count."""
    better_somewhere = False
    for metric, goal in OBJECTIVES.items():
        sign = 1 if goal == "min" else -1    # after this, a positive difference = a is worse
        diffs = [sign * (x[metric] - y[metric]) for x, y in zip(results[a], results[b])]
        mean, hw = confidence_interval(diffs)
        if mean - hw > 0:
            return False                     # a is significantly worse here
        if mean + hw < 0:
            better_somewhere = True          # a is significantly better here
    return better_somewhere


def pareto_front(results):
    """The rules that no other rule dominates."""
    return [a for a in results if not any(dominates(results, b, a) for b in results if b != a)]


def plot_tradeoff(results, front, filename="tradeoff.png"):
    """Left: tardiness vs energy cost (the real trade-off). Right: throughput (equal for all rules)."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [2.2, 1]})
    points = {}
    for name, reps in results.items():
        x, x_hw = confidence_interval([r["Avg tardiness (min)"] for r in reps])
        y, y_hw = confidence_interval([r["Energy cost (EUR/week)"] for r in reps])
        points[name] = (x, y)
        color = BLUE if name in front else GRAY
        ax1.errorbar(x, y, xerr=x_hw, yerr=y_hw, fmt="none", ecolor=color, elinewidth=1, alpha=0.4)
        ax1.plot(x, y, "o", ms=8, color=color)
        ax1.annotate(name, (x, y), xytext=(6, 9), textcoords="offset points", color=INK, fontsize=10)
    ax1.plot(*zip(*sorted(points[n] for n in front)), "--", color=BLUE, lw=1.5)   # connect the front
    ax1.set(xlabel="Average tardiness (min)", ylabel="Energy cost (EUR/week)",
            title="Tardiness vs energy cost (lower left is better)")
    ax1.legend(handles=[Line2D([], [], color=BLUE, marker="o", ls="--", label="Pareto front"),
                        Line2D([], [], color=GRAY, marker="o", ls="", label="Dominated")], frameon=False)

    names = list(results)
    for i, name in enumerate(names):
        m, hw = confidence_interval([r["Throughput (parts/week)"] for r in results[name]])
        ax2.errorbar(m, i, xerr=hw, fmt="o", ms=7, color=BLUE if name in front else GRAY, elinewidth=1)
    ax2.set_yticks(range(len(names)), names)
    ax2.invert_yaxis()
    ax2.set(xlabel="Throughput (parts/week)", title="Throughput: no significant difference")

    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.25)
    fig.text(0.01, 0.01, f"Mean ± 95% CI, {REPLICATIONS} replications of 1 week each", color=INK, fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(filename, dpi=150)
    print(f"\nPlot saved to {filename}")


if __name__ == "__main__":
    results = run_experiment()
    print_table(results)
    print_paired(results, "ECO(0)", "EDD")
    print_paired(results, "SPT", "EDD")
    front = pareto_front(results)
    print("\nPareto-optimal rules:", ", ".join(front))
    plot_tradeoff(results, front)
