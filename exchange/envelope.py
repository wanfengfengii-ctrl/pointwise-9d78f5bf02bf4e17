from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class V1Envelope:
    version: int
    body: str
    digest: str


def _body_digest(body: str) -> str:
    if not isinstance(body, str) or not body:
        raise ValueError('V1 envelope requires body text')
    return sha256(body.encode('utf-8')).hexdigest()


def seal_v1(body: str) -> V1Envelope:
    return V1Envelope(1, body, _body_digest(body))


def verify_v1(envelope: V1Envelope) -> str:
    if not isinstance(envelope, V1Envelope) or type(envelope.version) is not int or envelope.version != 1:
        raise ValueError('unsupported packet envelope version')
    if not isinstance(envelope.digest, str) or envelope.digest != _body_digest(envelope.body):
        raise ValueError('packet body does not match its V1 digest')
    return envelope.body
