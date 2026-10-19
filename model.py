"""
model.py - Discrete-event simulation (DES) of a small production line, using SimPy.

The line has 3 machines in sequence:   arrivals -> M1 -> M2 -> M3 -> finished
Time unit: minutes.
"""
import random

import simpy

from rules import RULES

# ---------------- Model parameters ----------------
NUM_MACHINES = 3
MEAN_INTERARRIVAL = 10.0           # average minutes between two part arrivals
MEAN_PROC_TIME = [6.0, 8.0, 6.0]   # average processing minutes on M1, M2, M3 (M2 = bottleneck)
DUE_DATE_FACTOR = (2.0, 6.0)       # due date = arrival + factor * total work, factor ~ Uniform(2, 6)
MTBF = 8 * 60.0                    # Mean Time Between Failures: one breakdown every 8 hours on average
MTTR = 30.0                        # Mean Time To Repair: 30 minutes on average
# -> availability = MTBF / (MTBF + MTTR) = 480 / 510 = about 94%


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
    """A machine with its own waiting queue. It processes one part at a time and can break down."""

    def __init__(self, env, index, line, seed):
        self.env = env
        self.index = index               # 0, 1, 2
        self.name = f"M{index + 1}"
        self.line = line
        self.queue = []                  # parts waiting for this machine
        self.wake_up = None              # event that wakes the machine when a part arrives
        self.failure_rng = random.Random(seed * 100 + index)   # own random stream for failures
        self.num_failures = 0
        # State bookkeeping: the machine is always 'idle', 'busy' or 'broken'.
        self.state = "idle"
        self.state_since = 0.0
        self.time_in_state = {"idle": 0.0, "busy": 0.0, "broken": 0.0}
        self.process = env.process(self.run())   # the machine's life cycle
        env.process(self.breakdowns())           # a second process that causes failures

    def set_state(self, new_state):
        """Change state and add the time spent in the old state to the statistics."""
        self.time_in_state[self.state] += self.env.now - self.state_since
        self.state = new_state
        self.state_since = self.env.now

    def add_part(self, part):
        """Put a part in the queue and wake the machine if it is waiting."""
        self.queue.append(part)
        if self.wake_up is not None and not self.wake_up.triggered:
            self.wake_up.succeed()

    def run(self):
        """The machine's life cycle: wait for work -> pick a part -> process it -> pass it on.
        A failure can interrupt it at any moment (while idle or while busy)."""
        while True:
            # 1) If the queue is empty, sleep until add_part() wakes us up.
            while not self.queue:
                self.set_state("idle")
                self.wake_up = self.env.event()
                try:
                    yield self.wake_up
                except simpy.Interrupt as failure:     # broke down while idle
                    yield from self.repair(failure.cause)

            # 2) Choose the next part with the line's dispatching rule (FIFO, SPT or EDD).
            part = self.line.rule(self.queue, self)
            self.queue.remove(part)
            self.line.log(f"{part.id:>4} starts on {self.name}")

            # 3) Process the part. If a failure interrupts us, we keep the work already
            #    done, wait for the repair, and then continue with the remaining time.
            remaining = part.proc_times[self.index]
            while remaining > 0:
                self.set_state("busy")
                start = self.env.now
                try:
                    yield self.env.timeout(remaining)
                    remaining = 0
                except simpy.Interrupt as failure:     # broke down while busy
                    remaining -= self.env.now - start
                    yield from self.repair(failure.cause)

            # 4) Send the part to the next machine (or out of the line).
            self.line.part_done(part, self.index)

    def repair(self, duration):
        """The machine is broken for 'duration' minutes."""
        self.set_state("broken")
        self.line.log(f"     {self.name} BREAKS DOWN (repair takes {duration:.1f} min)")
        yield self.env.timeout(duration)
        self.line.log(f"     {self.name} repaired")

    def breakdowns(self):
        """Failure process: time to failure ~ Exponential(MTBF), repair time ~ Exponential(MTTR)."""
        while True:
            yield self.env.timeout(self.failure_rng.expovariate(1.0 / MTBF))
            repair_time = self.failure_rng.expovariate(1.0 / MTTR)
            self.num_failures += 1
            self.process.interrupt(cause=repair_time)  # stop the machine's run() process
            yield self.env.timeout(repair_time)        # no new failure during the repair


class ProductionLine:
    """Creates the machines and the part arrivals, and collects finished parts."""

    def __init__(self, env, rule=RULES["FIFO"], seed=1, verbose=False):
        self.env = env
        self.rule = rule                          # dispatching rule used by every machine
        self.verbose = verbose
        self.arrival_rng = random.Random(seed)   # own random stream for arrivals and part data
        self.machines = [Machine(env, i, self, seed) for i in range(NUM_MACHINES)]
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
    # Short demo: print the first hour event by event, then run one full week.
    env = simpy.Environment()
    ProductionLine(env, seed=42, verbose=True)
    env.run(until=60)

    # Same seed for every rule -> same parts and same failures (a fair comparison).
    sim_time = 7 * 24 * 60
    for name, rule in RULES.items():
        env = simpy.Environment()
        line = ProductionLine(env, rule=rule, seed=42)
        env.run(until=sim_time)

        done = line.finished
        lead_times = [p.finish_time - p.arrival_time for p in done]
        tardiness = [max(0.0, p.finish_time - p.due_date) for p in done]
        print(f"\n{name} - one week: {len(done)} parts finished")
        print(f"  Average lead time: {sum(lead_times) / len(done):.1f} min")
        print(f"  Average tardiness: {sum(tardiness) / len(done):.1f} min")
        print(f"  Tardy parts:       {sum(t > 0 for t in tardiness) / len(done):.0%}")
