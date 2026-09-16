from backend.collectors.odds_api import redact_api_key


def test_redacts_apikey_param():
    text = "GET https://api.the-odds-api.com/v4/sports/x/odds?apiKey=FAKEKEY123&regions=us"
    result = redact_api_key(text)
    assert "apiKey=<redacted>&regions=us" in result
    assert "FAKEKEY123" not in result


def test_redacts_case_insensitive():
    text = "url?apikey=FAKEKEY123&regions=us"
    result = redact_api_key(text)
    assert "FAKEKEY123" not in result
    assert "<redacted>" in result


def test_leaves_text_without_apikey_unchanged():
    text = "Client error '429 Too Many Requests' for url 'https://example.com/foo?regions=us'"
    assert redact_api_key(text) == text
