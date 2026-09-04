from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


def test_refresh_discards_late_success_and_closes_before_completion(monkeypatch):
    from app.services import login_service as login
    entered, release = threading.Event(), threading.Event()
    events, clients, committed = [], [], []
    class Client:
        def __init__(self):
            self.index = len(clients) + 1
            clients.append(self)
        def generate(self):
            events.append(f'generate:{self.index}')
            return SimpleNamespace(qr_url='https://account.bilibili.com/test')
        def poll(self, challenge):
            if self.index == 1:
                entered.set()
                assert release.wait(2)
            return SimpleNamespace(status=login.QrStatus.SUCCESS)
        def candidate_cookies(self):
            return self.index
        def close(self):
            events.append(f'close:{self.index}')
    def validate(candidate, *, cancelled):
        assert not cancelled()
        committed.append(candidate)
        return SimpleNamespace(code='verified', text='ok')
    monkeypatch.setattr(login, 'render_qr_png', lambda url: b'fake-png')
    monkeypatch.setattr(login, 'validate_and_commit_candidate_cookies', validate)
    notifications = []
    flow = login.LoginWorkflow(client_factory=Client, notify=lambda event, data: notifications.append((event, data)), total_timeout=2, poll_interval=0)
    thread = threading.Thread(target=lambda: (flow.run(), events.append('returned')))
    thread.start()
    assert entered.wait(1)
    assert flow.request_refresh() == 2
    assert flow.request_refresh() == 3
    release.set(); thread.join(2)
    assert not thread.is_alive()
    assert committed == [2]
    assert events.index('close:1') < events.index('generate:2')
    assert events.index('close:2') < events.index('returned')
    assert flow.terminal_outcome.code == 'success'
    assert notifications[-1][1]['code'] == 'verified'
    assert notifications[-1][1]['generation'] == 3


@pytest.mark.parametrize('action', ['cancel', 'timeout'])
def test_cancel_or_timeout_closes_session_without_committing(monkeypatch, action):
    from app.services import login_service as login
    closed = []
    flow = None
    class Client:
        def generate(self):
            return SimpleNamespace(qr_url='https://account.bilibili.com/test')
        def poll(self, challenge):
            if action == 'cancel':
                flow.request_cancel()
            return SimpleNamespace(status=login.QrStatus.WAITING_SCAN)
        def close(self):
            closed.append(True)
    monkeypatch.setattr(login, 'render_qr_png', lambda url: b'fake')
    monkeypatch.setattr(login, 'validate_and_commit_candidate_cookies', lambda *a, **k: pytest.fail('Must not commit'))
    flow = login.LoginWorkflow(client_factory=Client, total_timeout=0.03, poll_interval=0.01)
    assert flow.run().code == ('cancelled' if action == 'cancel' else 'timeout')
    assert closed == [True]


def test_settings_theme_and_directory_migrate_independently(monkeypatch, isolated_paths):
    import json
    from app.config import AppConfig, config_path, load_config, save_config
    from dataclasses import replace
    path = config_path()
    path.write_text(json.dumps({'schema_version': 1, 'download_dir': str(isolated_paths.root), 'theme': 'unsupported'}))
    settings = load_config()
    assert settings.download_dir == str(isolated_paths.root)
    assert settings.theme == 'system'
    save_config(replace(settings, theme='dark'))
    save_config(replace(load_config(), download_dir=str(isolated_paths.root / 'new')))
    assert load_config().theme == 'dark'
