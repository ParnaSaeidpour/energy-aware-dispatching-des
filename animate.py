"""
animate.py - Make an animated GIF of the production line: EDD (top) vs ECO(-120) (bottom).

Both lines get the same parts and the same breakdowns (same seed). The GIF shows one day
from 06:00 to 22:00, one picture every 5 minutes. Run:  python animate.py  -> line.gif
"""
import matplotlib.pyplot as plt
import simpy
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Rectangle

import model
from rules import RULES

SEED = 5
SHOWN = ["EDD", "ECO(-120)"]
DAY = 24 * 60
START, END, STEP = 6 * 60, 22 * 60, 5      # minutes after midnight of day 2
STATE_COLOR = {"busy": "#2c8a55", "idle": "#a2aaae", "broken": "#cf3b36", "waiting": "#6c55c9"}
STATE_TEXT = {"busy": "working", "idle": "idle", "broken": "BROKEN", "waiting": "waiting for\ncheap power"}
PART_COLOR = {"ok": "#3b7ea1", "tight": "#d77a12", "late": "#c7343a"}


def record(rule_name):
    """Run the model (1-day warm-up first) and take a snapshot of the line every STEP minutes."""
    env = simpy.Environment()
    line = model.ProductionLine(env, rule=RULES[rule_name], seed=SEED)
    env.run(until=DAY)
    line.reset_stats()
    snapshots = []
    for t in range(DAY + START, DAY + END + 1, STEP):
        env.run(until=t)
        states, queues = [], []
        for m in line.machines:
            m.set_state(m.state)   # update the energy cost up to now
            # Part color = slack: time to due date minus the work still to do.
            slacks = [p.due_date - t - sum(p.proc_times[m.index:]) for p in m.queue]
            queues.append(["late" if s < 0 else "tight" if s < 60 else "ok" for s in slacks])
            waiting = m.state == "idle" and m.queue and rule_name.startswith("ECO") and line.expensive_now()
            states.append("waiting" if waiting else m.state)
        snapshots.append({"states": states, "queues": queues,
                          "cost": sum(m.energy_cost for m in line.machines), "done": len(line.finished)})
    return snapshots


def draw_line(ax, y, name, snap):
    """Draw one production line (3 queues + 3 machines) with its bottom edge at height y."""
    ax.text(1, y + 13, name, fontsize=13, weight="bold", va="center")
    ax.text(1, y + 8.5, f"finished: {snap['done']}", fontsize=9, color="#4d575c", va="center")
    ax.text(1, y + 5, f"energy cost: {snap['cost']:.0f} EUR", fontsize=9, color="#4d575c", va="center")
    for i in range(3):
        x = 18 + i * 27
        # Queue: one small square per waiting part (max 24 shown).
        ax.text(x, y + 17.5, f"queue: {len(snap['queues'][i])}", fontsize=8, color="#4d575c")
        for k, urgency in enumerate(snap["queues"][i][:24]):
            ax.add_patch(Rectangle((x + (k % 6) * 2.6, y + 13 - (k // 6) * 3.2), 2.1, 2.6, color=PART_COLOR[urgency]))
        # Machine: a colored box with its state.
        state = snap["states"][i]
        ax.add_patch(Rectangle((x + 16.5, y + 4), 7, 11, color=STATE_COLOR[state]))
        ax.text(x + 20, y + 9.5, f"M{i + 1}", color="white", fontsize=12, weight="bold", ha="center", va="center")
        ax.text(x + 20, y + 2.8, STATE_TEXT[state], fontsize=7.5, ha="center", va="top")
        ax.annotate("", (x + 16.2, y + 9.5), (x + 15.4, y + 9.5), arrowprops={"arrowstyle": "->", "color": "#7f898e"})


def make_gif(filename="line.gif"):
    data = {name: record(name) for name in SHOWN}
    fig, ax = plt.subplots(figsize=(9, 5.2))
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.99)

    def frame(n):
        ax.clear()
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 58)
        ax.axis("off")
        minute = START + n * STEP
        expensive = model.PEAK_START <= minute / 60 < model.PEAK_END
        price = model.PEAK_PRICE if expensive else model.OFF_PEAK_PRICE
        ax.add_patch(Rectangle((0, 51), 100, 7, color="#f4dfbd" if expensive else "#dbe3f1"))
        ax.text(2, 54.5, f"{minute // 60:02d}:{minute % 60:02d}", fontsize=16, weight="bold", va="center")
        ax.text(16, 54.5, f"Electricity {'EXPENSIVE (daytime)' if expensive else 'cheap (night)'}: "
                f"{price:.2f} EUR/kWh", fontsize=11, va="center")
        draw_line(ax, 27, SHOWN[0], data[SHOWN[0]][n])
        draw_line(ax, 4, SHOWN[1], data[SHOWN[1]][n])
        ax.text(0, 0.5, "Parts:  blue = on track   orange = tight   red = will be late        "
                "Machines:  green = working   grey = idle   purple = waiting for cheap power   red = broken",
                fontsize=7, color="#4d575c")

    frames = len(data[SHOWN[0]])
    FuncAnimation(fig, frame, frames=frames).save(filename, writer=PillowWriter(fps=10), dpi=100)
    print(f"Saved {filename} ({frames} frames)")


if __name__ == "__main__":
    make_gif()
