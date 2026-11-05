import pytest

from trader.kernel.broker.creds import key_fingerprint
from trader.kernel.broker.models import BrokerAccount
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets, account_prefix, load_secrets


def _acct(id_, env="real"):
    return BrokerAccount(id=id_, env=env, cano="00000000")


SECRETS = Secrets.model_validate({
    "KIS_REAL_APP_KEY": "shared-k", "KIS_REAL_APP_SECRET": "shared-s", "KIS_REAL_CANO": "11111111",
    "KIS_HTS_ID": "me", "KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2",
    "KIS_REAL_2_CANO": "22222222", "KIS_ISA_APP_KEY": "k3", "KIS_ISA_APP_SECRET": "s3",
})


def test_creds_prefer_account_keys_then_shared():
    main = SECRETS.kis_creds(_acct("real-main"))
    assert main.shared and main.app_key == "shared-k"
    assert main.bucket == "real-" + key_fingerprint("shared-k")
    two = SECRETS.kis_creds(_acct("real-2"))
    assert not two.shared and two.app_key == "k2" and two.cano == "22222222"
    assert two.hts_id == "me"
    same = Secrets.model_validate({"KIS_A_APP_KEY": "k2", "KIS_A_APP_SECRET": "s2"})
    assert same.kis_creds(_acct("a")).bucket == two.bucket != main.bucket
    assert SECRETS.kis_creds(env="paper").app_key is None


def test_account_prefix_rules():
    assert account_prefix("real-2") == "KIS_REAL_2_"
    assert account_prefix("isa-1", "KIS_ISA_") == "KIS_ISA_"
    assert account_prefix("--x.y--") == "KIS_X_Y_"


class _WithCreds:
    def __init__(self):
        self.id, self.env, self.credentials = "isa-1", "real", "KIS_ISA_"


def test_kis_creds_honours_credentials_attribute():
    isa = SECRETS.kis_creds(_WithCreds())
    assert isa.app_key == "k3" and isa.prefix == "KIS_ISA_"


def test_secrets_from_env_when_file_missing(project, monkeypatch):
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    assert load_secrets().KIS_PAPER_APP_KEY == "k"


def test_env_overrides_file(project, monkeypatch):
    env_file = project / "secrets" / ".env"
    env_file.write_text("DART_API_KEY=file\nWEB_TOKEN=file\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "env")
    monkeypatch.setenv("WEB_TOKEN", "")
    s = load_secrets()
    assert s.DART_API_KEY == "env" and s.WEB_TOKEN == "file"


def test_whitespace_env_does_not_override(project, monkeypatch):
    (project / "secrets" / ".env").write_text("DART_API_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "   ")
    assert load_secrets().DART_API_KEY == "file"


def test_cp949_env_is_config_error(project):
    (project / "secrets" / ".env").write_bytes("# 한글\nDART_API_KEY=x\n".encode("cp949"))
    with pytest.raises(ConfigError, match="UTF-8"):
        load_secrets()


def test_env_var_overrides_account_key(project, monkeypatch):
    (project / "secrets" / ".env").write_text("KIS_REAL_2_APP_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("KIS_REAL_2_APP_KEY", "env")
    assert load_secrets().get("KIS_REAL_2_APP_KEY") == "env"


def test_get_returns_none_for_blank_and_unknown():
    s = Secrets.model_validate({"DART_API_KEY": "  ", "KIS_X_APP_KEY": "v"})
    assert s.get("DART_API_KEY") is None and s.get("KIS_X_APP_KEY") == "v" and s.get("NOPE") is None


def test_ensure_web_token_creates_once(project):
    from trader.kernel.config.secrets import ensure_web_token

    p = project / "secrets" / ".env"
    p.write_text("# 주석\nDART_API_KEY=x\n", encoding="utf-8")
    t1, made1 = ensure_web_token()  # 경로 생략 → paths.secrets_path()
    t2, made2 = ensure_web_token(p)
    assert made1 and not made2 and t1 == t2 and len(t1) >= 24
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# 주석\nDART_API_KEY=x\n") and f"WEB_TOKEN={t1}" in text
    assert load_secrets(p).WEB_TOKEN == t1


def test_ensure_web_token_prefers_env(project, monkeypatch):
    from trader.kernel.config.secrets import ensure_web_token

    monkeypatch.setenv("WEB_TOKEN", "from-env")
    assert ensure_web_token() == ("from-env", False)
    assert not (project / "secrets" / ".env").exists()
