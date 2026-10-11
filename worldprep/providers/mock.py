"""離線 mock：不呼叫任何外部服務，用東京範例內容跑完整流程，驗證剪輯、品牌與狀態機。"""
import re
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from .base import ImageResult, ResearchResult

CLAIMS = [
    ("geography", "東京位於關東平原，面向東京灣。"),
    ("history", "1603 年德川家康在江戶開設幕府。"),
    ("history", "1868 年明治維新後，江戶改名為東京並成為首都。"),
    ("history", "1923 年關東大地震重創東京。"),
    ("city", "東京的鐵道網絡以山手線環狀線串起主要副都心。"),
    ("business", "東京是許多日本大型企業總部的所在地。"),
    ("culture", "江戶時代的町人文化孕育了浮世繪與歌舞伎。"),
    ("attractions", "東京鐵塔於 1958 年完工。"),
]

SCRIPT = [
    ("hook", "開場", ["如果我告訴你，今天的東京，其實是從一場幾乎摧毀整座城市的災難之後重新長出來的，你會相信嗎？"]),
    ("outline", "這集要看的三件事", ["要回答這個問題，我們分三步來看。", "第一，江戶怎麼變成東京。", "第二，大地震之後的重建。",
                                     "最後，今天你看到的東京。"]),
    ("geography", "一片平原與一個海灣", ["東京站在關東平原上，面對東京灣。平坦的土地讓城市可以一直往外長，海灣則把它和整個世界連在一起。"]),
    ("history", "從江戶到東京", ["一六〇三年，德川家康在江戶開設幕府，這個小漁村開始變成權力中心。", "一八六八年明治維新之後，江戶改名東京，正式成為日本的首都。"]),
    ("city", "一座被鐵道定義的城市", ["一九二三年的關東大地震幾乎讓東京歸零，重建時，鐵道成了城市的骨架。山手線把新宿、澀谷、池袋這些副都心串在一起。"]),
    ("business", "東京怎麼賺錢", ["權力和人口集中，企業就跟著來。今天，許多日本大型企業的總部都在這裡，東京也因此成為整個國家的經濟心臟。"]),
    ("culture", "町人留下的東西", ["江戶時代的商人與工匠，創造了浮世繪與歌舞伎。這種為大眾而生的文化，到今天仍然流在東京的動漫與街頭。"]),
    ("attractions", "你看到的東京", ["理解了戰後重建，你再看一九五八年完工的東京鐵塔，就會發現它不只是一個地標，而是一個時代重新站起來的象徵。"]),
    ("closing", "結語", ["東京不是一天造成的，而是一次又一次重建出來的。先看懂世界，再出發。"]),
]


