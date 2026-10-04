import base64, json, os, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from f1stream import Feed


def z(obj):
    c = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return base64.b64encode(c.compress(json.dumps(obj).encode()) + c.flush()).decode()


def test_feed_builds_rows_cars_and_messages():
    f = Feed()
    f.apply("DriverList", {"1": {"Tla": "VER", "FullName": "Max V", "TeamName": "RB", "TeamColour": "3671C6"}, "4": {"Tla": "NOR"}})
    f.apply("TimingData", {"Lines": {"1": {"Position": "2", "GapToLeader": "+1.5", "IntervalToPositionAhead": {"Value": "+1.5"},
            "LastLapTime": {"Value": "1:35.250"}, "NumberOfLaps": 3}, "4": {"Position": "1", "GapToLeader": ""}}})
    f.apply("TimingData", {"Lines": {"1": {"BestLapTime": {"Value": "1:34.000"}}}})  # partial update merges
    f.apply("TimingAppData", {"Lines": {"1": {"Stints": [{"Compound": "SOFT", "TotalLaps": 3}]}}})
    f.apply("RaceControlMessages", {"Messages": [{"Flag": "GREEN", "Category": "Flag", "Message": "GREEN LIGHT"}]})
    f.apply("Position.z", z({"Position": [{"Entries": {"1": {"Status": "OnTrack", "X": 10, "Y": 20, "Z": 0}}}]}))
    r = f.rows()
    assert [x["code"] for x in r] == [None if False else "NOR", "VER"]
    assert r[1]["gap"] == 1.5 and r[1]["last"] == 95.25 and r[1]["best"] == 94.0 and r[1]["tyre"] == "SOFT"
    assert f.cars == {"1": [10, 20]} and f.messages()[0]["flag"] == "GREEN" and f.lap_rows()[1][0]["lap_number"] == 3