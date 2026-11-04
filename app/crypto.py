"""Protocol-compatible cryptography ported from lib/utils/encrypt.dart."""

import base64
import hashlib
import json
import time
import uuid
from urllib.parse import quote

from Crypto.Cipher import AES, DES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

WEB_KEY = "u2oh6Vu^HWe4_AES"
APP_KEY = "z4ok6lu^oWp4_AES"
RSA_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC79d8Ot0hCbxxSISC6x8SCwTBspFSzlLKHJUYqoFNu1TSRaw4hEYkOnvEaL1VyoxV6HXcDrzwYvaFZaZaPQPFnfCHZy5dQwxcmifgSHqS+oKXw40Ys4cVIqnU5d90S7EWSRdBglX489jlqVaNcQSkDx2TYmC+DbAq9FV/BU09ISQIDAQAB"
CAPTCHA_ID = "Qt9FIw9o4pwRjOyqM6yizZBh682qN2TU"


def dumps(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def ms():
    return int(time.time() * 1000)


def md5(value: str | bytes):
    return hashlib.md5(value.encode() if isinstance(value, str) else value).hexdigest()


def aes(text, key, cbc=False):
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv=key.encode()) if cbc else AES.new(key.encode(), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(text.encode(), 16))).decode()


def rsa_encrypt(text):
    key = RSA.import_key(base64.b64decode(RSA_KEY))
    cipher = PKCS1_v1_5.new(key)
    data = text.encode()
    size = key.size_in_bytes() - 11
    return base64.b64encode(b"".join(cipher.encrypt(data[i : i + size]) for i in range(0, len(data), size))).decode()


def rsa_public_decrypt(text):
    key = RSA.import_key(base64.b64decode(RSA_KEY))
    data = base64.b64decode(text)
    size = key.size_in_bytes()
    chunks = []
    for i in range(0, len(data), size):
        raw = pow(int.from_bytes(data[i : i + size], "big"), key.e, key.n).to_bytes(size, "big")
        if raw[:2] not in (b"\x00\x01", b"\x00\x02"):
            raise ValueError("Invalid RSA padding")
        chunks.append(raw[raw.index(b"\x00", 2) + 1 :])
    return b"".join(chunks).decode()


def des_decrypt(text, key="SL2(M/eD"):
    return unpad(DES.new(key.encode(), DES.MODE_ECB).decrypt(bytes.fromhex(text)), 8).decode()


def enc_params(params):
    params = {**params, "_c_0_": uuid.uuid4().hex, "token": "4faa8662c59590c6f43ae9fe5b002b42", "_time": str(ms())}
    safe = "~!*'()"
    query = "&".join(f"{quote(str(k), safe=safe)}={quote(str(v), safe=safe)}" for k, v in params.items())
    return {**params, "inf_enc": md5(query + "&DESKey=Z(AfY@XS")}


def device_code():
    return aes(str(uuid.uuid4()), "QrCbNY@MuK1X8HGw")


def signed(data, secret):
    return md5("".join(f"{k}{data[k]}" for k in sorted(data)) + secret)


def file_crc(data):
    sample = data[:524288] + data[-524288:] if len(data) > 1048576 else data
    return md5(sample + format(len(data), "x").encode())
