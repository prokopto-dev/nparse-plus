"""SignalR JSON hub protocol codec tests (offline)."""

import httpx
import pytest

from nparseplus.net import hubproto

# Verbatim from tools/pigparse_probe_transcript.md.
NEGOTIATE_BODY = {
    "negotiateVersion": 1,
    "connectionId": "qqaquivNcBwo9Ejs-IiWjA",
    "connectionToken": "z8iZEzKvk1SqhkjK_eEQmg",
    "availableTransports": [
        {"transport": "WebSockets", "transferFormats": ["Text", "Binary"]},
        {"transport": "ServerSentEvents", "transferFormats": ["Text"]},
        {"transport": "LongPolling", "transferFormats": ["Text", "Binary"]},
    ],
}


def test_encode_decode_round_trip() -> None:
    frame = hubproto.invocation_frame("JoinServerGroup", [0])
    assert frame.endswith(hubproto.RECORD_SEPARATOR)
    (decoded,) = hubproto.decode_frames(frame)
    assert decoded == {"type": 1, "target": "JoinServerGroup", "arguments": [0]}


def test_decode_multiple_frames_one_payload() -> None:
    raw = '{"type":6}\x1e{"type":1,"target":"PlayerLocationEvent","arguments":[{"name":"A"}]}\x1e'
    frames = hubproto.decode_frames(raw)
    assert [f.get("type") for f in frames] == [6, 1]


def test_decode_skips_malformed_and_non_dict_frames() -> None:
    raw = 'not json\x1e{"type":6}\x1e[1,2]\x1e'
    assert hubproto.decode_frames(raw) == [{"type": 6}]


def test_decode_accepts_bytes() -> None:
    assert hubproto.decode_frames(b'{"type":6}\x1e') == [{"type": 6}]


def test_handshake_frame_and_response_check() -> None:
    assert hubproto.HANDSHAKE_FRAME == '{"protocol":"json","version":1}\x1e'
    hubproto.check_handshake_response({})  # live server answers {}
    with pytest.raises(hubproto.HandshakeError):
        hubproto.check_handshake_response({"error": "nope"})


def test_websocket_url() -> None:
    assert (
        hubproto.websocket_url("https://www.pigparse.org/PP", "tok")
        == "wss://www.pigparse.org/PP?id=tok"
    )
    assert hubproto.websocket_url("http://localhost:5000/PP", "t") == "ws://localhost:5000/PP?id=t"


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_negotiate_returns_ws_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/PP/negotiate"
        assert request.url.params["negotiateVersion"] == "1"
        return httpx.Response(200, json=NEGOTIATE_BODY)

    url = hubproto.negotiate("https://www.pigparse.org/PP", http=_http(handler))
    assert url == "wss://www.pigparse.org/PP?id=z8iZEzKvk1SqhkjK_eEQmg"


def test_negotiate_rejects_missing_token_and_no_websockets() -> None:
    def no_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"negotiateVersion": 1})

    with pytest.raises(hubproto.HandshakeError):
        hubproto.negotiate("https://x/PP", http=_http(no_token))

    def no_ws(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "connectionToken": "t",
                "availableTransports": [{"transport": "LongPolling"}],
            },
        )

    with pytest.raises(hubproto.HandshakeError):
        hubproto.negotiate("https://x/PP", http=_http(no_ws))


def test_negotiate_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        hubproto.negotiate("https://x/PP", http=_http(handler))
