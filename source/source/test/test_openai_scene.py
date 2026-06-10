from api.openai_scene import _fallback_scene_analysis, parse_scene_analysis_response


def test_parse_scene_analysis_response_accepts_fenced_json() -> None:
    raw_text = """```json
    {
      "summary": "Два человека в спокойном портретном кадре.",
      "people_count": 2,
      "people": [
        {
          "label": "человек слева",
          "position_in_frame": "слева",
          "role_in_scene": "основной участник",
          "apparent_age_group": "молодой взрослый",
          "apparent_gender_presentation": "маскулинная подача",
          "face_visibility": "лицо видно полностью",
          "facial_expression": "лёгкая улыбка",
          "clothing": "тёмное худи",
          "pose": "стоит прямо"
        }
      ],
      "background": "внутренняя стена помещения",
      "shot_type": "средний план",
      "main_action": "люди спокойно позируют",
      "mood": ["спокойное", "дружелюбное"],
      "relationships": ["друзья"]
    }
    ```"""

    analysis = parse_scene_analysis_response(raw_text)

    assert analysis.people_count == 2
    assert analysis.people[0].label == "человек слева"
    assert analysis.people[0].position_in_frame == "слева"
    assert analysis.relationships == ["друзья"]
    assert analysis.mood == ["спокойное", "дружелюбное"]


def test_fallback_scene_analysis_handles_non_json_text() -> None:
    raw_text = """
    На фотографии видно 4 человека на церемонии. Мужчина в центре надевает кольцо женщине.
    Настроение радостное и торжественное.
    """

    analysis = _fallback_scene_analysis(raw_text)

    assert analysis.people_count == 4
    assert "4 человека" in analysis.summary
