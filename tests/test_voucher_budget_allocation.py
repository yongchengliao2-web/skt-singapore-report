import unittest
from tools.fetch_skt_voucher_cost import QUERY, build_payload

class VoucherIdAllocationTests(unittest.TestCase):
    def test_source_is_voucher_id_with_same_daily_rounding_as_budget(self):
        self.assertIn('sg_skt_onsite_voucher_id_cost', QUERY)
        self.assertIn('ROUND(SUM(IFNULL(seller_voucher_cost, 0)) * 0.55, 2)', QUERY)

    def test_allocation_reconciles_to_authoritative_total(self):
        payload = build_payload([
            dict(date='2026-11-01', product_id=str(i), item_weight=1, daily_total_sgd=100)
            for i in (1, 2, 3)
        ])
        self.assertEqual([r[2] for r in payload['rows']], [33.333333, 33.333333, 33.333334])
        self.assertEqual(payload['metadata']['calculation_method'], 'voucher_id_daily_allocated_v1')

    def test_missing_weights_preserve_total_as_unallocated(self):
        payload = build_payload([dict(date='2026-11-01', product_id='12', item_weight=0, daily_total_sgd=45.67)])
        self.assertEqual(payload['rows'], [['2026-11-01', '0', 45.67]])

    def test_rejects_duplicate_item_days(self):
        row = dict(date='2026-11-01', product_id='1', item_weight=1, daily_total_sgd=10)
        with self.assertRaises(RuntimeError):
            build_payload([row, row])

    def test_rejects_conflicting_daily_totals(self):
        with self.assertRaises(RuntimeError):
            build_payload([dict(date='2026-11-01', product_id=str(i), item_weight=1, daily_total_sgd=i) for i in (1, 2)])

if __name__ == '__main__':
    unittest.main()
