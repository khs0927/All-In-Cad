import struct

import pytest
from pydantic import ValidationError

from all_in_cad.protocol import (
    MAX_FRAME_BYTES,
    FrameDecoder,
    NativeMethod,
    ProtocolError,
    RpcRequest,
    RpcResponse,
    decode_body,
    encode_frame,
)


def test_frame_round_trip_with_split_chunks() -> None:
    request = RpcRequest(method=NativeMethod.SYSTEM_PING, params={"hello": "world"})
    frame = encode_frame(request)
    decoder = FrameDecoder()
    messages: list[bytes] = []
    for chunk in (frame[:2], frame[2:7], frame[7:]):
        messages.extend(decoder.feed(chunk))
    assert len(messages) == 1
    decoded = RpcRequest.model_validate(decode_body(messages[0]))
    assert decoded.request_id == request.request_id
    assert decoded.params == {"hello": "world"}


def test_decoder_handles_back_to_back_frames() -> None:
    left = RpcRequest(method=NativeMethod.SYSTEM_PING)
    right = RpcRequest(method=NativeMethod.HOST_CONTEXT)
    decoder = FrameDecoder()
    messages = decoder.feed(encode_frame(left) + encode_frame(right))
    assert [decode_body(body)["method"] for body in messages] == [
        "system.ping",
        "host.context",
    ]


def test_decoder_rejects_oversized_and_zero_frames() -> None:
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError):
        decoder.feed(struct.pack("<I", MAX_FRAME_BYTES + 1))
    with pytest.raises(ProtocolError):
        decoder.feed(struct.pack("<I", 0))


def test_response_shape_is_strict() -> None:
    request = RpcRequest(method=NativeMethod.SYSTEM_PING)
    with pytest.raises(ValidationError):
        RpcResponse(request_id=request.request_id, ok=False)
