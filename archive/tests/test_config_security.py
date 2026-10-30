import pytest

from evalplat.config import Settings


_VALID_KEY = "S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0="


def _settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg://service:random-password@db/evalplat",
        "app_master_key": _VALID_KEY,
        "app_env": "production",
        "sandbox_require_immutable_image": True,
        "sandbox_host_dir": "/srv/evalplat/sandboxes",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_accept_external_credentials():
    assert _settings().app_env == "production"


def test_sandbox_host_dir_must_be_absolute_in_all_environments():
    with pytest.raises(ValueError, match="SANDBOX_HOST_DIR"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://service:random-password@db/evalplat",
            app_master_key=_VALID_KEY,
            app_env="development",
            sandbox_host_dir="./data/sandboxes",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "postgresql+psycopg://evalplat:evalplat@db/evalplat"},
        {"sandbox_require_immutable_image": False},
        {"app_master_key": "replace-with-generated-fernet-key"},
    ],
)
def test_production_settings_reject_insecure_defaults(overrides):
    with pytest.raises(ValueError):
        _settings(**overrides)