class MockLLM:
    def research(self, task: str, system: str, prompt: str, episode_id: int | None = None) -> ResearchResult:
        notes = "\n".join(f"- {c}（來源：Mock 官方資料 https://example.gov/tokyo 2025）" for _, c in CLAIMS)
        return ResearchResult(text=notes, sources=[{"url": "https://example.gov/tokyo", "title": "Mock 官方資料", "page_age": ""}])

    def text(self, task, system, prompt, episode_id=None, effort=None) -> str:
        return "mock"

    def json(self, task: str, system: str, prompt: str, schema: dict, episode_id: int | None = None, effort: str | None = None,
             cached_prefix: str | None = None):
        prompt = (cached_prefix + "\n\n" if cached_prefix else "") + prompt
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", prompt, re.M)]
        if task == "topic":
            return {"choice_index": 0, "angle": "東京為什麼能成為世界之都？", "reason": "mock"}
        if task == "research_extract":
            return {"claims": [{"claim": c, "section": sec, "importance": "key", "source": "Mock 官方資料",
                                "source_url": "https://example.gov/tokyo", "source_type": "government",
                                "source_date": "2025", "confidence": 0.9} for sec, c in CLAIMS]}
        if task == "factcheck_extract":
            claims = dict(re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M))
            return {"results": [{"id": i, "verdict": "verified", "final_claim": claims.get(str(i), ""), "source": "Mock 官方資料",
                                 "source_url": "https://example.gov/tokyo", "source_date": "2025", "confidence": 0.9, "note": ""}
                                for i in ids]}
        if task in ("script", "script_revise"):
            return {"thesis": "東京是一座在災難與重建中不斷重新定義自己的城市。",
                    "outline_points": ["從江戶到東京", "地震後的重建", "今天的東京"],
                    "sections": [{"section": sec, "heading": h, "causal_link": "",
                                  "outline_point": {"hook": 0, "outline": 0, "geography": 1, "history": 1, "city": 2,
                                                    "business": 2}.get(sec, 3),
                                  "paragraphs": [{"text": t, "claim_ids": ids[:1]} for t in paras]} for sec, h, paras in SCRIPT]}
        if task == "script_review":
            return {"pass": True, "coherent": True, "follows_structure": True, "hook_strong": True,
                    "natural_taiwanese_chinese": True, "sounds_ai_generated": False, "unsupported_sentences": [],
                    "issues": [], "unexplained_terms": [], "boring_passages": [], "title_supported": False, "title_fix": "東京為什麼能成為世界之都？從江戶到今天的城市野心",
                    "score": 8.5}
        if task == "storyboard":
            sids = re.findall(r"^(s\d{3}) ", prompt, re.M)
            cycle = ["slide", "stock_video", "chart", "slide"]
            out = []
            for i, sid in enumerate(sids):
                vt = cycle[i % len(cycle)]
                out.append({"scene_id": sid, "visual_type": vt, "visual_description": f"東京畫面 {i}",
                            "shot_type": ["people", "object", "map", "daily_life", "landmark"][i % 5],
                            "slide_headline": f"東京第{i + 1}幕", "slide_number": "",
                            "search_query": "Tokyo skyline", "ai_prompt": "", "realistic": False,
                            "camera_motion": ["zoom_in", "pan_left", "zoom_out", "pan_right"][i % 4], "transition": "fade",
                            "on_screen_text": "1868 明治維新" if i == 2 else "", "map_required": vt == "map",
                            "chart_required": vt == "chart", "map_place": "東京", "map_caption": "關東平原・東京灣",
                            "chart": {"title": "東京發展里程碑", "unit": "年", "kind": "bar", "labels": ["江戶幕府", "明治維新", "東京鐵塔"],
                                      "values": [1603, 1868, 1958], "source": "Mock 官方資料", "claim_id": ids[0] if ids else 0}})
            return {"scenes": out}
        if task == "topic_request":
            titles = [{"main_title": "一座被災難摧毀兩次的城市，為什麼還能成為世界之都？", "archetype": "B"},
                      {"main_title": "東京旅遊攻略", "archetype": "C"},
                      {"main_title": "為什麼東京的鐵道，長成一個圓圈？", "archetype": "F"}]
            if "titles" in schema["properties"] and len(schema["properties"]) == 1:
                return {"titles": titles}
            return {"destination": "東京", "region": "亞洲城市", "familiar_phenomenon": "山手線為什麼是一個圓",
                    "archetype": "B", "core_question": "東京為什麼能成為世界之都？", "titles": titles,
                    "narrative_arc": "災難 → 重建 → 鐵道骨架 → 企業集中 → 今天的東京", "opening_15s": "先講關東大地震後的東京幾乎歸零",
                    "research_questions": ["德川幕府為何選江戶？"], "wow_details_to_verify": ["東京鐵塔 1958 年完工"],
                    "script_direction": "每段都回到重建", "visual_direction": "東京鐵塔、山手線"}
        if task == "en_script":
            def block(name):
                m = re.search(rf"## {name}\n(.*?)(?:\n\n##|\Z)", prompt, re.S)
                return m.group(1).splitlines() if m else []
            scenes = [re.match(r"(s\d{3}) \[\w+\] ", ln) for ln in block("Scenes")]
            return {"destination": "Tokyo", "titles": ["Why did Tokyo rise twice?", "Tokyo after the fire", "The city that rebuilt itself"],
                    "scenes": [{"scene_id": m.group(1), "text": f"Tokyo scene {i} tells part of the story."} for i, m in enumerate(scenes) if m],
                    "headings": [{"section": ln.split(":")[0], "heading": ln.split(":")[0].title()} for ln in block("Sections")],
                    "slide_headlines": [{"scene_id": ln.split(":")[0], "headline": "Tokyo"} for ln in block("Slide headlines")],
                    "charts": [{"scene_id": ln.split(":")[0], "title": "Milestones", "unit": "year", "labels": ["A", "B", "C"]}
                               for ln in block("Charts")]}
        if task == "en_meta":
            return {"description_intro": "Why did Tokyo become a world capital?", "tags": ["Tokyo", "Japan"], "hashtags": ["#BeyondTravel"],
                    "pinned_comment": "Which era of Tokyo would you visit?", "shorts_title": "Tokyo rose twice #Shorts",
                    "shorts_description": "Full story: [YouTube video link] #BeyondTravel", "instagram_caption": "Tokyo was destroyed twice.",
                    "threads_post": "Did you know Tokyo was destroyed twice? [YouTube video link]"}
        if task == "social":
            return {"shorts_title": "東京被摧毀兩次，為什麼還是世界之都？ #Shorts",
                    "shorts_description": "完整故事在這裡：【YouTube 正片連結】 #世界先修課 #東京 #城市",
                    "instagram_caption": "東京其實被摧毀過兩次。\n完整影片連結在個人檔案\n#世界先修課 #東京",
                    "threads_post": "你知道東京被毀過兩次嗎？完整版：【YouTube 正片連結】"}
        if task == "metadata":
            return {"description_intro": "東京為什麼能成為世界之都？這集從江戶幕府、明治維新、關東大地震一路看到今天的東京。",
                    "tags": ["東京", "Tokyo", "日本歷史", "城市發展", "世界先修課"], "keywords": ["東京"],
                    "hashtags": ["#世界先修課", "#東京", "#城市紀錄片"], "pinned_comment": "你覺得東京最像哪個時代？"}
        if task == "thumbnail":
            return {"country": "日本", "city": "東京", "title_lines": ["為什麼東京", "能成為世界之都？"], "gold_keywords": ["世界之都"],
                    "subtitle": "從江戶到未來", "core_question": "東京為什麼能成為世界之都", "direction": "綜合",
                    "landmarks": ["東京鐵塔", "富士山"], "mood": "宏偉", "scene": "Tokyo Tower at dusk with Mount Fuji"}
        if task == "topic_options":
            return {"options": [{"main_title": f"香港選項{i}", "angle": "mock", "why_click": "mock", "archetype": "A"} for i in (1, 2, 3)]}
        if task == "reply_parse":
            return {"option_number": 2, "custom_topic": "", "custom_is_full_title": False, "next_city": "首爾"}
        if task == "title_judge":
            picks = re.findall(r"^- \[([A-G])\] (.+)$", prompt, re.M)[:3]
            return {"picks": [{"main_title": t, "archetype": a, "reason": "mock"} for a, t in picks]}
        raise KeyError(f"mock has no fixture for task {task}")


