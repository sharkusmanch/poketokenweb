import pytest

from poketokenweb import heartbeat


# --- clamp_interval --------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (120, 120),
    (30, 30),
    (3600, 3600),
    (0, heartbeat.MIN_INTERVAL),      # would spin the poll loop with no sleep
    (-5, heartbeat.MIN_INTERVAL),
    (99999, heartbeat.MAX_INTERVAL),
    ("300", 300),
    ("abc", heartbeat.DEFAULT_INTERVAL),
    (None, heartbeat.DEFAULT_INTERVAL),
])
def test_clamp_interval(raw, expected):
    assert heartbeat.clamp_interval(raw) == expected


# --- stale_after -----------------------------------------------------------

def test_stale_after_tracks_the_effective_interval_not_the_ceiling():
    # An earlier version used 3 * MAX_INTERVAL (3 hours), which at the default
    # 120s interval tolerated 90 missed polls.
    assert heartbeat.stale_after(600) == 3000.0
    assert heartbeat.stale_after(600) < 3 * heartbeat.MAX_INTERVAL


def test_stale_after_has_a_floor_for_short_intervals():
    # At 30s, 5 intervals is 150s -- too tight for a cold scan on a slow disk.
    assert heartbeat.stale_after(30) == 600.0


def test_stale_after_is_monotonic_in_the_interval():
    assert heartbeat.stale_after(120) <= heartbeat.stale_after(600)


# --- write / age -----------------------------------------------------------

def test_age_is_none_before_any_heartbeat(tmp_path):
    assert heartbeat.age(tmp_path / "heartbeat") is None


def test_write_then_age(tmp_path):
    path = tmp_path / "state" / "heartbeat"
    heartbeat.write(path, now=1000.0, succeeded=True)
    assert heartbeat.age(path, now=1060.0) == 60.0
    assert heartbeat.success_age(path, now=1060.0) == 60.0


def test_a_pre_json_heartbeat_still_reads(tmp_path):
    # Upgrade in place: an old bare-float file must not read as "never started".
    path = tmp_path / "heartbeat"
    path.write_text("1000.0")
    assert heartbeat.age(path, now=1060.0) == 60.0
    assert heartbeat.success_age(path, now=1060.0) is None


def test_a_failed_beat_carries_the_previous_success_forward(tmp_path):
    path = tmp_path / "heartbeat"
    heartbeat.write(path, now=1000.0, succeeded=True)
    heartbeat.write(path, now=1200.0, succeeded=False)
    assert heartbeat.age(path, now=1200.0) == 0.0
    assert heartbeat.success_age(path, now=1200.0) == 200.0


def test_success_age_is_none_when_no_poll_ever_succeeded(tmp_path):
    path = tmp_path / "heartbeat"
    heartbeat.write(path, now=1000.0, succeeded=False)
    assert heartbeat.age(path, now=1000.0) == 0.0
    assert heartbeat.success_age(path, now=1000.0) is None


def test_write_creates_the_parent_directory(tmp_path):
    path = tmp_path / "deep" / "nested" / "heartbeat"
    heartbeat.write(path, now=1.0)
    assert path.is_file()


def test_write_is_atomic_leaving_no_temp_file(tmp_path):
    path = tmp_path / "heartbeat"
    heartbeat.write(path, now=1.0)
    assert [p.name for p in tmp_path.iterdir()] == ["heartbeat"]


def test_concurrent_writes_never_leave_a_torn_file(tmp_path):
    # A FIXED temp name lets two writers interleave into one file and rename a
    # torn result into place -- the exact defect found in the config writer.
    import threading as _t
    path = tmp_path / "heartbeat"
    errors: list = []

    def beat(n: int) -> None:
        try:
            for i in range(40):
                heartbeat.write(path, now=float(n * 1000 + i), succeeded=bool(n % 2))
                assert heartbeat.age(path, now=0.0) is not None
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [_t.Thread(target=beat, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert heartbeat.age(path, now=0.0) is not None
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_never_raises_on_an_unwritable_path(tmp_path):
    # A heartbeat we cannot write must not kill the poll loop.
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    heartbeat.write(blocked / "heartbeat", now=1.0)   # must not raise


def test_age_of_a_corrupt_heartbeat_is_none(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("not a float")
    assert heartbeat.age(path) is None


# --- healthy ---------------------------------------------------------------

def test_healthy_during_boot_with_no_heartbeat_yet():
    assert heartbeat.healthy(None, 120, boot_age=10.0) is True


def test_UNHEALTHY_when_the_daemon_never_published_and_boot_grace_expired():
    # THE regression this module exists for: a daemon that dies before its
    # first poll must eventually fail the probe, not stay green forever.
    assert heartbeat.healthy(None, 120, boot_age=heartbeat.BOOT_GRACE_SECONDS + 1) is False


def test_healthy_with_a_fresh_publish():
    assert heartbeat.healthy(5.0, 120, boot_age=10_000.0, publish_age=5.0) is True


def test_UNHEALTHY_when_beating_but_never_publishing_past_boot_grace():
    """THE second regression: a daemon whose every poll raises keeps beating.

    Judged on the beat alone it looked healthy forever while /api/state answered
    503 and the app served nothing at all.
    """
    assert heartbeat.healthy(
        0.0, 120, boot_age=heartbeat.BOOT_GRACE_SECONDS + 1, publish_age=None
    ) is False


def test_healthy_while_still_booting_even_with_no_publish_yet():
    assert heartbeat.healthy(0.0, 120, boot_age=10.0, publish_age=None) is True


def test_UNHEALTHY_when_publishes_have_stopped_though_polls_continue():
    stale = heartbeat.stale_after(120) + 1
    assert heartbeat.healthy(0.0, 120, boot_age=10_000.0, publish_age=stale) is False


def test_unhealthy_once_the_publish_is_stale():
    stale = heartbeat.stale_after(120) + 1
    assert heartbeat.healthy(stale, 120, boot_age=10_000.0, publish_age=stale) is False


def test_the_staleness_bound_follows_the_configured_interval():
    # 700s of silence is fine at a 600s interval, dead at a 120s one.
    assert heartbeat.healthy(700.0, 600, boot_age=10_000.0, publish_age=700.0) is True
    assert heartbeat.healthy(700.0, 120, boot_age=10_000.0, publish_age=700.0) is False
