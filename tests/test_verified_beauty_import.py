from scripts.import_verified_beauty_products import PRODUCTS, mapped_products


def test_verified_beauty_batch_is_valid_unique_and_canonical():
    records, rejected = mapped_products()
    assert rejected == []
    assert len(PRODUCTS) == len(records) == 9
    assert len({record.canonical_gtin for record in records}) == 9
    assert all(len(record.canonical_gtin) == 14 for record in records)
    assert all(record.source == "MANUAL_VERIFIED" for record in records)


def test_cnd_upc_and_ean_representations_deduplicate():
    records, _ = mapped_products()
    by_name = {record.name: record for record in records}
    assert by_name["CND Vinylux Weekly Nail Polish Crushed Rose 0.5 Fl Oz"].canonical_gtin == "00639370908014"
    assert by_name["CND Vinylux Weekly Nail Polish Mauve Maverick 0.5 Fl Oz"].canonical_gtin == "00639370909646"
