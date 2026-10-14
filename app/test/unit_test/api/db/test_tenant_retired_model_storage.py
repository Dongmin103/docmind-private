"""Retained historical tenant column defaults, using the actual ORM and SQL."""
import pytest
from peewee import SqliteDatabase

from api.db.db_models import Tenant


@pytest.fixture
def tenant_db():
    db = SqliteDatabase(':memory:')
    with db.bind_ctx([Tenant]):
        db.connect()
        db.create_tables([Tenant])
        yield db
        db.close()


def _payload(identity):
    return dict(id=identity, name='Synthetic identity', llm_id='', embd_id='', img2txt_id='', rerank_id='', parser_ids='')


def test_fresh_tenant_insert_supplies_inert_asr_value_without_database_default(tenant_db):
    column = next(row for row in tenant_db.execute_sql('PRAGMA table_info(tenant)').fetchall() if row[1] == 'asr_id')
    assert column[3] == 1  # Existing databases require NOT NULL.
    assert column[4] is None  # The old database has no SQL column default.
    tenant = Tenant(**_payload('fresh'))
    assert tenant.save(force_insert=True) == 1
    assert Tenant.get_by_id('fresh').asr_id == ''


def test_orm_default_does_not_modify_historical_stored_value(tenant_db):
    Tenant.create(**_payload('existing'), asr_id='historical-retired-model')
    Tenant(**_payload('fresh')).save(force_insert=True)
    Tenant.update(name='Renamed').where(Tenant.id == 'existing').execute()
    assert Tenant.get_by_id('existing').asr_id == 'historical-retired-model'
    assert Tenant.get_by_id('fresh').asr_id == ''
