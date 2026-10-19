"""
rules.py - Dispatching rules.

A dispatching rule decides which waiting part a free machine processes next.
Every rule has the same form:   rule(queue, machine) -> chosen part
The queue is a list of Part objects, kept in the order they joined the queue.
'machine' is the Machine that asks (rules can use machine.index and machine.env.now).
"""


def fifo(queue, machine):
    """First In, First Out: the part that has waited longest at this machine."""
    return queue[0]


def spt(queue, machine):
    """Shortest Processing Time: the part that is fastest to process on this machine.
    Finishes many parts quickly -> short queues and low average lead time."""
    return min(queue, key=lambda part: part.proc_times[machine.index])


def edd(queue, machine):
    """Earliest Due Date: the most urgent part (its due date is the soonest).
    Focuses on delivering on time -> low tardiness."""
    return min(queue, key=lambda part: part.due_date)


# All rules by name, so other files can loop over them.
RULES = {"FIFO": fifo, "SPT": spt, "EDD": edd}
