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


# --------------------------------------------------------------------------
# httpx logs every request at INFO, and the Odds API only accepts its key as
# a query parameter -- so the key lands in the log file on every odds fetch.
# redact_api_key guarded exception strings only; httpx's own request logging
# went straight past it.
#
# Found in production on 2026-09-19: scheduler.log held
# `.../americanfootball_ncaaf/odds?apiKey=<the real key>`.
# --------------------------------------------------------------------------

import logging

from backend.collectors.odds_api import install_log_redaction


def _capture(caplog, logger_name, msg, *args):
    logging.getLogger(logger_name).info(msg, *args)
    return caplog.text


def test_an_api_key_logged_by_httpx_is_redacted(caplog):
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(
            caplog, "httpx",
            'HTTP Request: GET https://api.the-odds-api.com/v4/sports/'
            'baseball_mlb/odds?apiKey=SECRET123&regions=us "HTTP/1.1 200 OK"')
    assert "SECRET123" not in text
    assert "<redacted>" in text


def test_redaction_survives_lazy_percent_formatting(caplog):
    """httpx logs with %-args, so the key is not in record.msg at all."""
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(caplog, "httpx", "HTTP Request: %s %s",
                        "GET", "https://x/odds?apiKey=SECRET123")
    assert "SECRET123" not in text
    assert "<redacted>" in text


def test_the_rest_of_the_message_is_kept(caplog):
    """Redaction, not suppression -- the request line stays useful."""
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(caplog, "httpx",
                        "HTTP Request: GET https://api.the-odds-api.com/v4/"
                        "sports/baseball_mlb/odds?apiKey=SECRET123 200 OK")
    assert "baseball_mlb" in text and "200 OK" in text


def test_it_applies_to_any_logger_not_just_httpx(caplog):
    """The key reaches logs through whatever happens to carry a URL."""
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(caplog, "backend.pipeline.full_pipeline",
                        "fetch failed for ?apiKey=SECRET123")
    assert "SECRET123" not in text


def test_installing_twice_does_not_double_redact(caplog):
    install_log_redaction()
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(caplog, "httpx", "GET /odds?apiKey=SECRET123")
    assert text.count("<redacted>") == 1


def test_a_message_without_a_key_is_untouched(caplog):
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        text = _capture(caplog, "httpx", "HTTP Request: GET https://espn/x 200")
    assert "<redacted>" not in text
    assert "https://espn/x" in text


def test_it_works_against_a_real_stream_handler_not_just_caplog():
    """The production shape: basicConfig + a propagated httpx record.

    caplog installs its own handler, so passing under caplog does not prove
    the filter reaches the handler the scheduler actually writes through. A
    logger's own filters do NOT apply to records that merely propagate to it,
    so filtering the root logger alone would leave this leaking.
    """
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        install_log_redaction()
        logging.getLogger("httpx").info(
            "HTTP Request: %s %s", "GET",
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
            "?apiKey=SECRET123&regions=us")
        handler.flush()
        written = stream.getvalue()
    finally:
        root.handlers, root.level = old_handlers, old_level

    assert "SECRET123" not in written, "the key reached the log file"
    assert "<redacted>" in written
    assert "baseball_mlb" in written


def test_constructing_the_collector_installs_the_filter():
    """No entrypoint can forget it.

    The key only leaves the process through a client built in
    OddsAPICollector.__init__, so installing there is the one place
    guaranteed to run before httpx can log a URL containing it.
    """
    import io

    from backend.collectors.odds_api import OddsAPICollector

    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        OddsAPICollector("SECRET123")          # installs as a side effect
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://x/odds?apiKey=SECRET123 200")
        handler.flush()
        written = stream.getvalue()
    finally:
        root.handlers, root.level = old_handlers, old_level

    assert "SECRET123" not in written
