"""A cold workspace cache builds once, not once per waiting thread.

waitress runs 16 threads against one process, so a burst on a cold cache used
to start 16 simultaneous builds of the same source.
"""

import threading
import time

from skills import workspace_cache as wcache


def _read_concurrently(entry, n=8):
    at_the_door = threading.Barrier(n)
    results, errors = [], []

    def read():
        at_the_door.wait(5)          # everyone arrives cold together
        try:
            results.append(entry.get()[0])
        except Exception as exc:     # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors
    return results


def test_a_cold_cache_builds_once_for_every_thread_waiting_on_it():
    calls = []

    def build():
        calls.append(1)
        time.sleep(0.2)              # hold the build open so the rest pile up
        return ["row"]

    entry = wcache._Entry("test-cold", ttl=60, build=build)
    results = _read_concurrently(entry)

    assert len(calls) == 1, f"built {len(calls)} times, expected 1"
    assert results == [["row"]] * 8


def test_a_warm_at_boot_does_not_double_build_with_a_first_request():
    calls = []

    def build():
        calls.append(1)
        time.sleep(0.2)
        return ["row"]

    entry = wcache._Entry("test-warm", ttl=60, build=build)
    warmer = threading.Thread(target=entry.warm)
    warmer.start()
    time.sleep(0.02)                 # a request lands mid-warm
    value, _ = entry.get()
    warmer.join(10)

    assert value == ["row"]
    assert len(calls) == 1, f"built {len(calls)} times, expected 1"
