from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_narration_plan_uses_local_cues_without_openai(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "openai_api_key", "")

    response = client.post(
        "/v1/narration-plan",
        json={"paragraphs": ["The door creaked open.", "Her friend smiled warmly."]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "moods": ["suspense", "warm"],
        "sentences": [["The door creaked open."], ["Her friend smiled warmly."]],
        "cues": [{"paragraph_index": 0, "sentence_index": 0, "effect": "door_creak"}],
        "source": "fallback",
    }


def test_narration_plan_uses_valid_ai_moods(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "openai_api_key", "test-key")

    class FakeResponses:
        async def create(self, **kwargs):
            assert kwargs["store"] is False
            return type(
                "Result",
                (),
                {"output_text": '{"moods":["suspense","warm"],"cues":['
                 '{"paragraph_index":0,"sentence_index":0,"effect":"door_creak"}]}'},
            )()

    class FakeOpenAI:
        responses = FakeResponses()

    monkeypatch.setattr(main, "AsyncOpenAI", lambda **kwargs: FakeOpenAI())
    response = client.post(
        "/v1/narration-plan",
        json={"paragraphs": ["The door creaked.", "She felt safe at home."]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "moods": ["suspense", "warm"],
        "sentences": [["The door creaked."], ["She felt safe at home."]],
        "cues": [{"paragraph_index": 0, "sentence_index": 0, "effect": "door_creak"}],
        "source": "ai",
    }


def test_speech_selects_configured_suspense_voice(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(main.settings, "elevenlabs_voice_id", "default-voice")
    monkeypatch.setattr(main.settings, "elevenlabs_suspense_voice_id", "suspense-voice")
    captured: dict[str, object] = {}

    class FakeResponse:
        content = b"audio"

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    response = client.post("/v1/speech", json={"text": "The door creaked.", "mood": "suspense"})

    assert response.status_code == 200
    assert response.content == b"audio"
    assert "suspense-voice" in str(captured["url"])
    assert captured["body"]["text"] == "The door creaked."
