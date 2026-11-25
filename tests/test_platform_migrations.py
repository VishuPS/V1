from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,inspect
from app.config import get_settings


def test_expansion_migrations_preserve_existing_private_products(tmp_path,monkeypatch):
    database=tmp_path/'preservation.db';url='sqlite:///'+database.as_posix()
    monkeypatch.setenv('DATABASE_URL',url);get_settings.cache_clear()
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.upgrade(config,'20261124_0023')
    engine=create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id,email,display_name,active,is_admin,created_at,updated_at) VALUES ('owner','owner@example.invalid','Owner',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO vault_products (id,user_id,identity_key,barcode,barcode_type,name,sku,brand,category,collection,notes,image_url,currency,custom_fields,revision,created_at,updated_at) VALUES ('product','owner','identity','000123','code128','Existing item','OLD-1','','Tools','Existing','Keep notes','','EUR','{}',7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    command.upgrade(config,'head')
    with engine.connect() as c:
        row=c.execute(text("SELECT barcode,name,notes,revision,supplier,tags FROM vault_products WHERE id='product'")).one()
        assert tuple(row)==('000123','Existing item','Keep notes',7,'','[]')
        assert c.execute(text("SELECT workspace_policy FROM users WHERE id='owner'")).scalar()=='legacy'
    assert {'inventory_items','stock_movements','asset_records','asset_events','workspace_receipts','funnel_events'}<=set(inspect(engine).get_table_names())
    command.downgrade(config,'20261124_0023')
    with engine.connect() as c:
        assert tuple(c.execute(text("SELECT barcode,name,notes,revision FROM vault_products WHERE id='product'")).one())==('000123','Existing item','Keep notes',7)
    engine.dispose();get_settings.cache_clear()
