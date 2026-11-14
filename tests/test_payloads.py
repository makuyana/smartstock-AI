from app.webhook.parse import parse_webhook

WA_TEXT = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "27110001111",
                            "phone_number_id": "123456789",
                        },
                        "contacts": [{"profile": {"name": "Thabo"}, "wa_id": "27820001111"}],
                        "messages": [
                            {
                                "from": "27820001111",
                                "id": "wamid.HBgL",
                                "timestamp": "1763000000",
                                "type": "text",
                                "text": {"body": "sold 12 chicken kota 8 russian"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


def test_parse_whatsapp_text():
    events = parse_webhook(WA_TEXT)
    assert len(events) == 1
    event = events[0]
    assert event.channel == "whatsapp"
    assert event.external_id == "wamid.HBgL"
    assert event.sender_id == "27820001111"
    assert event.sender_name == "Thabo"
    assert event.type == "text"
    assert event.text == "sold 12 chicken kota 8 russian"


def test_parse_whatsapp_image_and_voice():
    image = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "27820001111", "profile": {"name": "Thabo"}}],
                            "messages": [
                                {
                                    "from": "27820001111",
                                    "id": "wamid.img",
                                    "timestamp": "1763000001",
                                    "type": "image",
                                    "image": {
                                        "id": "media-1",
                                        "mime_type": "image/jpeg",
                                        "caption": "invoice from boxer",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    audio = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "27820001111",
                                    "id": "wamid.aud",
                                    "timestamp": "1763000002",
                                    "type": "audio",
                                    "audio": {"id": "aud-1", "mime_type": "audio/ogg; codecs=opus"},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }
    image_event = parse_webhook(image)[0]
    assert image_event.type == "image"
    assert image_event.media_id == "media-1"
    assert image_event.caption == "invoice from boxer"
    audio_event = parse_webhook(audio)[0]
    assert audio_event.type == "audio"
    assert audio_event.media_id == "aud-1"
    assert audio_event.text is None


def test_whatsapp_status_is_ignored():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.x",
                                    "status": "delivered",
                                    "timestamp": "1763000000",
                                    "recipient_id": "27820001111",
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }
    assert parse_webhook(payload) == []


def test_parse_instagram_text_and_image():
    text = {
        "object": "instagram",
        "entry": [
            {
                "id": "987654321",
                "messaging": [
                    {
                        "sender": {"id": "ig-user-1"},
                        "recipient": {"id": "987654321"},
                        "timestamp": 1763000000000,
                        "message": {"mid": "mid.1", "text": "bought 20 bread R180"},
                    }
                ],
            }
        ],
    }
    event = parse_webhook(text)[0]
    assert event.channel == "instagram"
    assert event.sender_id == "ig-user-1"
    assert event.external_id == "mid.1"
    assert event.text == "bought 20 bread R180"
    assert event.sent_at.year >= 2025

    image = {
        "object": "instagram",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "ig-user-1"},
                        "timestamp": 1763000000000,
                        "message": {
                            "mid": "mid.2",
                            "attachments": [
                                {"type": "image", "payload": {"url": "https://example.com/invoice.jpg"}}
                            ],
                        },
                    }
                ]
            }
        ],
    }
    image_event = parse_webhook(image)[0]
    assert image_event.type == "image"
    assert image_event.media_url == "https://example.com/invoice.jpg"


def test_instagram_page_object_and_echo_skipped():
    payload = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "ig-user-1"},
                        "timestamp": 1763000000,
                        "message": {"mid": "mid.echo", "text": "hi", "is_echo": True},
                    },
                    {
                        "sender": {"id": "ig-user-1"},
                        "timestamp": 1763000001,
                        "message": {"mid": "mid.real", "text": "sold 1 kota"},
                    },
                ]
            }
        ],
    }
    events = parse_webhook(payload)
    assert len(events) == 1
    assert events[0].channel == "instagram"
    assert events[0].external_id == "mid.real"


def test_duplicate_ids_in_one_payload_collapse():
    payload = WA_TEXT
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "27820001111",
                                    "id": "wamid.same",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": "sold 1 kota"},
                                },
                                {
                                    "from": "27820001111",
                                    "id": "wamid.same",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": "sold 1 kota"},
                                },
                            ]
                        }
                    }
                ]
            }
        ],
    }
    assert len(parse_webhook(payload)) == 1