class MockImages:
    name = "mock"

    def __init__(self, source: str = "https://pixabay.com/photos/mock", license: str = "Pixabay Content License"):
        self.source, self.license = source, license

    def get(self, query: str, out: Path, episode_id: int | None = None) -> ImageResult:
        out = out.with_suffix(".jpg")
        h = uuid.uuid5(uuid.NAMESPACE_URL, str(out)).int
        img = Image.new("RGB", (2400, 1350), (40 + h % 80, 60 + h % 60, 90 + h % 90))
        d = ImageDraw.Draw(img)
        for i in range(0, 2400, 160):
            d.rectangle([i, 900 - (h >> (i % 17)) % 500, i + 120, 1350], fill=(20, 30, 50))
        img.save(out, quality=90)
        return ImageResult(path=out, source=f"{self.source}-{h % 10000}", creator="Mock Creator",
                           license=self.license, license_url="https://example.com/license", usage_rights="mock")


class MockSlides:
    name = "mock_slides"

    def generate_many(self, jobs: dict, episode_id: int) -> dict:
        out = {}
        for k, (prompt, path) in jobs.items():
            r = MockImages(source="gemini:mock", license="Generated").get(prompt, path, episode_id)
            r.ai_generated = r.realistic = True
            out[k] = r
        return out

    def get(self, prompt: str, out: Path, episode_id: int | None = None, reserve: float = 0.0) -> ImageResult:
        return self.generate_many({"x": (prompt, out)}, episode_id)["x"]

    def compose(self, prompt: str, references: list, out: Path, episode_id: int | None = None, model=None, price=None,
                reserve: float = 0.0) -> ImageResult:
        return self.get(prompt, out, episode_id)


class MockVideos:
    name = "mock_video"

    def get(self, query: str, out: Path, episode_id: int | None = None, min_seconds: float = 5) -> ImageResult:
        from ..render.ffmpeg import run_ffmpeg

        out = out.with_suffix(".mp4")
        run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25:duration=4", "-pix_fmt", "yuv420p", str(out)])
        return ImageResult(path=out, source="https://pixabay.com/videos/mock", creator="Mock Videographer",
                           license="Pixabay Content License", license_url="https://example.com/license",
                           usage_rights="mock", media_type="video")
