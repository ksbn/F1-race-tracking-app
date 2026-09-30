import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main


def test_rows_sorted_with_best_lap():
    main.state["drivers"] = {1: {"name_acronym": "VER", "team_colour": "3671C6"}, 4: {"name_acronym": "NOR"}}
    main._raw.update(pos={1: 2, 4: 1}, gap={4: {"gap_to_leader": None}}, stints={},
                     laps={1: [{"lap_number": 1, "lap_duration": 90.5}, {"lap_number": 2, "lap_duration": 89.9}]})
    rows = main.build_rows()
    assert [r["code"] for r in rows] == ["NOR", "VER"]
    assert rows[1]["best"] == 89.9 and rows[1]["last"] == 89.9