"""负载计划编译测试。"""

from evalplat.services.load_profile_service import build_schedule


def test_fixed_rps_schedule():
    schedule = build_schedule({"mode": "fixed_rps", "rps": 2}, 3)
    assert [item.offset_seconds for item in schedule] == [0.0, 0.5, 1.0]


def test_fixed_concurrency_schedule():
    schedule = build_schedule({"mode": "fixed_concurrency", "concurrency": 2}, 5)
    assert [item.offset_seconds for item in schedule] == [0.0, 0.0, 1.0, 1.0, 2.0]


def test_staircase_schedule_marks_stage():
    schedule = build_schedule({"mode": "staircase", "stages": [{"rps": 1, "duration_seconds": 2}, {"rps": 2, "duration_seconds": 2}]}, 5)
    assert [item.stage for item in schedule] == [0, 0, 1, 1, 1]
