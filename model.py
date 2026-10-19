"""
model.py - Discrete-event simulation (DES) of a small production line, using SimPy.

The line has 3 machines in sequence:   arrivals -> M1 -> M2 -> M3 -> finished
Time unit: minutes.
"""
import random

import simpy

# ---------------- Model parameters ----------------
NUM_MACHINES = 3
MEAN_INTERARRIVAL = 10.0           # average minutes between two part arrivals
MEAN_PROC_TIME = [6.0, 8.0, 6.0]   # average processing minutes on M1, M2, M3 (M2 = bottleneck)
DUE_DATE_FACTOR = (2.0, 6.0)       # due date = arrival + factor * total work, factor ~ Uniform(2, 6)


class Part:
    """One part (job). Its random data is drawn once, when it arrives."""

    def __init__(self, part_id, arrival_time, rng):
        self.id = part_id
        self.arrival_time = arrival_time
        # Random processing time on each machine (exponential distribution).
        self.proc_times = [rng.expovariate(1.0 / mean) for mean in MEAN_PROC_TIME]
        # Due date: more total work -> more time allowed. The random factor makes
        # some orders urgent and others relaxed.
        factor = rng.uniform(*DUE_DATE_FACTOR)
        self.due_date = arrival_time + factor * sum(self.proc_times)
        self.finish_time = None


class Machine:
    """A machine with its own waiting queue. It processes one part at a time."""

    def __init__(self, env, index, line):
        self.env = env
        self.index = index               # 0, 1, 2
        self.name = f"M{index + 1}"
        self.line = line
        self.queue = []                  # parts waiting for this machine
        self.busy_time = 0.0             # total minutes spent processing
        self.wake_up = None              # event that wakes the machine when a part arrives
        env.process(self.run())          # start the machine's life-cycle process

    def add_part(self, part):
        """Put a part in the queue and wake the machine if it is waiting."""
        self.queue.append(part)
        if self.wake_up is not None and not self.wake_up.triggered:
            self.wake_up.succeed()

    def run(self):
        """The machine's life cycle: wait for work -> pick a part -> process it -> pass it on."""
        while True:
            # 1) If the queue is empty, sleep until add_part() wakes us up.
            while not self.queue:
                self.wake_up = self.env.event()
                yield self.wake_up

            # 2) Choose the next part. For now: first in, first out (FIFO).
            part = self.queue.pop(0)

            # 3) Process the part: the simulation clock jumps forward by the processing time.
            self.line.log(f"{part.id:>4} starts on {self.name}")
            yield self.env.timeout(part.proc_times[self.index])
            self.busy_time += part.proc_times[self.index]

            # 4) Send the part to the next machine (or out of the line).
            self.line.part_done(part, self.index)


class ProductionLine:
    """Creates the machines and the part arrivals, and collects finished parts."""

    def __init__(self, env, seed=1, verbose=False):
        self.env = env
        self.verbose = verbose
        self.arrival_rng = random.Random(seed)   # own random stream for arrivals and part data
        self.machines = [Machine(env, i, self) for i in range(NUM_MACHINES)]
        self.finished = []                        # parts that left the line
        env.process(self.arrivals())

    def log(self, message):
        if self.verbose:
            print(f"t={self.env.now:8.1f} | {message}")

    def arrivals(self):
        """Generate parts with exponential inter-arrival times (a Poisson arrival process)."""
        part_id = 0
        while True:
            yield self.env.timeout(self.arrival_rng.expovariate(1.0 / MEAN_INTERARRIVAL))
            part_id += 1
            part = Part(part_id, self.env.now, self.arrival_rng)
            self.log(f"{part.id:>4} arrives (due at {part.due_date:.1f})")
            self.machines[0].add_part(part)

    def part_done(self, part, machine_index):
        """Called when a machine finishes a part."""
        if machine_index + 1 < NUM_MACHINES:
            self.machines[machine_index + 1].add_part(part)
        else:
            part.finish_time = self.env.now
            self.finished.append(part)
            self.log(f"{part.id:>4} finished")


if __name__ == "__main__":
    # Short demo: print the first hour event by event, then run one full day.
    env = simpy.Environment()
    ProductionLine(env, seed=42, verbose=True)
    env.run(until=60)

    env = simpy.Environment()
    line = ProductionLine(env, seed=42)
    sim_time = 24 * 60
    env.run(until=sim_time)

    done = line.finished
    lead_times = [p.finish_time - p.arrival_time for p in done]
    tardiness = [max(0.0, p.finish_time - p.due_date) for p in done]
    print(f"\nOne day: {len(done)} parts finished")
    print(f"Average lead time: {sum(lead_times) / len(done):.1f} min")
    print(f"Average tardiness: {sum(tardiness) / len(done):.1f} min")
    for m in line.machines:
        print(f"{m.name} utilization: {m.busy_time / sim_time:.0%}, parts waiting at end: {len(m.queue)}")
