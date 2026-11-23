from ma import config


def load(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return config.load(path)


def test_defaults_have_no_personal_data(tmp_path):
    c = config.load(tmp_path / "missing.toml")
    assert (c.language, c.asr_language, c.me_name) == ("en", "en", "Me")


def test_language_drives_speech_and_my_name(tmp_path):
    c = load(tmp_path, 'language = "ru"\n')
    assert (c.language, c.asr_language, c.me_name) == ("ru", "ru", "Я")


def test_speech_language_is_separate(tmp_path):
    c = load(tmp_path, 'language = "en"\nme_name = "Alex"\n\n[asr]\nlanguage = "de"\n')
    assert (c.language, c.asr_language, c.me_name) == ("en", "de", "Alex")


def test_unknown_language_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_HOME", str(tmp_path))
    assert load(tmp_path, 'language = "xx"\n').language == "en"


def test_secure_home_makes_data_private(tmp_path, monkeypatch):
    import os
    from ma import paths
    root = tmp_path / "home"
    (root / "recordings" / "r1").mkdir(parents=True)
    (root / "voices.sqlite").write_text("x")
    (root / "recordings" / "r1" / "meta.json").write_text("{}")
    os.chmod(root, 0o755)
    os.chmod(root / "voices.sqlite", 0o644)
    monkeypatch.setenv("MA_HOME", str(root))
    old = os.umask(0o022)
    try:
        paths.secure_home()
        assert os.umask(0o077) == 0o077
    finally:
        os.umask(old)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "recordings" / "r1").stat().st_mode & 0o777 == 0o700
    assert (root / "voices.sqlite").stat().st_mode & 0o777 == 0o600
    assert (root / "recordings" / "r1" / "meta.json").stat().st_mode & 0o777 == 0o600
