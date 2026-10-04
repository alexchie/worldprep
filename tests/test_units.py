import pytest

from worldprep.agents.subtitles import build_srt, phrases
from worldprep.brand import format_title, validate_title
from worldprep.states import IllegalTransition, State as S, check_transition


def test_title_format():
    t = format_title("為什麼東京能成為世界之都？", 1)
    assert t == "為什麼東京能成為世界之都？｜世界先修課 EP.01"
    assert validate_title(t, 1) == []
    assert format_title(t, 1) == t
    assert validate_title(t, 2)
    assert validate_title(format_title("東京旅遊攻略", 3), 3)
    assert validate_title("東京 EP1", 1)


def test_state_machine():
    check_transition(S.IDEA, S.RESEARCHING)
    check_transition(S.QA, S.READY_FOR_DELIVERY)
    check_transition(S.NOTIFIED, S.REGENERATING)
    check_transition(S.FAILED, S.RENDERING)
    for bad in [(S.QA, S.DELIVERED), (S.RENDERING, S.NOTIFIED), (S.NOTIFIED, S.DELIVERED), (S.IDEA, S.QA)]:
        with pytest.raises(IllegalTransition):
            check_transition(*bad)


def test_subtitles():
    for p in phrases("其實是從一場幾乎摧毀整座城市的災難之後重新長出來的，你會相信嗎？"):
        assert 0 < len(p) <= 18
    srt = build_srt([{"start": 3.0, "duration": 4.0, "text": "一六〇三年，德川家康在江戶開設幕府。"}])
    assert srt.startswith("1\n00:00:03,000 --> ")


def test_topic_reply_parsing_and_sender_check():
    import email

    from worldprep.agents.inbox import authenticated_sender, extract_topic

    body = "京都：為什麼能活過千年\n\n2026年10月5日 週一 08:00 cyy89997@gmail.com 寫道：\n> 世界先修課 EP.01 已完成"
    assert extract_topic(body) == "京都：為什麼能活過千年"
    assert extract_topic("Dubai please\r\n\r\nOn Mon, Oct 5, 2026 at 8:00 AM x wrote:\r\n> old") == "Dubai please"

    good = email.message_from_string(
        "From: Owner <beyondtravelwithus@gmail.com>\nAuthentication-Results: mx.google.com; "
        "dkim=pass header.i=@gmail.com; spf=pass; dmarc=pass (p=NONE) header.from=gmail.com\n\nbody")
    spoof = email.message_from_string(
        "From: beyondtravelwithus@gmail.com\nAuthentication-Results: mx.google.com; dkim=fail; dmarc=fail\n\nbody")
    other = email.message_from_string("From: someone@example.com\nAuthentication-Results: dmarc=pass\n\nbody")
    assert authenticated_sender(good, "beyondtravelwithus@gmail.com")
    assert not authenticated_sender(spoof, "beyondtravelwithus@gmail.com")
    assert not authenticated_sender(other, "beyondtravelwithus@gmail.com")
