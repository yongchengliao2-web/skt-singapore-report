import unittest

from tools.fetch_skt_voucher_cost import QUERY, build_payload


class VoucherDirectItemTests(unittest.TestCase):
    def test_query_uses_direct_seller_cost_without_net_weights(self):
        self.assertIn('SUM(IFNULL(seller_voucher_cost_sgd, 0)) * 0.55', QUERY)
        self.assertNotIn('net_voucher_cost', QUERY)
        self.assertNotIn('sg_skt_onsite_voucher_id_cost', QUERY)

    def test_preserves_item_amounts_without_rescaling(self):
        payload = build_payload([
            dict(date='2026-11-01', product_id='1', voucher_spend_sgd=16.5),
            dict(date='2026-11-01', product_id='2', voucher_spend_sgd=38.5),
        ])
        self.assertEqual([r[2] for r in payload['rows']], [16.5, 38.5])
        self.assertEqual(payload['metadata']['calculation_method'], 'direct_item_seller_cost_v1')

    def test_rejects_duplicate_item_days(self):
        row = dict(date='2026-11-01', product_id='1', voucher_spend_sgd=16.5)
        with self.assertRaises(RuntimeError):
            build_payload([row, row])


if __name__ == '__main__':
    unittest.main()
