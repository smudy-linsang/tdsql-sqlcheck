# -*- coding: utf-8 -*-
"""CP-W03 crypto.py 测试：AES-GCM 封套往返、AAD 防置换、keyring 失败关闭（CP-TST-40/60）。"""
import base64
import json
import os

import pytest

from backend.services.copilot.crypto import (
    CryptoUnavailableError, Keyring, decrypt, encrypt, hmac_digest, load_keyring,
    reset_keyring_cache,
)


class TestKeyring:
    def test_load_and_roundtrip(self, keyring_file):
        kr = load_keyring(keyring_file)
        assert kr.active_kid == "key-test"
        env = encrypt("hello-密钥", "copilot_turns", "t1", "request_envelope",
                      owner="subj1")
        out = decrypt(env, "copilot_turns", "t1", "request_envelope", owner="subj1")
        assert out == "hello-密钥"

    def test_aad_cross_row_rejected(self, keyring_file):
        """AAD 跨行置换：用另一主键解密必须失败关闭。"""
        env = encrypt("data", "copilot_turns", "t1", "request_envelope",
                      owner="subj1")
        with pytest.raises(CryptoUnavailableError):
            decrypt(env, "copilot_turns", "t2", "request_envelope", owner="subj1")
        with pytest.raises(CryptoUnavailableError):
            decrypt(env, "copilot_turns", "t1", "response_envelope", owner="subj1")
        with pytest.raises(CryptoUnavailableError):
            decrypt(env, "copilot_turns", "t1", "request_envelope", owner="subj2")

    def test_missing_keyring_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COPILOT_KEYRING_FILE", str(tmp_path / "nope.json"))
        reset_keyring_cache()
        with pytest.raises(CryptoUnavailableError):
            load_keyring()

    def test_bad_key_length_rejected(self, tmp_path, monkeypatch):
        p = tmp_path / "k.json"
        p.write_text(json.dumps({
            "schema_version": 1, "active_kid": "k1",
            "keys": {"k1": base64.b64encode(b"short").decode()}}))
        monkeypatch.setenv("COPILOT_KEYRING_FILE", str(p))
        reset_keyring_cache()
        with pytest.raises(CryptoUnavailableError):
            load_keyring()

    def test_duplicate_json_keys_rejected(self, tmp_path, monkeypatch):
        key = base64.b64encode(os.urandom(32)).decode()
        p = tmp_path / "k.json"
        p.write_text(
            '{"schema_version":1,"active_kid":"k1","keys":{"k1":"%s","k1":"%s"}}'
            % (key, key))
        monkeypatch.setenv("COPILOT_KEYRING_FILE", str(p))
        reset_keyring_cache()
        with pytest.raises(CryptoUnavailableError):
            load_keyring()

    def test_hmac_purpose_separation(self, keyring_file):
        h1 = hmac_digest("copilot-input", "same-text")
        h2 = hmac_digest("other-purpose", "same-text")
        assert h1 != h2 and len(h1) == 64

    def test_rotation_old_kid_readable(self, tmp_path, monkeypatch):
        """轮换：旧 kid 保留期间旧记录可解，新写用新 kid。"""
        k1 = base64.b64encode(os.urandom(32)).decode()
        k2 = base64.b64encode(os.urandom(32)).decode()
        p = tmp_path / "k.json"
        p.write_text(json.dumps({"schema_version": 1, "active_kid": "k1",
                                 "keys": {"k1": k1}}))
        monkeypatch.setenv("COPILOT_KEYRING_FILE", str(p))
        reset_keyring_cache()
        env = encrypt("old-data", "t", "1", "f", owner="S")
        # 轮换到 k2（k1 保留）
        p.write_text(json.dumps({"schema_version": 1, "active_kid": "k2",
                                 "keys": {"k1": k1, "k2": k2}}))
        reset_keyring_cache()
        assert decrypt(env, "t", "1", "f", owner="S") == "old-data"
        env2 = encrypt("new-data", "t", "1", "f", owner="S")
        assert json.loads(env2)["kid"] == "k2"
