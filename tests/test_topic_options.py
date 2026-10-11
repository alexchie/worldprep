from datetime import datetime, timezone

from sqlalchemy import select

from worldprep.agents import inbox, notify
from worldprep.db import init_db, session
from worldprep.models import TopicOption, TopicRequest
from worldprep.pipeline import prepare_topic_options
from worldprep.providers import get_providers


def test_reply_picks_option_and_next_city_gets_options():
    init_db()
    p = get_providers(mock=True)
    # 上一封信給過香港的 3 個選項
    with session() as s:
        s.add(TopicOption(city="香港", options=[{"main_title": f"香港標題{i}", "angle": f"故事{i}", "why_click": "x", "archetype": "A"}
                                              for i in (1, 2, 3)], shown_at=datetime.now(timezone.utc)))
    topic, city = inbox.resolve(p, "選 2，後天想做首爾")
    assert topic == "香港：故事2\n指定標題：香港標題2" and city == "首爾"

    with session() as s:
        s.add(TopicRequest(message_id="m1", text=topic, received_at=datetime.now(timezone.utc)))
        s.add(TopicOption(message_id="m1", city=city))
        s.add(TopicOption(message_id="m2", city=city))  # 同一個城市又回了一次：只想一次選項
    prepare_topic_options(p)
    with session() as s:
        assert len([o for o in s.scalars(select(TopicOption).where(TopicOption.city == "首爾")) if o.options]) == 1
    html, text = notify.topic_choice()
    assert "首爾" in text and "香港選項1" in text and "主題選擇" in html
    with session() as s:
        assert all(o.shown_at for o in s.scalars(select(TopicOption).where(TopicOption.city == "首爾")))
    # 選項只出現一次；之後沒有新城市時改成提醒
    assert "還沒有收到後天想做的城市" in notify.topic_choice()[1]
