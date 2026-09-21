from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def raw_submission() -> dict[str, Any]:
    return {
        "code": "37WVUJ",
        "state": "confirmed",
        "title": "Годовая динамика почв",
        "abstract": "Полный текст тезисов.\nВторая строка.",
        "description": "дерново-подзолистые почвы, компьютерная томография",
        "submission_type": {"id": 20, "name": {"ru": "Устный доклад"}},
        "track": {
            "id": 31,
            "position": 5,
            "name": {"ru": "1.5. Физика и гидрология почв"},
        },
        "speakers": [
            {
                "code": "HYTFXX",
                "name": "Тимофеева Мария Валерьевна",
                "biography": "Почвенный институт им. В.В. Докучаева",
                "email": "speaker@example.org",
            }
        ],
        "answers": [
            {
                "question": {
                    "identifier": "CDHXWQFZ",
                    "question": {"ru": "Соавторы доклада и их аффилиации"},
                },
                "answer": (
                    "Абросимов К.Н., Почвенный институт; "
                    "Юдина А.В., МГУ, факультет почвоведения"
                ),
            }
        ],
    }
