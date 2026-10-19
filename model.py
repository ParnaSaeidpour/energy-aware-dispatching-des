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

# Energy: electric power of a machine in each state (kW).
POWER_KW = {"busy": 15.0, "idle": 5.0, "broken": 1.0}
# Time-of-use electricity price (EUR per kWh): expensive in the daytime, cheap at night.
PEAK_START, PEAK_END = 8, 20       # daytime = 08:00 to 20:00
PEAK_PRICE, OFF_PEAK_PRICE = 0.30, 0.10
RECHECK = 5.0                      # minutes before a waiting machine looks at its queue again


def electricity_price(t):
    """Electricity price (EUR/kWh) at simulation time t (minutes). The simulation starts at midnight."""
    hour = (t / 60) % 24
    return PEAK_PRICE if PEAK_START <= hour < PEAK_END else OFF_PEAK_PRICE


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
        self.energy_kwh = 0.0
        self.energy_cost = 0.0
        self.process = env.process(self.run())   # the machine's life cycle
        env.process(self.breakdowns())           # a second process that causes failures

    def set_state(self, new_state):
        """Change state. The time and energy of the old state are added to the statistics.
        (The price is constant inside one period, because ProductionLine.tariff_changes()
        also calls this method at every price change, like a meter reading.)"""
        duration = self.env.now - self.state_since
        kwh = POWER_KW[self.state] * duration / 60
        self.time_in_state[self.state] += duration
        self.energy_kwh += kwh
        self.energy_cost += kwh * electricity_price(self.state_since)
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
            # 1) Choose the next part with the line's dispatching rule. The rule may also
            #    return None = "wait" (the energy-aware ECO rule does this in expensive hours).
            part = None
            while part is None:
                if self.queue:
                    part = self.line.rule(self.queue, self)
                if part is None:
                    # Stay idle until add_part() (or a price change) wakes us up. If the rule
                    # said "wait", also look again after RECHECK minutes (a part may become urgent).
                    self.set_state("idle")
                    self.wake_up = self.env.event()
                    wait = self.wake_up | self.env.timeout(RECHECK) if self.queue else self.wake_up
                    try:
                        yield wait
                    except simpy.Interrupt as failure:     # broke down while idle
                        yield from self.repair(failure.cause)
            self.queue.remove(part)
            self.line.log(f"{part.id:>4} starts on {self.name}")

            # 2) Process the part. If a failure interrupts us, we keep the work already
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

            # 3) Send the part to the next machine (or out of the line).
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
        env.process(self.tariff_changes())

    def expensive_now(self):
        return electricity_price(self.env.now) == PEAK_PRICE

    def tariff_changes(self):
        """At every price change: close the energy periods of all machines (meter reading)
        and wake up machines that are waiting, so they can react to the new price."""
        while True:
            hour = (self.env.now / 60) % 24
            if hour < PEAK_START:
                next_change = PEAK_START
            elif hour < PEAK_END:
                next_change = PEAK_END
            else:
                next_change = PEAK_START + 24
            yield self.env.timeout((next_change - hour) * 60)
            for m in self.machines:
                m.set_state(m.state)
                if m.wake_up is not None and not m.wake_up.triggered:
                    m.wake_up.succeed()

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
        for m in line.machines:
            m.set_state(m.state)   # close the last open period
        print(f"  Energy: {sum(m.energy_kwh for m in line.machines):.0f} kWh, "
              f"cost {sum(m.energy_cost for m in line.machines):.2f} EUR")
