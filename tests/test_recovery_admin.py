from datetime import datetime, timezone

from app.models import FallbackProviderState, ProductRecovery
from app.recovery import enqueue
from app.recovery_admin import recovery_dashboard
from tests.test_admin import create_user, auth_headers


def test_recovery_requires_admin(unauthenticated_client, session_factory):
    client=unauthenticated_client
    assert client.get('/v1/admin/recovery').status_code == 401
    create_user(session_factory,email='member-recovery@example.com',name='Member')
    headers=auth_headers(client,'member-recovery@example.com')
    assert client.get('/v1/admin/recovery',headers=headers).status_code == 403


def test_recovery_dashboard_filters_and_diagnostics(unauthenticated_client, session_factory):
    now=datetime.now(timezone.utc)
    with session_factory() as s:
        enqueue(s,['4006381333931','3017620422003'],now)
        job=s.get(ProductRecovery,'04006381333931'); job.status='retry';job.attempts=1;job.detail='not_found'
        other=s.get(ProductRecovery,'03017620422003');other.status='recovered'
        s.add(FallbackProviderState(canonical_gtin=job.canonical_gtin,provider='EANDB',status='unavailable',detail='access_denied',checked_at=now,expires_at=now))
        s.commit()
    create_user(session_factory,email='recovery-admin@example.com',name='Admin',is_admin=True)
    headers=auth_headers(unauthenticated_client,'recovery-admin@example.com')
    response=unauthenticated_client.get('/v1/admin/recovery?status=retry&limit=1',headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control']=='no-store'
    data=response.json()
    assert data['total']==1 and data['counts']['recovered']==1
    assert 'access denied' in data['items'][0]['reason']
    assert data['items'][0]['checks'][0]['detail']=='access_denied'
    assert data['items'][0]['next_attempt'].endswith('Z')
    assert 'lease_token' not in data['items'][0]
    assert data['provider_summary'][0]['products']==1
    assert unauthenticated_client.get('/v1/admin/recovery?status=bad',headers=headers).status_code==422
    assert unauthenticated_client.get('/v1/admin/recovery?limit=101',headers=headers).status_code==422
    page=unauthenticated_client.get('/v1/admin/recovery?status=retry&offset=1',headers=headers).json()
    assert page['items']==[] and page['total']==1


def test_recovery_empty(session_factory):
    with session_factory() as s:
        data=recovery_dashboard(s)
        assert data.items==[] and data.total==0 and data.provider_summary==[]
