import unittest
from decimal import Decimal

from tools.fetch_skt_voucher_cost import allocate_daily_cost


class VoucherBudgetAllocationTests(unittest.TestCase):
    def records(self, weights, total='55.01'):
        return [dict(date='2026-11-01', product_id=str(i + 1),
                     daily_voucher_cost_sgd=total, voucher_distribution_weight=w)
                for i, w in enumerate(weights)]

    def test_item_distribution_reconciles_daily_total_after_rounding(self):
        result = allocate_daily_cost(self.records([1, 1, 1]))
        self.assertEqual(sum(Decimal(str(row['voucher_spend_sgd'])) for row in result), Decimal('55.01'))

    def test_uses_relative_weights_instead_of_scaling_item_costs(self):
        result = allocate_daily_cost(self.records([30, 70], '55'))
        self.assertEqual([row['voucher_spend_sgd'] for row in result], [16.5, 38.5])

    def test_missing_item_weights_preserves_cost_as_unallocated(self):
        result = allocate_daily_cost(self.records([0, 0]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['product_id'], '0')
        self.assertEqual(result[0]['voucher_spend_sgd'], 55.01)

    def test_rejects_inconsistent_daily_totals(self):
        records = self.records([1, 1])
        records[1]['daily_voucher_cost_sgd'] = '60'
        with self.assertRaises(ValueError):
            allocate_daily_cost(records)


if __name__ == '__main__':
    unittest.main()
